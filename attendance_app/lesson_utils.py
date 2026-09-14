"""Pure helpers for lesson timing, name-detection, and cache writes.

No Flask app/request state here — everything takes its inputs as plain arguments so it
stays testable and reusable from the route modules.
"""
import json
import re
import unicodedata

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


def normalized_words(s: str) -> list:
    """Words compared "flat": no case, no accents, apostrophes and any punctuation are word
    breaks — « L’objet », "L'OBJET" and "l objet" all give ["l", "objet"], and
    "LV-Anglais-A2S7-B" gives ["lv", "anglais", "a2s7", "b"]."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()
    s = s.replace("œ", "oe").replace("æ", "ae")
    return re.findall(r"[a-z0-9]+", s)


def parse_ue_table(text: str) -> list:
    """Profile « Table des UE », one "Nom | autre mot-clé = CODE" per line ->
    [(["Nom", "autre mot-clé"], "CODE"), ...]. The first name is the one printed in the
    PDF's reference table; the others are only extra keywords for matching."""
    table = []
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):  # "# …" lines are notes
            names, code = line.split("=", 1)
            table.append(([n.strip() for n in names.split("|") if n.strip()], code.strip()))
    return table


def _stem(word: str) -> str:
    """Crude French singular: "projets" -> "projet", "langues" -> "langue". Applied to both
    sides, so it only has to be consistent, not linguistically right ("anglais" -> "anglai")."""
    if len(word) > 3 and word[-1] in "sx" and not any(c.isdigit() for c in word):
        return word[:-1]
    return word


# Not required in the title: « L'objet dans son environnement » must still match a title
# written « Objet dans environnement ».
UE_STOPWORDS = {"a", "au", "aux", "d", "dans", "de", "des", "du", "en", "et", "l", "la", "le",
                "les", "par", "pour", "sa", "ses", "son", "sur", "un", "une"}
# A generic "Langues" / "LV" UE covers every language course PASS names by the language
# itself ("Anglais S9 B", "LV-Anglais-…"). One way only: "Anglais" never matches "Espagnol".
GENERIC_LANGUAGE_WORDS = {_stem(w) for w in ("langue", "langues", "lv")}
LANGUAGE_WORDS = GENERIC_LANGUAGE_WORDS | {_stem(w) for w in (
    "anglais", "allemand", "espagnol", "italien", "portugais", "russe", "chinois", "japonais",
    "arabe", "coreen", "francais", "fle")}


def _one_edit_apart(a: str, b: str) -> bool:
    """One inserted, deleted or substituted letter (a typo), not more."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i + (len(a) == len(b)):] == b[i + 1:]


def _keyword_word_matches(word: str, title_words: set) -> bool:
    if word in title_words:
        return True
    if word in GENERIC_LANGUAGE_WORDS:
        return bool(title_words & LANGUAGE_WORDS)
    if len(word) >= 2 and any(c.isdigit() for c in word):
        # Codes glued into a longer token: "S9" in "A3S9", "A2S7" in "FISE-A2S7B".
        return any(word in w for w in title_words)
    if len(word) >= 7:
        return any(len(w) >= 7 and _one_edit_apart(word, w) for w in title_words)
    return False


def ue_code_for(events: list, ue_table: list) -> str:
    """Code of the first UE line with a keyword matching one of `events` (a merged PDF row).
    A keyword matches when every one of its significant words (UE_STOPWORDS left out) is
    found in the title, in any order, compared flat (normalized_words, singular/plural
    folded) — each word either equal to a title word, a digit-bearing code inside one
    ("S9" in "A3S9"), a generic language word against a language name, or one typo away
    for words of 7+ letters. It also matches a PASS detail line with the same words.
    Deliberately no "most words overlap" matching: a blank CODE UE box beats a wrong one
    (half-word overlap put P3AS9 on English classes through "S9")."""
    for names, code in ue_table:
        for name in names:
            raw = normalized_words(name)
            words = [_stem(w) for w in raw]
            # Stopwords checked before stemming: "dans" would otherwise become "dan".
            significant = [_stem(w) for w in raw if w not in UE_STOPWORDS] or words
            if not significant:
                continue
            for ev in events:
                title_words = {_stem(w) for w in normalized_words(ev["title"])}
                if all(_keyword_word_matches(w, title_words) for w in significant) or any(
                        [_stem(w) for w in normalized_words(d)] == words for d in ev["details"]):
                    return code
    return ""


def ue_short_code(full_code: str) -> str:
    """PASS catalogue / organism code -> the UE code the sheet wants: the segment before the
    campus suffix. "FIP-TES310-BR" -> "TES310", "PA-DI-CCU-B" -> "CCU", "UETAF-OBJENV-B" -> "OBJENV"."""
    parts = [p for p in full_code.split("-") if p]
    return parts[-2] if len(parts) >= 2 else full_code


def organism_ue_code(organism: str) -> str:
    """UE code carried by an « Organismes » group name ("UETAF-CCU-B" -> "CCU"), else ""
    — most groups are just cohorts ("FIP A3 BREST")."""
    return ue_short_code(organism) if re.fullmatch(r"UE[A-Z]*-[A-Z0-9]+-[A-Z]+", organism) else ""


def ue_name_from_projet(projet: str) -> str:
    """Agenda « Projets » -> the UE's own name, group/campus suffixes dropped:
    "Transition Ecologique et Sociétale B" -> "Transition Ecologique et Sociétale",
    "Projet A3S9 - B" -> "Projet A3S9", "Anglais S9 B - C1" -> "Anglais S9",
    "LV-Anglais-A2S7-B" -> "LV-Anglais-A2S7"."""
    name = re.split(r"\s+-\s+", projet.strip())[0]
    return re.sub(r"(?:[\s-][A-Z])+$", "", name).strip()


# Agenda « Projets » that are school events, not UEs: no code to look for.
NON_UE_PROJECT_WORDS = {_stem(w) for w in ("activites", "evenements", "presentation", "rattrapages")}


def is_ue_project(name: str) -> bool:
    words = normalized_words(name)
    return bool(words) and _stem(words[0]) not in NON_UE_PROJECT_WORDS


def ue_names_match(a: str, b: str) -> bool:
    """Same UE under two spellings (agenda vs catalogue: "Anglais S9" / "Anglais A3S9",
    "Projet A3S9" / "Projet S9"), tried both ways with ue_code_for's tolerant matching."""
    as_title = lambda title: [{"title": title, "details": []}]
    return bool(ue_code_for(as_title(b), [([a], "x")]) or ue_code_for(as_title(a), [([b], "x")]))


def ue_name_distance(a: str, b: str) -> int:
    """How far two (matching) UE names are: significant words found in only one of them,
    compared flat. 0 for the same name spelled differently."""
    words = lambda s: {_stem(w) for w in normalized_words(s) if w not in UE_STOPWORDS}
    return len(words(a) ^ words(b))


def ue_search_term(name: str) -> str:
    """Longest plain word of a UE name, for PASS's catalogue search (substring, 3+ letters):
    "Projet A3S9" -> "Projet", "Conception centrée utilisateur" -> "utilisateur"."""
    words = re.findall(r"[^\W\d_]{3,}", name)
    return max(words, key=len) if words else ""


def merge_ue_suggestions(existing_text: str, suggestions: list) -> tuple:
    """Existing « Table des UE » + PASS suggestions ({"name", "code", "alternatives"}) ->
    (text, added, to_complete). The student's own lines always win: a suggestion is dropped
    when its code is already in the table, or when the table already gives its UE a code.
    Doubts become "# …" notes (ignored by parse_ue_table) for the student to settle."""
    table = parse_ue_table(existing_text)
    known_codes = {code.casefold() for _, code in table if code}
    lines, added, to_complete = [], 0, 0
    for sug in suggestions:
        if sug["code"] and sug["code"].casefold() in known_codes:
            continue
        if ue_code_for([{"title": sug["name"], "details": []}], table):
            continue
        if sug["code"]:
            lines += [f"# {sug['name']} : PASS propose aussi {code} ({name})" for code, name in sug["alternatives"]]
            lines.append(f"{sug['name']} = {sug['code']}")
            known_codes.add(sug["code"].casefold())
            added += 1
        else:
            lines.append(f"# À compléter, code introuvable dans PASS : {sug['name']} = ")
            to_complete += 1
    if not lines:
        return existing_text, 0, 0
    base = existing_text.rstrip()
    block = "\n".join(["# Ajouté depuis PASS — à vérifier avant d'enregistrer"] + lines)
    return (f"{base}\n{block}" if base else block), added, to_complete


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
