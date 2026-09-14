"""Live PASS-authenticated requests.Session tracking, and cached agenda fetches."""
import datetime
import json
import time

import requests
from flask import session

import db as dbmod
import pass_schedule as ps
from lesson_utils import (cache_lessons, is_ue_project, normalized_words, organism_ue_code, ue_name_distance,
                          ue_name_from_projet, ue_names_match, ue_search_term, ue_short_code)

# token -> live authenticated requests.Session — an in-memory hot cache in front of the
# `live_sessions` DB table (see current_pass_session()), so a Flask reload/restart doesn't
# force every logged-in user (there can be several, one token each) to log in again.
LIVE_SESSIONS: dict[str, requests.Session] = {}


def week_fetched(username: str, week_ref: datetime.date | None = None) -> bool:
    """Has this week ever been pulled from PASS? Drives the "not loaded yet" prompt on
    /lessons — an empty week that was fetched and an empty week that was never fetched
    look identical in lessons_cache, and only the second one is worth a button."""
    db = dbmod.get_db()
    week_start, _ = ps.week_bounds(week_ref)
    return db.execute(
        "SELECT 1 FROM events_cache_meta WHERE owner_username=? AND week_start=?",
        (username, week_start),
    ).fetchone() is not None


def fetch_events_cached(s: requests.Session, username: str, force: bool = False,
                        week_ref: datetime.date | None = None) -> list:
    """Serves the local cache; ONLY `force=True` — the "Actualiser depuis PASS" button —
    ever talks to PASS. A live fetch means an SSO-session check, the aspxtoasp bridge and
    one or two agenda round-trips, several seconds in practice, so browsing from week to
    week must never trigger one: the student decides when to pay that cost, and the cache
    is authoritative until they do.

    Backed by the DB (events_cache_meta + lessons_cache), not an in-memory dict —
    lessons_cache gets written on every live fetch, so a cache hit here just re-reads
    that table, and the cache is shared across uwsgi's worker processes instead of
    fragmented one-per-process.

    The single exception is the first ever load of the *current* week, which fetches by
    itself so a fresh login lands on a filled page rather than an empty one with a button.
    """
    db = dbmod.get_db()
    # Each browsable week gets its own marker: a fetched current week must not make a
    # never-fetched next week look loaded (and vice versa).
    week_start, week_end = ps.week_bounds(week_ref)
    already_fetched = db.execute(
        "SELECT 1 FROM events_cache_meta WHERE owner_username=? AND week_start=?",
        (username, week_start),
    ).fetchone() is not None
    bootstrap = not already_fetched and week_start == ps.week_bounds()[0]
    if not force and not bootstrap:
        return [
            {"id": r["lesson_id"], "date": r["date"], "time": r["time"], "title": r["title"],
             "details": json.loads(r["details_json"]), "teachers": json.loads(r["teachers_json"])}
            for r in db.execute(
                "SELECT lesson_id, date, time, title, details_json, teachers_json FROM lessons_cache "
                "WHERE owner_username=? AND date BETWEEN ? AND ? ORDER BY date, time",
                (username, week_start, week_end),
            )
        ]

    # A dead session doesn't reliably surface as an error from the calls below: PASS's
    # menu API and the classic-ASP agenda page can both return 200 with no structural
    # mismatch, just zero events, so parse_events() silently returns [] instead of
    # raising — the page would render as an empty "no lessons" week with no way to
    # tell that from a real light week. check_session_alive() gives an actual yes/no.
    ps.check_session_alive(s)
    agenda_link = ps.get_agenda_link(s, username=username)
    agenda_url = ps.bridge_to_classic_asp(s, agenda_link, username=username)
    # The Tableau view answers with a month; everything downstream (the /lessons page,
    # the PDF's "Semaine du ... au ..." header, lessons_cache) is weekly.
    events = ps.fetch_week_events(s, agenda_url, week_ref, username=username)

    if events:
        cache_lessons(db, username, events)
    # A week the student explicitly asked for is marked fetched even when it came back
    # empty — that's a real answer ("no courses"), and without the marker /lessons would
    # keep offering to load it. An empty *bootstrap* fetch stays unmarked so it retries
    # next request: right after login the first PASS call can race the server's own
    # session materialization and come back spuriously empty, and pinning that would show
    # "no courses" for a week that has some.
    if events or force:
        db.execute(
            "INSERT INTO events_cache_meta (owner_username, week_start, fetched_at, date_min, date_max) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(owner_username, week_start) DO UPDATE SET fetched_at=excluded.fetched_at, "
            "date_min=excluded.date_min, date_max=excluded.date_max",
            (username, week_start, time.time(), week_start, week_end),
        )
        db.commit()
    return events


def suggest_ue_codes(s: requests.Session, username: str, formation: str = "") -> list:
    """One {"name", "code", "alternatives"} per UE among the student's cached lessons, for
    « Remplir depuis PASS ». For one lesson per course (title before " - "):
      1. its PASS popup gives the UE name (« Projets ») and sometimes the code itself in
         « Organismes » ("UETAF-CCU-B") — taken as is;
      2. otherwise the UE catalogues are searched, those of the student's formation and TAF
         first, then all others ("Projet S9" = P3AS9 only exists in FISE24's), keeping only
         entries whose name really matches the UE (ue_names_match). Several distinct codes
         -> first one plus "alternatives"; none -> code "".
    Several dozen requests: only ever run on the student's explicit click."""
    db = dbmod.get_db()
    samples = {}
    for row in db.execute("SELECT lesson_id, date, title FROM lessons_cache WHERE owner_username=? "
                          "ORDER BY date DESC", (username,)):
        # One lesson per UE, decided before any request: "Transition Ecologique et Sociétale B"
        # and "… R" are the same UE, and school events ("Activités rentrée") have no code.
        name = ue_name_from_projet(row["title"].split(" - ")[0])
        if is_ue_project(name):
            samples.setdefault(" ".join(normalized_words(name)), row)
    if not samples:
        return []

    agenda_url = ps.bridge_to_classic_asp(s, ps.get_agenda_link(s, username=username), username=username)
    # Only NomCal is needed here: try the agenda page as PASS serves it first — forcing the
    # Tableau view (what fetch_week_events needs) re-renders a whole month, ~20 s on PASS.
    m = (ps.AGENDA_NOMCAL_RE.search(ps.fetch_agenda_html(s, agenda_url, username=username, view=None))
         or ps.AGENDA_NOMCAL_RE.search(ps.fetch_agenda_html(s, agenda_url, username=username)))
    if not m:
        raise LookupError("Identifiant d'agenda (NomCal) introuvable sur la page agenda PASS.")
    nom_cal = m.group(1)

    catalogues = [(cid, label) for cid, label in ps.list_ue_catalogues(s, username)
                  if normalized_words(label)[:1] == ["ue"]]  # not "PERIODE - …" nor archived "Z-UE - …"
    wanted = {w for w in normalized_words(formation) if w.isalpha() and len(w) >= 3} | {"taf"}
    preferred = [cid for cid, label in catalogues if set(normalized_words(label)) & wanted]
    others = [cid for cid, _ in catalogues if cid not in preferred]

    suggestions, seen = [], set()
    for row in samples.values():
        try:
            info = ps.parse_event_ue_info(ps.fetch_event_detail(s, row["lesson_id"], row["date"], nom_cal))
        except requests.RequestException:
            info = {"projet": "", "organismes": []}
        name = ue_name_from_projet(info["projet"] or row["title"].split(" - ")[0])
        key = " ".join(normalized_words(name))
        if not key or key in seen or not is_ue_project(name):
            continue
        seen.add(key)

        codes = list(dict.fromkeys(c for c in map(organism_ue_code, info["organismes"]) if c))
        if len(codes) == 1:
            suggestions.append({"name": name, "code": codes[0], "alternatives": []})
            continue

        matches, term = [], ue_search_term(name)
        for tier in (preferred, others):
            if term and tier:
                matches = [(ue_short_code(full), label) for full, label in ps.search_ue_catalogues(s, term, tier)
                           if ue_names_match(name, label)]
            if matches:
                break
        if matches:
            # Closest names only: "Transition Ecologique et Sociétale" also matches
            # "Ingénieur.e responsable & Transitions Ecologique et Sociétale" (ING150), which
            # must not come before — nor even beside — the exact TES310 / TES390 entries.
            best = min(ue_name_distance(name, label) for _, label in matches)
            matches = [(code, label) for code, label in matches if ue_name_distance(name, label) == best]
        by_code = dict(reversed(matches))  # first label seen for each code, in PASS's order
        ordered = list(dict.fromkeys(code for code, _ in matches))
        suggestions.append({"name": name, "code": ordered[0] if ordered else "",
                            "alternatives": [(code, by_code[code]) for code in ordered[1:]]})
    return suggestions


def current_pass_session() -> requests.Session | None:
    """The Flask session cookie only ever carries this browser's own `token` — a reload
    that lost the in-memory LIVE_SESSIONS entry is recovered by rebuilding a
    requests.Session from that exact token's row in `live_sessions`, never by username,
    so one user's restored session can't end up handed to a different browser."""
    token = session.get("token")
    if not token:
        return None
    s = LIVE_SESSIONS.get(token)
    if s is not None:
        return s

    db = dbmod.get_db()
    row = db.execute("SELECT * FROM live_sessions WHERE token=?", (token,)).fetchone()
    if not row:
        return None
    stored = json.loads(row["cookies_json"])
    if isinstance(stored, dict):
        # Old name->value format (see persist_live_session): can't be restored reliably,
        # so treat it like no session and send the student back to the login form.
        return None
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    for c in stored:
        s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"],
                      secure=c["secure"], expires=c["expires"])
    LIVE_SESSIONS[token] = s
    return s


def persist_live_session(db, token: str, username: str, s: requests.Session) -> None:
    """Every cookie with its domain and path, not a flat name->value dict: restoring such a
    dict created domain-less cookies that PASS's own Set-Cookie then duplicated instead of
    replacing, so after a restart the classic-ASP agenda got the stale ASPSESSIONID and
    answered « Session variable does not exists »."""
    cookies_json = json.dumps([
        {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
         "secure": c.secure, "expires": c.expires}
        for c in s.cookies
    ])
    db.execute(
        "INSERT INTO live_sessions (token, owner_username, cookies_json, created_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(token) DO UPDATE SET cookies_json=excluded.cookies_json",
        (token, username, cookies_json, datetime.datetime.now().isoformat(timespec="seconds")),
    )
    db.commit()
