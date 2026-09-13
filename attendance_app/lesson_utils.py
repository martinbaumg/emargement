"""Pure helpers for lesson timing, name-detection, and cache writes.

No Flask app/request state here — everything takes its inputs as plain arguments so it
stays testable and reusable from the route modules.
"""
import json

# Best-effort "this looks like a person's name" check on PASS's own raw detail lines
# (e.g. "DAGNAT Fabien"), used to fill the Intervenant column on /lessons and the PDF's
# Nom intervenant column — not authoritative, just PASS's own listed presenter(s) for
# that session.
NAME_DENYLIST_PREFIXES = ("GROUPES", "ACTIVITES", "ACTIVITÉS", "EVENEMENTS",
                           "EVÈNEMENTS", "ÉVÈNEMENTS", "PROJET", "UETAF")


def _is_upper_token(t):
    letters = [c for c in t if c.isalpha()]
    return bool(letters) and all(c == c.upper() for c in letters)


def looks_like_person_name(s: str) -> bool:
    if any(ch.isdigit() for ch in s):
        return False
    if "(" in s or ")" in s:
        return False
    tokens = s.split()
    if not (2 <= len(tokens) <= 4):
        return False
    if tokens[0].upper().rstrip(",") in NAME_DENYLIST_PREFIXES:
        return False
    if not all(_is_upper_token(t) for t in tokens[:-1]):
        return False
    return any(c.isalpha() for c in tokens[-1])


def teacher_names(events: list) -> list:
    """Trainers of one or several events (a merged PDF row), de-duplicated in order. PASS's
    own "Formateur(s)" list (e["teachers"], see pass_schedule.add_event_trainers) wins;
    the detail-line guess only covers rows cached before that list existed."""
    names = []
    for e in events:
        for name in e.get("teachers") or [t for t in e["details"] if looks_like_person_name(t)][:2]:
            if name not in names:
                names.append(name)
    return names


def short_teacher_name(name: str) -> str:
    """PASS's "BAUMGAERTNER Martin" -> "BAUMGAERTNER M." for the PDF: the leading
    all-caps words are the surname, then the first given name's initial ("Pierre-Antoine"
    -> "P.-A."; later given names dropped). Anything not in that shape is kept as is."""
    tokens = name.split()
    i = 0
    while i < len(tokens) and _is_upper_token(tokens[i]):
        i += 1
    if i == 0 or i == len(tokens):
        return name
    initials = "-".join(f"{part[0]}." for part in tokens[i].split("-") if part)
    return f"{' '.join(tokens[:i])} {initials}"


def cache_lessons(db, owner_username, events):
    for e in events:
        db.execute(
            "INSERT OR REPLACE INTO lessons_cache "
            "(lesson_id, owner_username, date, time, title, details_json, teachers_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (e["id"], owner_username, e["date"], e["time"], e["title"], json.dumps(e["details"]),
             json.dumps(e.get("teachers", []))),
        )
    db.commit()


def _time_bounds(time_str: str):
    """'08H00-09H15' -> (480, 555) — start/end in minutes since midnight."""
    def to_minutes(hhmm):
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    start_raw, end_raw = time_str.split("-")
    return to_minutes(start_raw.replace("H", ":")), to_minutes(end_raw.replace("H", ":"))


VALID_MERGE_GAPS_MINUTES = (0, 15)  # back-to-back, or the standard one-slot break — nothing else is a real continuation


def merge_contiguous_lessons(events: list) -> list:
    """Group consecutive same-day, same-title events into one PDF row spanning the
    full range, but only when the gap to the previous one is exactly a real break
    (0 or 15 min — e.g. two 1h15 slots with the standard pause in between). Any
    other gap (5 min, 20 min...) means these are two unrelated sessions that just
    happen to share a title, not a continuation — matches how the paper sheet is
    actually filled ("possibilité de rassembler plusieurs créneaux"), without
    inventing a pause that never really happened. `events` must already be sorted
    by (date, time). A lesson with no valid neighbor stays its own group of one,
    with no pause."""
    groups = []
    i, n = 0, len(events)
    while i < n:
        group = [events[i]]
        _, cur_end = _time_bounds(events[i]["time"])
        j = i + 1
        while j < n:
            nxt = events[j]
            if nxt["date"] != events[i]["date"] or nxt["title"] != events[i]["title"]:
                break
            nxt_start, nxt_end = _time_bounds(nxt["time"])
            gap = nxt_start - cur_end
            if gap not in VALID_MERGE_GAPS_MINUTES:
                break
            group.append(nxt)
            cur_end = nxt_end
            j += 1
        groups.append(group)
        i = j
    return groups
