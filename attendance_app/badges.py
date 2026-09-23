"""Badges earned from the student's own cached schedule — the « Palmarès » page.

Nothing here touches PASS: every badge is computed from what /lessons already stored
(lessons_cache, the exclusions, events_cache_meta, tickets), so the page costs one
round of SQL and never a live fetch. The flip side is that a badge only knows about the
weeks the student has actually loaded — said plainly on the page rather than hidden.
"""
import datetime
import json
import re
import time

import pass_schedule as ps
from lesson_utils import _time_bounds, parse_ue_table, ue_code_for

# Room codes as PASS writes them in the agenda's « Ressources » cell: "BR-D02-217A",
# "BR-B04 005A", "RE-D00-117A". Deliberately strict — the same cell also carries capacities
# ("(VC-220)"), service tags ("DF-BIB") and named places ("BR-Centre vie"), and a badge
# counting rooms is better off missing the amphi called by its name than counting "DF-BIB"
# as a classroom.
ROOM_RE = re.compile(r"\b[A-Z]{2}-[A-Z]\d{2}[ -]\d{3}[A-Z]?\b")

EARLY_START = 8 * 60          # 08H00: "commence à 8h" means at or before this
LATE_END = 18 * 60 + 30       # 18H30: past this, you are closing the building
CONTINUATION_GAP = 15         # same rule as the PDF's merge_contiguous_lessons


def _minutes(time_str: str) -> tuple:
    """(start, end) in minutes since midnight, or None for an all-day/malformed slot —
    same guard as /lessons: PASS does serve entries with no usable time."""
    if not ps.TIME_RE.match(time_str or ""):
        return None
    return _time_bounds(time_str)


def _lessons(db, username: str) -> list:
    rows = db.execute(
        "SELECT lesson_id, date, time, title, details_json, teachers_json FROM lessons_cache "
        "WHERE owner_username=? ORDER BY date, time", (username,)).fetchall()
    lessons = []
    for r in rows:
        bounds = _minutes(r["time"])
        if not bounds:
            continue
        start, end = bounds
        details = json.loads(r["details_json"])
        lessons.append({
            "id": r["lesson_id"], "date": r["date"], "title": r["title"], "details": details,
            "teachers": json.loads(r["teachers_json"]),
            "start": start, "end": end, "minutes": max(0, end - start),
            "rooms": {code for d in details for code in ROOM_RE.findall(d)},
        })
    return lessons


def _by_key(lessons: list, key) -> dict:
    out = {}
    for lesson in lessons:
        out.setdefault(key(lesson), []).append(lesson)
    return out


def _week_key(lesson: dict) -> str:
    return ps.week_bounds(datetime.datetime.strptime(lesson["date"], "%Y%m%d").date())[0]


def _weekday(lesson: dict) -> int:
    return datetime.datetime.strptime(lesson["date"], "%Y%m%d").weekday()


def _longest_block(day: list) -> int:
    """Longest run of back-to-back sessions (breaks of 15 min or less), pauses included —
    what the day actually feels like, whatever the titles say."""
    longest, start, end = 0, day[0]["start"], day[0]["end"]
    for lesson in day[1:]:
        if lesson["start"] - end <= CONTINUATION_GAP:
            end = max(end, lesson["end"])
        else:
            longest = max(longest, end - start)
            start, end = lesson["start"], lesson["end"]
    return max(longest, end - start)


def _longest_gap(day: list) -> int:
    """Biggest hole between two sessions of the same day (overlapping slots merged)."""
    longest, end = 0, day[0]["end"]
    for lesson in day[1:]:
        longest = max(longest, lesson["start"] - end)
        end = max(end, lesson["end"])
    return longest


def _hours(minutes: int) -> str:
    return f"{minutes // 60}h{minutes % 60:02d}"


def facts(db, username: str) -> dict:
    """Everything the badge rules read, computed once from the cached weeks."""
    lessons = _lessons(db, username)
    days = _by_key(lessons, lambda e: e["date"])
    weeks = _by_key(lessons, _week_key)

    excluded_ids = {r["lesson_id"] for r in db.execute(
        "SELECT lesson_id FROM lesson_exclusions WHERE owner_username=?", (username,))}
    excluded_titles = {r["title"] for r in db.execute(
        "SELECT title FROM title_exclusions WHERE owner_username=?", (username,))}
    ue_row = db.execute("SELECT ue_table FROM profiles WHERE owner_username=?", (username,)).fetchone()
    ue_table = parse_ue_table(ue_row["ue_table"] if ue_row else "")

    teacher_minutes, ue_codes = {}, set()
    for lesson in lessons:
        for name in lesson["teachers"]:
            teacher_minutes[name] = teacher_minutes.get(name, 0) + lesson["minutes"]
        code = ue_code_for([lesson], ue_table)
        if code:
            ue_codes.add(code)

    # A week with no interstice at all: at least four days of class, and not one of them with a
    # hole longer than the standard break. Rare on purpose — it needs a whole week to cooperate.
    gapless_weeks = 0
    for week in weeks.values():
        week_days = {}
        for lesson in week:
            week_days.setdefault(lesson["date"], []).append(lesson)
        if len(week_days) >= 4 and all(_longest_gap(day) <= CONTINUATION_GAP for day in week_days.values()):
            gapless_weeks += 1

    top_teacher = max(teacher_minutes.items(), key=lambda kv: kv[1], default=("", 0))
    weeks_loaded = db.execute(
        "SELECT count(*) FROM events_cache_meta WHERE owner_username=?", (username,)).fetchone()[0]

    return {
        "lessons": lessons,
        "total_minutes": sum(e["minutes"] for e in lessons),
        "weeks_loaded": weeks_loaded,
        "weeks_with_lessons": len(weeks),
        "early_starts": sum(1 for e in lessons if e["start"] <= EARLY_START),
        "late_ends": sum(1 for e in lessons if e["end"] >= LATE_END),
        "friday_free_weeks": sum(1 for week in weeks.values() if not any(_weekday(e) == 4 for e in week)),
        "full_weeks": sum(1 for week in weeks.values() if len({_weekday(e) for e in week if _weekday(e) < 5}) == 5),
        "max_week_minutes": max((sum(e["minutes"] for e in week) for week in weeks.values()), default=0),
        "max_day_span": max((max(e["end"] for e in day) - min(e["start"] for e in day)
                             for day in days.values()), default=0),
        "max_block": max((_longest_block(day) for day in days.values()), default=0),
        "max_gap": max((_longest_gap(day) for day in days.values()), default=0),
        "rooms": len({room for e in lessons for room in e["rooms"]}),
        "long_days": sum(1 for day in days.values()
                         if min(e["start"] for e in day) <= EARLY_START
                         and max(e["end"] for e in day) >= LATE_END),
        "weekend_sessions": sum(1 for e in lessons if _weekday(e) >= 5),
        "gapless_weeks": gapless_weeks,
        "max_day_teachers": max((len({t for e in day for t in e["teachers"]}) for day in days.values()),
                                default=0),
        "top_teacher": top_teacher[0],
        "top_teacher_minutes": top_teacher[1],
        "ue_codes": len(ue_codes),
        "excluded_minutes": sum(e["minutes"] for e in lessons
                                if e["id"] in excluded_ids or e["title"] in excluded_titles),
        "tickets": db.execute("SELECT count(*) FROM tickets WHERE owner_username=?", (username,)).fetchone()[0],
    }


# (id, title, DSFR icon, what it takes, the line once it's yours, fact key, target, formatter)
# Targets are deliberately reachable in a semester — a badge nobody can get is just a
# grey tile. `fmt` turns a raw fact into what the progress line shows.
DEFINITIONS = [
    ("huit_heures", "Survivant du 8h", "fr-icon-alarm-warning-line",
     "5 séances qui commencent à 8h00 ou avant.",
     "Le réveil a perdu cinq fois. Vous êtes toujours là.",
     "early_starts", 5, "count"),
    ("zero_vendredi", "Zéro vendredi", "fr-icon-calendar-2-line",
     "Une semaine entière sans le moindre cours le vendredi.",
     "Votre week-end commence le jeudi soir. Personne ne peut vous l'enlever.",
     "friday_free_weeks", 1, "count"),
    ("marathon", "Marathon 10h", "fr-icon-timer-line",
     "Une journée de 10 heures entre le premier et le dernier cours.",
     "Parti à l'aube, rentré à la nuit. Pour un seul et même jour.",
     "max_day_span", 600, "hours"),
    ("bloc", "Bloc de quatre heures", "fr-icon-stop-circle-line",
     "4 heures d'affilée, pauses de 15 minutes comprises.",
     "Aucune échappatoire, juste des pauses réglementaires.",
     "max_block", 240, "hours"),
    ("trou_noir", "Trou noir", "fr-icon-pause-circle-line",
     "3 heures de trou entre deux cours de la même journée.",
     "Un vide sidéral au milieu de la journée. La bibliothèque vous connaît.",
     "max_gap", 180, "hours"),
    ("forcat", "Semaine de forçat", "fr-icon-fire-line",
     "30 heures de cours dans une seule semaine.",
     "Une semaine à temps plein, sans les avantages.",
     "max_week_minutes", 1800, "hours"),
    ("grand_chelem", "Grand chelem", "fr-icon-calendar-check-line",
     "Une semaine avec cours les cinq jours ouvrés.",
     "Lundi, mardi, mercredi, jeudi, vendredi. Le carton plein.",
     "full_weeks", 1, "count"),
    ("fermeture", "Fermeture du bâtiment", "fr-icon-moon-line",
     "Une séance qui se termine à 18h30 ou plus tard.",
     "Vous avez vu le gardien éteindre les lumières.",
     "late_ends", 1, "count"),
    ("proprietaire", "Tour du propriétaire", "fr-icon-map-pin-2-line",
     "10 salles différentes fréquentées.",
     "Vous connaissez le bâtiment mieux que le plan d'évacuation.",
     "rooms", 10, "count"),
    ("fidele", "Abonné au même intervenant", "fr-icon-user-star-line",
     "10 heures passées avec le même intervenant.",
     "À ce stade, ce n'est plus un cours, c'est une relation.",
     "top_teacher_minutes", 600, "hours"),
    ("collectionneur", "Collectionneur d'UE", "fr-icon-hashtag",
     "8 codes UE différents sur vos séances.",
     "Votre table des UE est plus complète que le catalogue.",
     "ue_codes", 8, "count"),
    ("autonome", "Grand autonome", "fr-icon-user-heart-line",
     "10 heures de séances retirées du PDF (travail en autonomie…).",
     "Ces heures-là ne se signent pas. Elles se vivent.",
     "excluded_minutes", 600, "hours"),
    ("centurion", "Centurion", "fr-icon-award-line",
     "100 heures de cours cumulées sur les semaines chargées.",
     "Cent heures. Le chiffre rond qui fait réfléchir.",
     "total_minutes", 6000, "hours"),
    ("alerte", "Lanceur d'alerte", "fr-icon-questionnaire-line",
     "Un ticket ouvert à l'assistance.",
     "Vous avez signalé quelque chose. C'est déjà plus que la moyenne.",
     "tickets", 1, "count"),
    ("explorateur", "Explorateur de semaines", "fr-icon-compass-3-line",
     "8 semaines chargées depuis PASS.",
     "Vous consultez l'avenir. Il ressemble beaucoup au présent.",
     "weeks_loaded", 8, "count"),

    # ---- the hard half: each one asks for a week that went genuinely badly ----
    ("quarante", "Semaine de quarante heures", "fr-icon-briefcase-line",
     "40 heures de cours dans une seule semaine.",
     "Un temps plein. Sans le salaire, sans les RTT, sans le comité d'entreprise.",
     "max_week_minutes", 2400, "hours"),
    ("douze_heures", "Amplitude douze heures", "fr-icon-anticlockwise-line",
     "Une journée de 12 heures entre le premier et le dernier cours.",
     "Vous avez vu le bâtiment ouvrir et vous l'avez vu fermer.",
     "max_day_span", 720, "hours"),
    ("bloc_six", "Bloc de six heures", "fr-icon-lock-line",
     "6 heures d'affilée, pauses réglementaires comprises.",
     "Plus personne ne prend de notes depuis la troisième heure.",
     "max_block", 360, "hours"),
    ("matin_soir", "Du matin au soir", "fr-icon-sun-line",
     "Une journée qui commence à 8h00 au plus tard et finit à 18h30 au plus tôt.",
     "Le jour s'est levé sans vous et couché sans vous.",
     "long_days", 1, "count"),
    ("abysse", "Trou abyssal", "fr-icon-arrow-down-circle-line",
     "5 heures de trou dans une même journée.",
     "Une demi-journée de liberté, soigneusement emmurée entre deux cours.",
     "max_gap", 300, "hours"),
    ("sans_trou", "Semaine sans interstice", "fr-icon-align-justify",
     "Une semaine d'au moins 4 jours sans un seul trou de plus de 15 minutes.",
     "Pas une faille dans l'emploi du temps. Le planning parfait, dans le mauvais sens.",
     "gapless_weeks", 1, "count"),
    ("weekend", "Cours le week-end", "fr-icon-calendar-event-line",
     "Une séance un samedi ou un dimanche.",
     "Quelqu'un, quelque part, a validé ça dans un planning. Et l'a trouvé normal.",
     "weekend_sessions", 1, "count"),
    ("defile", "Le défilé", "fr-icon-team-line",
     "5 intervenants différents dans la même journée.",
     "Cinq personnes vous ont expliqué quelque chose. Vous en avez retenu deux.",
     "max_day_teachers", 5, "count"),
    ("cadastre", "Cadastre complet", "fr-icon-building-line",
     "20 salles différentes fréquentées.",
     "Il ne vous manque plus que la salle des serveurs et le local à vélos.",
     "rooms", 20, "count"),
    ("bicentenaire", "Bicentenaire", "fr-icon-medal-line",
     "200 heures de cours cumulées.",
     "Deux cents heures. On ne compte plus, on constate.",
     "total_minutes", 12000, "hours"),

    # Last on purpose, and last in every sense: its own count is the wall behind it.
    ("integrale", "L'intégrale", "fr-icon-trophy-line",
     "Tous les autres succès, sans exception.",
     "Il n'y a plus rien à décrocher. C'était précisément le problème.",
     "", 0, "count"),
]

# The one badge that isn't read off a fact: it counts the others (see evaluate).
COMPLETION_ID = "integrale"

# Badges that measure how much the app is used rather than how the week went — kept on the wall,
# left out of the score the TAF ranking compares, which would otherwise reward opening the site.
USAGE_BADGES = {"explorateur", "alerte"}

# Share of participants holding a badge -> what it's worth saying about it. Colours come from the
# DSFR's decorative palettes (green / terre battue / glycine), never from its status colours:
# "Épique" is a flourish, not a warning. No blue, so the « Je suis FIP » sheet leaves them alone.
RARITY_TIERS = [
    (60, "Commun", "att-tier--commun"),
    (30, "Peu commun", "att-tier--peu-commun"),
    (10, "Rare", "att-tier--rare"),
    (1, "Épique", "att-tier--epique"),
    (0, "Inédit", "att-tier--epique"),  # nobody has it yet — the page says so in words
]


# Earned count -> the title the page gives you. Deadpan on purpose: the ranks are the joke.
RANKS = [
    (0, "Fantôme du bâtiment B", "Aucun badge. Techniquement, vous n'avez jamais existé."),
    (1, "Présence remarquée", "On vous a vu. Une fois. C'est un début."),
    (5, "Habitué", "Votre badge d'accès commence à être usé."),
    (10, "Pilier de l'amphi", "Une place attitrée, au troisième rang."),
    (15, "Référence locale", "Les nouveaux vous demandent où est la salle."),
    (20, "Légende de l'émargement", "Votre feuille est affichée au mur de la scolarité."),
    # Computed, so adding a badge moves the last rung instead of leaving it unreachable.
    (len(DEFINITIONS), "Intégraliste", "Vous avez tout. Y compris le badge qui dit que vous avez tout."),
]


def _progress_label(value: int, target: int, fmt: str) -> str:
    if fmt == "hours":
        return f"{_hours(min(value, target))} / {_hours(target)}"
    return f"{min(value, target)} / {target}"


def featured_id(db, username: str) -> str:
    """The badge this student pinned to their profile, by id — "" when none."""
    row = db.execute("SELECT featured_badge FROM profiles WHERE owner_username=?", (username,)).fetchone()
    return row["featured_badge"] if row else ""


def _badge(definition: tuple, value: int, target: int, pinned: str) -> dict:
    badge_id, title, icon, rule, flavour, _key, _target, fmt = definition
    return {
        "id": badge_id, "title": title, "icon": icon, "rule": rule, "flavour": flavour,
        "earned": value >= target, "featured": badge_id == pinned,
        "value": value, "target": target,
        "progress": min(100, round(100 * value / target)) if target else 0,
        "progress_label": _progress_label(value, target, fmt),
    }


def evaluate(db, username: str) -> dict:
    """The badge wall: every definition with its state, plus the rank and the near miss."""
    data = facts(db, username)
    pinned = featured_id(db, username)
    # Two passes: COMPLETION_ID has no fact of its own — what it measures is the wall itself,
    # so it can only be settled once every other badge has been.
    badges = [_badge(d, data[d[5]], d[6], pinned) for d in DEFINITIONS if d[0] != COMPLETION_ID]
    badges.append(_badge(next(d for d in DEFINITIONS if d[0] == COMPLETION_ID),
                         sum(1 for b in badges if b["earned"]), len(DEFINITIONS) - 1, pinned))
    # Earned first: a wall that opens on what you got reads better than one that opens on
    # what you missed. Within each half, the closest to done comes first.
    badges.sort(key=lambda b: (not b["earned"], -b["progress"], b["title"]))
    earned = sum(1 for b in badges if b["earned"])
    rank = [r for r in RANKS if earned >= r[0]][-1]
    next_up = next((b for b in badges
                    if not b["earned"] and b["progress"] and b["id"] != COMPLETION_ID), None)

    return {
        "badges": badges,
        "featured": next((b for b in badges if b["featured"] and b["earned"]), None),
        "earned": earned,
        "total": len(badges),
        "rank": rank[1],
        "rank_line": rank[2],
        "next_up": next_up,
        "facts": data,
        "total_hours": _hours(data["total_minutes"]),
        "weeks_loaded": data["weeks_loaded"],
        "lesson_count": len(data["lessons"]),
        "top_teacher": data["top_teacher"],
        "top_teacher_hours": _hours(data["top_teacher_minutes"]),
    }


def featured(db, username: str) -> dict | None:
    """The pinned badge as the profile shows it, or None. Re-checked against the current data
    rather than trusted from the column: a badge can stop being earned (sessions put back on
    the PDF undo « Grand autonome »), and a profile shouldn't keep bragging about it."""
    if not featured_id(db, username):
        return None
    return evaluate(db, username)["featured"]


def set_featured(db, username: str, badge_id: str) -> dict | None:
    """Pins one badge to the profile, replacing whatever was there — `badge_id` empty unpins.
    Returns the badge, or None when it was a clear. Refuses (None, nothing written) an id that
    is unknown or not earned: the buttons only exist on earned badges, so anything else is a
    hand-made POST. Upserts, since a student can reach the palmarès before saving a profile."""
    badge = None
    if badge_id:
        badge = next((b for b in evaluate(db, username)["badges"]
                      if b["id"] == badge_id and b["earned"]), None)
        if not badge:
            return None
    db.execute("INSERT INTO profiles (owner_username, featured_badge) VALUES (?, ?) "
               "ON CONFLICT(owner_username) DO UPDATE SET featured_badge=excluded.featured_badge",
               (username, badge_id))
    db.commit()
    return badge


# ------------------------------------------------------------------ the others, in aggregate

def record_score(db, username: str, state: dict) -> None:
    """Refreshes this student's own tally, on their way into the palmarès. One upsert per visit
    instead of re-evaluating every account on every page view; a student who stops coming keeps
    their last tally, which is also their last true one — badges only move when the app is used."""
    earned = [b["id"] for b in state["badges"] if b["earned"]]
    row = db.execute("SELECT taf FROM profiles WHERE owner_username=?", (username,)).fetchone()
    db.execute(
        "INSERT INTO badge_scores (owner_username, taf, earned, score, badges_json, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(owner_username) DO UPDATE SET taf=excluded.taf, "
        "earned=excluded.earned, score=excluded.score, badges_json=excluded.badges_json, "
        "updated_at=excluded.updated_at",
        (username, (row["taf"] if row else "").strip().upper(), len(earned),
         sum(1 for b in earned if b not in USAGE_BADGES), json.dumps(earned), time.time()),
    )
    db.commit()


def _tier(pct: int) -> tuple:
    return next((t[1:] for t in RARITY_TIERS if pct >= t[0]), RARITY_TIERS[-1][1:])


def population(db, username: str) -> dict:
    """How the others did — as percentages and group averages only. No name, no pseudonym, no
    per-person row: the one individual figure on the page is the reader's own position, which
    says nothing about anyone else."""
    rows = db.execute("SELECT owner_username, taf, score, badges_json FROM badge_scores").fetchall()
    participants = len(rows)
    mine = next((r for r in rows if r["owner_username"] == username), None)
    my_score = mine["score"] if mine else 0

    holders = {}
    for r in rows:
        for badge_id in json.loads(r["badges_json"]):
            holders[badge_id] = holders.get(badge_id, 0) + 1
    rarity = {}
    for badge_id in (d[0] for d in DEFINITIONS):
        pct = round(100 * holders.get(badge_id, 0) / participants) if participants else 0
        tier, css = _tier(pct)
        rarity[badge_id] = {"pct": pct, "tier": tier, "class": css}

    groups = {}
    for r in rows:
        if r["taf"]:
            groups.setdefault(r["taf"], []).append(r["score"])
    my_taf = (mine["taf"] if mine else "") or ""
    ranking = sorted(
        ({"taf": taf, "participants": len(scores), "average": round(sum(scores) / len(scores), 1),
          "mine": taf == my_taf}
         for taf, scores in groups.items()),
        key=lambda g: (-g["average"], g["taf"]))
    # Bars are read against the leader, not against the maximum reachable score: the point of
    # the row is the gap to the TAF above, and a bar at 30% of a theoretical 13 says nothing.
    top = ranking[0]["average"] if ranking else 0
    for i, group in enumerate(ranking):
        group["position"] = i + 1
        group["share"] = round(100 * group["average"] / top) if top else 0

    return {
        "participants": participants,
        "rarity": rarity,
        "rank": sum(1 for r in rows if r["score"] > my_score) + 1,
        "percentile": max(1, round(100 * (sum(1 for r in rows if r["score"] > my_score) + 1) / participants))
        if participants else 100,
        "my_score": my_score,
        "max_score": len(DEFINITIONS) - len(USAGE_BADGES),
        "taf_ranking": ranking,
        "my_taf": my_taf,
    }


def wall(db, username: str) -> dict:
    """Everything the palmarès page renders: this student's badges, their rank and the rarity of
    each badge among the others."""
    state = evaluate(db, username)
    record_score(db, username, state)
    pop = population(db, username)
    for badge in state["badges"]:
        badge["rarity"] = pop["rarity"].get(badge["id"])
    state["pop"] = pop
    return state
