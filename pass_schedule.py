#!/usr/bin/env python3
"""
Fetch and parse the weekly course schedule from pass.imt-atlantique.fr (Alcuin/OpenPortal).

Auth: either
  - a live browser session cookie (--cookie / --cookie-file), or
  - your SSO credentials (--login), which drives the CAS/SAMLv2 login flow headlessly.
    Password is prompted locally via getpass — never pass it as a CLI argument (shows up
    in shell history / ps) and never paste it in chat.

Session cookie chain (once authenticated):
  1. GET  Default.aspx                         -> confirm session alive
  2. POST Services/.../GetMainMenuDatas.sopx    -> get "Agenda" link id (usually 154)
  3. GET  aspxtoasp.aspx?url=Agenda.asp...      -> auto-submit bridge form (.NET -> classic ASP)
  4. POST commun/aspxtoasp.asp                  -> 302 redirect with one-time token
  5. GET  Eplug/Agenda/Agenda.asp?...&token=... -> agenda in whatever view the account
                                                  last used
  6. POST Eplug/Agenda/Agenda.asp (full form + TypVis=Vis-Tab.xsl) -> "Tableau"
                                                  view, the only one carrying real times

SSO login chain (--login):
  1. GET  OpDotNet/Noyau/Default.aspx?          -> 302 to /?url=<base64>
  2. GET  that                                  -> auto-submit form -> POST Login.aspx?url=...
  3. POST Login.aspx?url=... (empty body)       -> login page (provider = SAMLv2ProviderConfiguration)
  4. GET  Login.aspx?url=...&auth=SAMLv2ProviderConfiguration -> auto-submit SAMLRequest -> IdP SSO
  5. POST SAMLRequest to idp.imt-atlantique.fr/idp/profile/SAML2/POST/SSO
  6. 302 chain -> /idp/Authn/ExtCas -> cas.imt-atlantique.fr/cas/login (real CAS form: username/password/lt/execution)
  7. POST credentials to CAS -> redirect back through IdP -> auto-submit SAMLResponse back to pass.imt-atlantique.fr
  8. Lands back on Default.aspx frameset with ASP.NET_SessionId / ASPSESSIONID cookies set.

Usage:
  python3 pass_schedule.py --login myusername
  python3 pass_schedule.py --cookie 'ASP.NET_SessionId=...; ASPSESSIONIDSWAACDSB=...; ...'
  python3 pass_schedule.py --cookie-file cookie.txt
  python3 pass_schedule.py --login myusername --json
"""
import argparse
import datetime
import getpass
import html
import json
import logging
import re
import sys
import tempfile
from collections import defaultdict
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

BASE = "https://pass.imt-atlantique.fr"
GROUP_ID = 31  # "Etudiants" space; adjust if GetMainMenuDatas returns a different default

LOGIN_START = f"{BASE}/OpDotNet/Noyau/Default.aspx?"
AUTOSUBMIT_FORM_RE = re.compile(r'<form[^>]*action="([^"]+)"[^>]*>(.*?)</form>', re.IGNORECASE | re.DOTALL)
INPUT_TAG_RE = re.compile(r"<input[^>]*>", re.IGNORECASE)
INPUT_ATTR_RE = {
    "name": re.compile(r'name="([^"]*)"', re.IGNORECASE),
    "value": re.compile(r'value="([^"]*)"', re.IGNORECASE),
    "type": re.compile(r'type="([^"]*)"', re.IGNORECASE),
}

DAYS_FR = {0: "Lundi", 1: "Mardi", 2: "Mercredi", 3: "Jeudi", 4: "Vendredi", 5: "Samedi", 6: "Dimanche"}

EVENT_RE = re.compile(
    r"onmouseover=\"DetEve\('(?P<id>\d+)','[^']*','(?P<date>\d{8})'\);\">\s*"
    r"<b>(?P<title>.*?)</b>(?P<rest>.*?)</TD>",
    re.IGNORECASE | re.DOTALL,
)
FONT_RE = re.compile(r"<font size=\"1\">(.*?)</font>", re.IGNORECASE | re.DOTALL)
TIME_RE = re.compile(r"^\d{1,2}H\d{2}-\d{1,2}H\d{2}$")

# PASS remembers, per account, which agenda view was last used (the "TypVis" dropdown:
# Jour / Semaine / Semaine complète / Mois / Tableau) and serves that one on the next
# visit — so what the scraper gets depends on the student's own saved preference, and
# the views are not interchangeable:
#   - the calendar views (Jour / Semaine / Semaine complète) lay events out on an hour
#     grid where the hour lives in the row header and the event cell holds nothing but
#     <b>title</b>. No exact time is recoverable (the grid's granularity is ValGra=60,
#     so an 09h30-10h45 session is just "somewhere in the 09h00 row");
#   - "Mois" doesn't render events as cells at all;
#   - "Tableau" (Vis-Tab.xsl) lists one <tr> per event with explicit Date / Début / Fin
#     / Description columns and the same DetEve('<id>') event ids as the grid.
# Hence: never trust the account's stored preference, force the Tableau view — the only
# one that carries real times. It answers with a month around NumDat rather than a week,
# so callers narrow it down themselves (see filter_week).
AGENDA_VIEW = "Vis-Tab.xsl"
AGENDA_VIEW_SELECT_RE = re.compile(r'<select[^>]*name="TypVis".*?</select>', re.IGNORECASE | re.DOTALL)
AGENDA_VIEW_SELECTED_RE = re.compile(r'<option\s+value="([^"]+)"[^>]*\bselected\b', re.IGNORECASE)


def _save_page(kind: str, content: str) -> str:
    """Raw response -> a temp file (container-local /tmp — fine, it's only needed for the
    next `docker exec` while debugging, not persisted storage)."""
    fd, path = tempfile.mkstemp(prefix=f"pass_debug_{kind}_", suffix=".html")
    with open(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _dump_debug_page(kind: str, content: str, username: str | None) -> str:
    """A parse regex/lookup came up empty — PASS's page structure likely changed. Save the
    page and log its path plus who hit it, so a report of "PDF/agenda broken" can be traced
    to the actual page without asking the user to repro."""
    path = _save_page(kind, content)
    logger.error("PASS page structure mismatch (%s) user=%s dump=%s", kind, username or "?", path)
    return path


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def parse_cookie_string(cookie_str: str) -> dict:
    jar = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        jar[k.strip()] = v.strip()
    return jar


def get_session(cookies: dict) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    for k, v in cookies.items():
        s.cookies.set(k, v, domain="pass.imt-atlantique.fr")
    return s


def parse_inputs(form_body: str) -> list:
    """[(name, value, type, checked), ...] for every <input> in a form body, HTML-entities decoded."""
    result = []
    for tag in INPUT_TAG_RE.findall(form_body):
        name_m = INPUT_ATTR_RE["name"].search(tag)
        if not name_m:
            continue
        name = html.unescape(name_m.group(1))
        value_m = INPUT_ATTR_RE["value"].search(tag)
        value = html.unescape(value_m.group(1)) if value_m else ""
        type_m = INPUT_ATTR_RE["type"].search(tag)
        itype = type_m.group(1).lower() if type_m else "text"
        checked = bool(re.search(r"\bchecked\b", tag, re.IGNORECASE))
        result.append((name, value, itype, checked))
    return result


def extract_hidden_fields(form_body: str) -> dict:
    """Hidden inputs -> {name: value}, or {name: [values]} when a name repeats — Shibboleth's
    consent form sends the attribute list as several same-named _shib_idp_consentIds inputs,
    and a plain dict would silently drop all but the last one."""
    fields = {}
    for name, value, itype, _checked in parse_inputs(form_body):
        if itype != "hidden":
            continue
        if name in fields:
            if isinstance(fields[name], list):
                fields[name].append(value)
            else:
                fields[name] = [fields[name], value]
        else:
            fields[name] = value
    return fields


def form_fields(form_body: str) -> dict:
    """Every successful control of a form, the way a browser submits it: hidden and text
    inputs, checkboxes/radios only when checked, nothing disabled. Repeated names become
    lists — PASS's agenda sends one `TypFil` value per checked event category plus one
    `EtaFil` per checked status, and a plain dict keeping only the last one leaves the
    agenda with an empty filter, which it answers with zero events in every view."""
    fields = {}
    for tag in INPUT_TAG_RE.findall(form_body):
        name_m = INPUT_ATTR_RE["name"].search(tag)
        if not name_m or re.search(r"\bdisabled\b", tag, re.IGNORECASE):
            continue
        type_m = INPUT_ATTR_RE["type"].search(tag)
        itype = type_m.group(1).lower() if type_m else "text"
        if itype in ("submit", "button", "image", "reset", "file"):
            continue
        if itype in ("checkbox", "radio") and not re.search(r"\bchecked\b", tag, re.IGNORECASE):
            continue
        name = html.unescape(name_m.group(1))
        value_m = INPUT_ATTR_RE["value"].search(tag)
        value = html.unescape(value_m.group(1)) if value_m else ""
        if name in fields:
            if isinstance(fields[name], list):
                fields[name].append(value)
            else:
                fields[name] = [fields[name], value]
        else:
            fields[name] = value
    return fields


def is_cas_login_form(form_body: str) -> bool:
    return 'name="password"' in form_body and 'name="username"' in form_body


def is_autosubmit_page(text: str) -> bool:
    """These bridge pages (SAML POST binding, aspxtoasp) carry <body onload="...submit()">
    with a single form and no user-fillable fields. The real CAS login page has neither."""
    return "onload" in text.lower() and "submit()" in text


def autosubmit_chain(s: requests.Session, resp: requests.Response, max_hops: int = 8) -> requests.Response:
    """Follow auto-submitting onload-form hops (SAML POST binding pages) until landing on a
    normal page (the CAS login form, or the final destination)."""
    for _ in range(max_hops):
        if not is_autosubmit_page(resp.text):
            return resp
        m = AUTOSUBMIT_FORM_RE.search(resp.text)
        if not m:
            return resp
        action, body = m.group(1), m.group(2)
        fields = extract_hidden_fields(body)
        url = urljoin(resp.url, html.unescape(action))
        resp = s.post(url, data=fields, timeout=15)
    return resp


def handle_consent_if_present(s: requests.Session, r: requests.Response) -> requests.Response:
    """Shibboleth's one-time 'Information Release' attribute-consent screen. Not an
    onload-autosubmit page (needs a real click), so autosubmit_chain skips over it —
    accept it explicitly, keeping whatever the IdP pre-selected as default."""
    if "attributeRelease" not in r.text and "Information Release" not in r.text:
        return r
    m = AUTOSUBMIT_FORM_RE.search(r.text)
    if not m:
        return r
    action, body = m.group(1), m.group(2)
    fields = extract_hidden_fields(body)
    for name, value, itype, checked in parse_inputs(body):
        if itype in ("checkbox", "radio") and checked:
            fields[name] = value
    fields["_eventId_proceed"] = "Accept"
    return s.post(urljoin(r.url, html.unescape(action)), data=fields, timeout=15)


def login(s: requests.Session, username: str, password: str) -> None:
    """Drive the CAS/SAMLv2 SSO login flow headlessly. Raises RuntimeError on failure."""
    r = s.get(LOGIN_START, timeout=15)  # -> /?url=<base64>
    r = autosubmit_chain(s, r)          # -> POST Login.aspx?url=... (empty body form) -> login chooser page

    # trigger the SSO provider explicitly (mirrors the JS button's GetProvidersUri() call)
    sep = "&" if "?" in r.url else "?"
    r = s.get(r.url + sep + "auth=SAMLv2ProviderConfiguration", timeout=15)
    r = autosubmit_chain(s, r)  # walks SAMLRequest POST -> IdP -> ExtCas -> CAS login page

    m = AUTOSUBMIT_FORM_RE.search(r.text)
    if not m or not is_cas_login_form(m.group(2)):
        dump_path = _dump_debug_page("cas_login_form", r.text, username)
        raise RuntimeError(f"Formulaire de connexion CAS introuvable — le flux a peut-être changé. Page brute sauvée dans {dump_path}.")

    action, body = m.group(1), m.group(2)
    fields = extract_hidden_fields(body)
    fields["username"] = username
    fields["password"] = password
    fields["_eventId"] = "submit"

    r = s.post(urljoin(r.url, html.unescape(action)), data=fields, timeout=15)
    if 'name="password"' in r.text and 'name="username"' in r.text:
        raise RuntimeError("Identifiant ou mot de passe incorrect.")

    r = handle_consent_if_present(s, r)  # Shibboleth attribute-release screen, first login only
    r = autosubmit_chain(s, r)           # walk SAMLResponse back to the SP (pass.imt-atlantique.fr)

    if "FRAMESET" not in r.text.upper() and "Default.aspx" not in r.url:
        raise RuntimeError("Connexion terminée mais retour au portail PASS échoué — vérification manuelle nécessaire.")


def check_session_alive(s: requests.Session):
    r = s.get(f"{BASE}/OpDotNet/Noyau/Default.aspx?", allow_redirects=True, timeout=15)
    r.raise_for_status()
    # A dead session doesn't land on a login page here — it 200s on
    # OPErreur.aspx?errCode=1, "Session perdue / Vous n'êtes pas ou plus authentifié à OP !".
    if "login" in r.url.lower() or "login.aspx" in r.text.lower() or "operreur.aspx" in r.url.lower():
        raise RuntimeError("Session expirée ou invalide — récupérez un cookie récent depuis le navigateur.")


def get_menu_link(s: requests.Session, link_name: str, group_id: int = GROUP_ID, username: str | None = None) -> dict:
    url = (
        f"{BASE}/OpDotNet/Services/"
        "OpenPortal.Entities.Commun.Services.IMainMenuUIServices^OpenPortal.Entities/"
        "GetMainMenuDatas.sopx"
    )
    r = s.post(url, json={"groupId": group_id, "isIEEdgeModeEnabled": False}, timeout=15)
    r.raise_for_status()
    data = r.json()
    for link in data.get("links", []):
        if link.get("linkName") == link_name:
            return link
    dump_path = _dump_debug_page("menu", r.text, username)
    raise RuntimeError(
        f"Lien '{link_name}' introuvable dans le menu — vérifiez group_id ou un changement de structure du menu. "
        f"Page brute sauvée dans {dump_path}."
    )


def get_agenda_link(s: requests.Session, group_id: int = GROUP_ID, username: str | None = None) -> dict:
    return get_menu_link(s, "Agenda", group_id, username)


def bridge_to_classic_asp(s: requests.Session, agenda_link: dict, username: str | None = None) -> str:
    """Follow the aspxtoasp bridge form and return the final Agenda.asp URL (with token)."""
    bridge_url = urljoin(BASE, agenda_link["url"])
    r = s.get(bridge_url, timeout=15)
    r.raise_for_status()

    form_fields = dict(re.findall(r'name="([^"]+)"\s+value="([^"]*)"', r.text))
    if "url" not in form_fields:
        dump_path = _dump_debug_page("bridge_form", r.text, username)
        raise RuntimeError(f"Champs du formulaire de transition introuvables — la page a peut-être changé. Page brute sauvée dans {dump_path}.")

    r2 = s.post(f"{BASE}/commun/aspxtoasp.asp", data=form_fields, allow_redirects=False, timeout=15)
    if r2.status_code not in (301, 302):
        raise RuntimeError(f"Redirection attendue depuis aspxtoasp.asp, reçu {r2.status_code}")

    return urljoin(BASE, r2.headers["Location"])


AGENDA_NUMDAT_RE = re.compile(r'name="NumDat"\s+value="(\d+)"', re.IGNORECASE)


def force_agenda_view(s: requests.Session, r: requests.Response, view: str, username: str | None = None,
                      num_dat: str | None = None) -> str:
    """Re-submit the agenda page's own form with TypVis=<view>, and NumDat=<num_dat> to
    move to another period — mirrors what the dropdown's onchange="Valider()" and the
    NavDat() arrows do in the browser (POST to Agenda.asp, session cookies only, no token
    needed). num_dat is a YYYYMMDD date; the Tableau view answers with its whole month."""
    sel = AGENDA_VIEW_SELECT_RE.search(r.text)
    current_view = AGENDA_VIEW_SELECTED_RE.search(sel.group(0)) if sel else None
    current_date = AGENDA_NUMDAT_RE.search(r.text)
    if (current_view and current_view.group(1) == view
            and (num_dat is None or (current_date and current_date.group(1) == num_dat))):
        return r.text  # already the right view on the right period, nothing to re-post

    m = AUTOSUBMIT_FORM_RE.search(r.text)
    if not m:
        # No form to re-submit: keep whatever we got rather than failing the whole fetch —
        # parse_events() will log/dump if the times turn out to be unparseable.
        _dump_debug_page("agenda_form", r.text, username)
        return r.text

    action, body = m.group(1), m.group(2)
    # Full form, not just the hidden fields: NumDat (week), DebHor/FinHor, NomCal *and*
    # the checked EtaFil/TypFil filter boxes that decide which events show up at all.
    fields = form_fields(body)
    fields["TypVis"] = view
    if num_dat:
        fields["NumDat"] = num_dat
    r2 = s.post(urljoin(r.url, html.unescape(action)), data=fields, timeout=15)
    r2.raise_for_status()
    return r2.text


ASP_SESSION_LOST_MARKER = "Session variable does not exists"


def check_asp_session(text: str) -> None:
    """The classic-ASP side answers a lost session with a 200 "Erreur" page reading
    « Session variable does not exists. » — check_session_alive() doesn't catch it (the
    ASP.NET side is still fine), and the page holds no event, so without this it reads as
    an empty week and gets marked fetched as one."""
    if ASP_SESSION_LOST_MARKER in text:
        raise RuntimeError("Session PASS (agenda) perdue — reconnectez-vous.")


def fetch_agenda_html(s: requests.Session, agenda_url: str, username: str | None = None,
                      view: str | None = AGENDA_VIEW, num_dat: str | None = None) -> str:
    r = s.get(agenda_url, timeout=15)
    r.raise_for_status()
    check_asp_session(r.text)
    if not view:
        return r.text
    text = force_agenda_view(s, r, view, username, num_dat)
    check_asp_session(text)
    return text


AGENDA_VIEW_OPTION_RE = re.compile(r'<option\s+value="([^"]+\.xsl)"[^>]*>(.*?)</option>', re.IGNORECASE | re.DOTALL)


def probe_agenda_views(s: requests.Session, agenda_url: str, username: str | None = None) -> list:
    """Diagnostic: render the same week in every TypVis view PASS offers and report what
    parse_events() can get out of each, saving every page for inspection. Which view
    carries the inline "08H00-09H15" time is the whole question behind AGENDA_VIEW, and
    it can only be answered against a live account."""
    r = s.get(agenda_url, timeout=15)
    r.raise_for_status()
    sel = AGENDA_VIEW_SELECT_RE.search(r.text)
    views = AGENDA_VIEW_OPTION_RE.findall(sel.group(0)) if sel else []
    if not views:
        dump_path = _dump_debug_page("view_select", r.text, username)
        raise RuntimeError(f"Sélecteur de vue (TypVis) introuvable sur la page agenda. Page brute sauvée dans {dump_path}.")

    results = []
    for value, label in views:
        try:
            text = force_agenda_view(s, r, value, username)
        except Exception as exc:  # a view that errors out is a result too, not a crash
            results.append({"view": value, "label": clean(label), "error": repr(exc),
                            "events": 0, "with_time": 0, "dump": ""})
            continue
        events = parse_events(text, username, dump=False)
        results.append({
            "view": value,
            "label": clean(label),
            "error": "",
            "events": len(events),
            "with_time": sum(1 for e in events if TIME_RE.match(e["time"])),
            "sample": next((f'{e["time"]} {e["title"][:40]}' for e in events if TIME_RE.match(e["time"])), ""),
            "dump": _save_page(f"view_{value.replace('.', '_')}", text),
        })
    return results


TABLE_ROW_RE = re.compile(r'<tr id="TableDatas".*?</tr>', re.IGNORECASE | re.DOTALL)
TABLE_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
TABLE_EVENT_ID_RE = re.compile(r"DetEve\('(\d+)'")
TABLE_DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}")
TABLE_HOUR_RE = re.compile(r"^\d{1,2}h\d{2}$", re.IGNORECASE)
# Titre | Type | Statut | Date(s) | Début | Fin | Description | Ressources (col 0 is the
# DetEve icon, col 9 the delete checkbox).
TABLE_COLS = {"title": 1, "type": 2, "statut": 3, "date": 4, "start": 5, "end": 6,
              "description": 7, "resources": 8}


def _cell_text(cell_html: str) -> str:
    return clean(html.unescape(re.sub(r"<[^>]+>", " ", cell_html)))


def parse_table_events(page: str, username: str | None = None) -> tuple:
    """The "Tableau" view: one <tr id="TableDatas"> per event, with real start/end times."""
    events, malformed = [], 0
    for row in TABLE_ROW_RE.findall(page):
        cells = [_cell_text(c) for c in TABLE_CELL_RE.findall(row)]
        id_m = TABLE_EVENT_ID_RE.search(row)
        if not id_m or len(cells) <= TABLE_COLS["resources"]:
            malformed += 1
            continue

        dates = TABLE_DATE_RE.findall(cells[TABLE_COLS["date"]])
        if not dates:
            malformed += 1
            continue
        if len(dates) > 1:
            # "Date(s)" can in principle list several occurrences of one event; they'd all
            # share this row's single id, which is the primary key of lessons_cache — so
            # emitting one event per date would silently collapse them. Never seen in
            # practice; log it rather than guess at a de-duplication scheme.
            logger.error("parse_table_events: event %s lists %d dates (%s), keeping the first — user=%s",
                         id_m.group(1), len(dates), cells[TABLE_COLS["date"]], username or "?")
        date = datetime.datetime.strptime(dates[0], "%d/%m/%Y").strftime("%Y%m%d")

        start, end = cells[TABLE_COLS["start"]], cells[TABLE_COLS["end"]]
        if TABLE_HOUR_RE.match(start) and TABLE_HOUR_RE.match(end):
            time_str = f"{start}-{end}".upper()
        else:
            time_str = ""  # all-day/undated entry: kept, but the PDF will skip it
            malformed += 1

        statut = cells[TABLE_COLS["statut"]]
        details = [cells[TABLE_COLS["type"]],
                   "" if statut.lower().startswith("actif") else statut,
                   cells[TABLE_COLS["description"]],
                   cells[TABLE_COLS["resources"]]]
        events.append({
            "id": id_m.group(1),
            "date": date,
            "time": time_str,
            "title": cells[TABLE_COLS["title"]],
            "details": [d for d in details if d],
        })
    return events, malformed


def parse_grid_events(page: str) -> tuple:
    """The calendar views (Jour / Semaine / Semaine complète): the time, when present at
    all, is the first <font size="1"> line inside the event cell. Kept as a fallback for
    an account or a PASS build still serving one of those to us."""
    events, malformed = [], 0
    for m in EVENT_RE.finditer(page):
        fields = [clean(f) for f in FONT_RE.findall(m.group("rest"))]
        time_str = fields[0] if fields else ""
        if not TIME_RE.match(time_str):
            malformed += 1
        events.append({
            "id": m.group("id"),
            "date": m.group("date"),  # YYYYMMDD
            "time": time_str,
            "title": clean(m.group("title")),
            "details": fields[1:],
        })
    return events, malformed


def parse_events(page: str, username: str | None = None, dump: bool = True) -> list:
    """Keeps every event PASS lists, even ones with a malformed/missing time —
    /lessons only ever reads time through the try/except'd helpers in lesson_utils,
    so a weird value there just shows blank, it doesn't crash. Only the PDF export's
    duration math (_time_bounds, unguarded) needs a real "HHHMM-HHHMM" time, so that
    filtering happens locally in export_pdf() instead of dropping the event here where
    it would also disappear from /lessons."""
    if TABLE_ROW_RE.search(page):
        events, malformed = parse_table_events(page, username)
    else:
        events, malformed = parse_grid_events(page)

    if dump and not events:
        # Zero matches at all (not just bad times) means neither parser fits the page:
        # an unexpected agenda view, an error/session page, or a real structure change.
        # Without a dump here that case is indistinguishable from a genuinely empty week.
        dump_path = _dump_debug_page("no_event", page, username)
        logger.error("parse_events: no event matched at all user=%s dump=%s", username or "?", dump_path)
    elif dump and malformed:
        dump_path = _dump_debug_page("event_time", page, username)
        logger.error("parse_events: %d event(s) with unparseable time user=%s dump=%s", malformed, username or "?", dump_path)
    return events


def week_bounds(ref: datetime.date | None = None) -> tuple:
    """(monday, sunday) as YYYYMMDD strings for the week containing `ref` (today by default)."""
    ref = ref or datetime.date.today()
    monday = ref - datetime.timedelta(days=ref.weekday())
    return monday.strftime("%Y%m%d"), (monday + datetime.timedelta(days=6)).strftime("%Y%m%d")


def filter_week(events: list, ref: datetime.date | None = None) -> list:
    """The Tableau view answers with a whole month; the attendance sheet is weekly, and
    export_pdf() derives its "Semaine du ... au ..." header from the min/max date of what
    it's handed — so narrow to one week before anything downstream sees the events."""
    lo, hi = week_bounds(ref)
    return [e for e in events if lo <= e["date"] <= hi]


def fetch_week_events(s: requests.Session, agenda_url: str, ref: datetime.date | None = None,
                      username: str | None = None) -> list:
    """Every event of the week containing `ref` (today by default), sorted. The Tableau
    view answers with the calendar month around NumDat, so a week straddling two months
    (e.g. Mon 28/09 - Sun 04/10) needs both months fetched and merged; events are keyed
    by their PASS id, which is stable across views and periods, so the overlap between
    the two months de-duplicates itself."""
    monday, sunday = week_bounds(ref)
    anchors = [monday] if monday[:6] == sunday[:6] else [monday, sunday]
    by_id = {}
    nom_cal = ""
    for anchor in anchors:
        page = fetch_agenda_html(s, agenda_url, username=username, num_dat=anchor)
        if not nom_cal:
            m = AGENDA_NOMCAL_RE.search(page)
            nom_cal = m.group(1) if m else ""
        for e in parse_events(page, username=username):
            by_id[e["id"]] = e
    events = filter_week(list(by_id.values()), ref)
    events.sort(key=lambda e: (e["date"], e["time"]))
    add_event_trainers(s, events, nom_cal, username=username)
    return events


AGENDA_NOMCAL_RE = re.compile(r'name="NomCal"\s+value="([^"]*)"', re.IGNORECASE)
EVENT_DETAIL_URL = f"{BASE}/Eplug/Agenda/Eve-Det.asp"
# Eve-Det.asp is what the agenda's hover popup (DetEve) loads: a script calling
# parent.MajDet('<TABLE>…</TABLE>'), i.e. the popup HTML inside a JS string literal —
# hence the \/ and \' escapes undone before matching. The trainers row reads
# "<B>4 Formateur(s)</B> : </TD><TD …>NAME<BR>NAME…</TD>".
TRAINERS_CELL_RE = re.compile(
    r"<B>\s*\d*\s*Formateur(?:\(s\)|s)?\s*</B>\s*:\s*</TD>\s*<TD[^>]*>(.*?)</TD>",
    re.IGNORECASE | re.DOTALL,
)
BR_RE = re.compile(r"<BR\s*/?>", re.IGNORECASE)


def parse_event_trainers(page: str) -> list:
    """Names listed under "Formateur(s)" in an Eve-Det.asp answer. Only that row is read:
    the same popup also lists every enrolled student ("95 Apprenant(s)"), which this app
    has no business keeping — so no debug dump of this page either."""
    text = page.replace("\\/", "/").replace("\\'", "'")
    m = TRAINERS_CELL_RE.search(text)
    if not m:
        return []
    return [name for name in (_cell_text(part) for part in BR_RE.split(m.group(1))) if name]


def fetch_event_trainers(s: requests.Session, event_id: str, date: str, nom_cal: str) -> list:
    r = s.get(EVENT_DETAIL_URL, params={"NumEve": event_id, "DatSrc": date, "NomCal": nom_cal}, timeout=15)
    r.raise_for_status()
    check_asp_session(r.text)
    return parse_event_trainers(r.text)


def add_event_trainers(s: requests.Session, events: list, nom_cal: str, username: str | None = None) -> None:
    """Sets e["teachers"] on every event. Neither the Tableau nor the calendar views carry
    the trainers; only the per-event popup does, so this costs one request per event.
    Sequential on purpose: classic ASP serializes requests sharing a session anyway.
    A failure leaves that event's list empty instead of failing the whole week."""
    for e in events:
        e["teachers"] = []
    if not nom_cal:
        logger.error("add_event_trainers: NomCal not found on the agenda page — user=%s", username or "?")
        return
    for e in events:
        try:
            e["teachers"] = fetch_event_trainers(s, e["id"], e["date"], nom_cal)
        except requests.RequestException as exc:
            logger.error("add_event_trainers: event %s failed (%r) — user=%s", e["id"], exc, username or "?")


DOSSIER_FRAME_RE = re.compile(r"addFrame\('frm0','(IMTA_DossierEtudiant\.opx\?[^']+)'")


def _simple_field(text: str, label_id: str) -> str:
    """Plain-text field value (e.g. Nom, Prénom) — these render as bare text with no
    nested tags, unlike the widget fields below."""
    m = re.search(rf'id="{label_id}"[^>]*>[^<]*</span><div class="form_fieldValue">\s*([^<]*)', text)
    return clean(html.unescape(m.group(1))) if m else ""


def _widget_field(text: str, field_code: str) -> str:
    """DirectoryContentList widget fields (Cursus, TAF) render their value as an
    `<a onclick="ouvrirDossierObjet(...)">label</a>` inside the field's mainDiv when
    set, and nothing when empty. Labels look like "ILSD* - INGÉNIERIE LOGICIELLE..." —
    only the short code before " - " is useful on the attendance sheet header."""
    m = re.search(rf'data-fieldcode="25_{field_code}">(.*?)</div>', text, re.DOTALL)
    if not m:
        return ""
    a_m = re.search(r"<a[^>]*>(.*?)</a>", m.group(1), re.DOTALL)
    if not a_m:
        return ""
    label = clean(html.unescape(a_m.group(1)))
    return label.split(" - ", 1)[0].rstrip("*").strip()


def fetch_dossier(s: requests.Session, username: str | None = None) -> dict:
    """Nom/Prénom/Cursus/TAF from the student's own Dossier Etudiant tab. The menu's
    "Ma Fiche" link redirects to Dossier.aspx with the session's GObjet/IdObjet/
    intIdUtilisateur context already set (it's always the logged-in user's own record);
    hitting Dossier.aspx directly without going through that link first 500s."""
    link = get_menu_link(s, "Ma Fiche", username=username)
    r = s.get(urljoin(BASE, link["url"]), timeout=15)
    r.raise_for_status()
    m = DOSSIER_FRAME_RE.search(r.text)
    if not m:
        dump_path = _dump_debug_page("dossier_frame", r.text, username)
        raise RuntimeError(
            f"Lien du cadre Dossier Étudiant introuvable — la page a peut-être changé. "
            f"Page brute sauvée dans {dump_path}."
        )
    frame_url = urljoin(r.url, html.unescape(m.group(1)))

    r2 = s.get(frame_url, timeout=15)
    r2.raise_for_status()
    text = r2.text

    return {
        "nom": _simple_field(text, "Lbl_NOM"),
        "prenom": _simple_field(text, "Lbl_PRE"),
        "formation": _widget_field(text, "CURS_PRI_INSC"),
        "taf": _widget_field(text, "IMTA_CHOIX_TAF2") or _widget_field(text, "IMTA_CHOIX_TAF1"),
    }


def group_by_day(events: list) -> dict:
    by_day = defaultdict(list)
    for e in events:
        by_day[e["date"]].append(e)
    for date in by_day:
        by_day[date].sort(key=lambda e: e["time"])
    return dict(sorted(by_day.items()))


def print_schedule(by_day: dict):
    for date, day_events in by_day.items():
        d = datetime.datetime.strptime(date, "%Y%m%d")
        print(f"\n=== {DAYS_FR[d.weekday()]} {d.strftime('%d/%m/%Y')} ===")
        for e in day_events:
            print(f"  {e['time']}  {e['title']}")
            if e.get("teachers"):
                print(f"      Formateur(s) : {', '.join(e['teachers'])}")
            if e["details"]:
                print(f"      {' | '.join(e['details'])}")


def main():
    ap = argparse.ArgumentParser(description="List this week's courses from pass.imt-atlantique.fr")
    ap.add_argument("--cookie", help="Raw Cookie header string")
    ap.add_argument("--cookie-file", help="File containing the raw Cookie header string")
    ap.add_argument("--login", metavar="USERNAME", help="SSO username; password is prompted locally (getpass)")
    ap.add_argument("--group-id", type=int, default=GROUP_ID)
    ap.add_argument("--json", action="store_true", help="Output JSON instead of text")
    ap.add_argument("--view", default=AGENDA_VIEW,
                    help="Agenda view to force (TypVis); empty string keeps the account's own preference")
    ap.add_argument("--probe-views", action="store_true",
                    help="Diagnostic: try every TypVis view and report which one yields parseable times")
    ap.add_argument("--all", action="store_true",
                    help="Keep every event PASS returns (a month in the Tableau view) instead of the current week")
    ap.add_argument("--week", type=int, default=0, metavar="N",
                    help="Week offset from the current one (1 = next week, -1 = last week)")
    args = ap.parse_args()

    if args.login:
        password = getpass.getpass(f"SSO password for {args.login}: ")
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0"})
        login(s, args.login, password)
    elif args.cookie or args.cookie_file:
        cookie_str = args.cookie or open(args.cookie_file, encoding="utf-8").read().strip()
        cookies = parse_cookie_string(cookie_str)
        s = get_session(cookies)
        check_session_alive(s)
    else:
        ap.error("provide --login, --cookie, or --cookie-file")

    agenda_link = get_agenda_link(s, args.group_id, username=args.login)
    agenda_url = bridge_to_classic_asp(s, agenda_link, username=args.login)
    if args.probe_views:
        for res in probe_agenda_views(s, agenda_url, username=args.login):
            print(f"{res['view']:<14} {res['label']:<18} events={res['events']:<4} avec horaire={res['with_time']:<4} "
                  f"{res.get('sample', '') or res['error']}")
            print(f"{'':<14} dump: {res['dump']}")
        return

    ref = datetime.date.today() + datetime.timedelta(weeks=args.week)
    if args.all:
        html_text = fetch_agenda_html(s, agenda_url, username=args.login, view=args.view,
                                      num_dat=ref.strftime("%Y%m%d"))
        events = parse_events(html_text, username=args.login)
    else:
        events = fetch_week_events(s, agenda_url, ref, username=args.login)
    by_day = group_by_day(events)

    if args.json:
        print(json.dumps(by_day, ensure_ascii=False, indent=2))
    else:
        print_schedule(by_day)
        print(f"\nTotal events: {len(events)}", file=sys.stderr)


if __name__ == "__main__":
    main()
