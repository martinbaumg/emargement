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


def cache_lessons(db, owner_username, events):
    for e in events:
        db.execute(
            "INSERT OR REPLACE INTO lessons_cache (lesson_id, owner_username, date, time, title, details_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (e["id"], owner_username, e["date"], e["time"], e["title"], json.dumps(e["details"])),
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
