"""Audience counter behind the footer's « visiteurs cette semaine », and every number the
/kpi dashboard is built from.

What is stored, per request: a timestamp, a random per-browser id, the endpoint, the
method, the status and whether the caller looked like a robot. No IP address, no user
agent, no login — the counter cannot tell who visited, only how many browsers did, which
is also what keeps this an exempt audience measurement rather than tracking. Rows older
than RETENTION_DAYS are dropped.
"""
import datetime
import os
import re
import secrets
import statistics
import time

from flask import request, session

import db as dbmod

RETENTION_DAYS = 400
# Endpoints that aren't a visit: assets, the health probe, and the guide's silent beacon.
SKIP_ENDPOINTS = {"static", "healthz", "fip_dsfr_stylesheet", "guide_seen"}
BOT_RE = re.compile(r"bot|crawler|crawl|spider|slurp|curl|wget|python-requests|headless|"
                    r"monitor|uptime|preview|scan|fetcher", re.IGNORECASE)
# Pages named as the dashboard names them, so the "top pages" table reads in French.
ENDPOINT_LABELS = {
    "lessons": "Cours de la semaine",
    "profile": "Profil",
    "export_pdf": "Téléchargement de la feuille",
    "login": "Connexion",
    "logout": "Déconnexion",
    "tickets": "Assistance",
    "admin_tickets": "Tickets reçus (admin)",
    "badges": "Palmarès",
    "kpi": "Tableau de bord d'audience",
    "lesson_exclusion": "Inclure / exclure une séance",
    "title_exclusion": "Exclure un cours",
    "lesson_ue_code": "Saisie d'un code UE",
    "profile_import": "Réimport du profil depuis PASS",
    "profile_ue_import": "Import des codes UE depuis PASS",
    "index": "Redirection d'accueil",
    "(inconnu)": "Page introuvable (404)",
}


def _is_bot() -> bool:
    return bool(BOT_RE.search(request.headers.get("User-Agent", "")))


def ensure_visitor() -> str | None:
    """Gives this browser a random id, kept in the signed Flask session cookie. Called on
    every request (before_request) rather than at first render, so the footer can count the
    visit being served — the hit row itself is only written once the response exists.
    Robots get no id at all: they don't keep cookies, so an id per hit would inflate every
    visitor count on the dashboard."""
    if _is_bot():
        return None
    if not session.get("vid"):
        session["vid"] = secrets.token_hex(8)
        session.permanent = False
    return session["vid"]


def _maybe_prune(db) -> None:
    """Retention, applied on roughly one write in two hundred — often enough that the table
    stays bounded, rarely enough that no single request pays for it."""
    if secrets.randbelow(200):
        return
    db.execute("DELETE FROM site_hits WHERE ts < ?", (time.time() - RETENTION_DAYS * 86400,))


def record(response):
    """One row per served page. Never raises into the response path: a counter that breaks
    the site it counts is worse than no counter."""
    endpoint = request.endpoint
    if endpoint in SKIP_ENDPOINTS:
        return response
    try:
        db = dbmod.get_db()
        db.execute(
            "INSERT INTO site_hits (ts, visitor, endpoint, method, status, is_bot) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), session.get("vid") or "", endpoint or "(inconnu)", request.method,
             response.status_code, int(_is_bot())),
        )
        _maybe_prune(db)
        db.commit()
    except Exception:  # noqa: BLE001 — see docstring
        pass
    return response


# ------------------------------------------------------------------ reading it back

def _start_of_day(d: datetime.date) -> float:
    return datetime.datetime.combine(d, datetime.time.min).timestamp()


def _week_start(offset_weeks: int = 0) -> float:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday(), weeks=-offset_weeks)
    return _start_of_day(monday)


def _visitors(db, since: float, until: float | None = None) -> int:
    sql = "SELECT count(DISTINCT visitor) FROM site_hits WHERE is_bot=0 AND visitor!='' AND ts>=?"
    params = [since]
    if until is not None:
        sql += " AND ts<?"
        params.append(until)
    return db.execute(sql, params).fetchone()[0]


def _views(db, since: float | None = None) -> int:
    if since is None:
        return db.execute("SELECT count(*) FROM site_hits WHERE is_bot=0").fetchone()[0]
    return db.execute("SELECT count(*) FROM site_hits WHERE is_bot=0 AND ts>=?", (since,)).fetchone()[0]


def _visitors_with_me(db, since: float) -> int:
    """Visitors since `since`, the browser being served included — its own hit row is only
    written once this page has been rendered, so counting rows alone would tell a site's
    very first visitor that there are « 0 visiteur cette semaine » while they stand on the
    page. Robots have no id and stay out of it."""
    vid = session.get("vid")
    if not vid:
        return _visitors(db, since)
    others = db.execute(
        "SELECT count(DISTINCT visitor) FROM site_hits "
        "WHERE is_bot=0 AND visitor!='' AND visitor!=? AND ts>=?", (vid, since)).fetchone()[0]
    return others + 1


def weekly_visitors() -> int:
    """The footer's number, on every page."""
    try:
        return _visitors_with_me(dbmod.get_db(), _week_start())
    except Exception:  # noqa: BLE001
        return 0


def _daily(db, days: int) -> list:
    """[{day, label, views, visitors}] for the last `days` days, empty days included — a
    gap in a trend line must read as a quiet day, not as a shorter month."""
    first = datetime.date.today() - datetime.timedelta(days=days - 1)
    rows = {r["d"]: r for r in db.execute(
        "SELECT strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime') AS d, count(*) AS views, "
        "count(DISTINCT visitor) AS visitors FROM site_hits WHERE is_bot=0 AND ts>=? GROUP BY d",
        (_start_of_day(first),))}
    series = []
    for i in range(days):
        day = first + datetime.timedelta(days=i)
        key = day.strftime("%Y-%m-%d")
        row = rows.get(key)
        series.append({"day": key, "label": day.strftime("%d/%m"),
                       "views": row["views"] if row else 0,
                       "visitors": row["visitors"] if row else 0})
    return series


def _trend(values: list) -> tuple:
    """(slope per day, intercept, R²) by least squares — the honest maths under the
    five-year projection the dashboard then prints without any honesty at all."""
    n = len(values)
    if n < 2:
        return 0.0, float(values[0] if values else 0), 0.0
    xs = list(range(n))
    mx, my = sum(xs) / n, sum(values) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if not sxx:
        return 0.0, my, 0.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, values)) / sxx
    intercept = my - slope * mx
    syy = sum((y - my) ** 2 for y in values)
    r2 = 0.0 if not syy else max(0.0, min(1.0, 1 - sum(
        (y - (intercept + slope * x)) ** 2 for x, y in zip(xs, values)) / syy))
    return slope, intercept, r2


_LOC = None


def _lines_of_code() -> int:
    """Lines of Python and templates behind all this, counted once per process. It exists
    for one KPI, and that KPI exists for one joke."""
    global _LOC
    if _LOC is None:
        app_dir = os.path.dirname(os.path.abspath(__file__))
        paths = [os.path.join(app_dir, n) for n in os.listdir(app_dir) if n.endswith(".py")]
        paths.append(os.path.join(os.path.dirname(app_dir), "pass_schedule.py"))
        total = 0
        for path in paths:
            try:
                with open(path, encoding="utf-8") as f:
                    total += sum(1 for _ in f)
            except OSError:
                pass
        _LOC = total
    return _LOC


def _hours_label(minutes: float) -> str:
    minutes = round(minutes)
    return f"{int(minutes // 60)}h{int(minutes % 60):02d}"


def dashboard() -> dict:
    """Every figure on /kpi. Deliberately computed in one place: the page is a joke, the
    numbers are not — each one is a real count of something that really happened."""
    db = dbmod.get_db()
    now = time.time()
    today = _start_of_day(datetime.date.today())
    week, prev_week = _week_start(), _week_start(-1)
    month = _start_of_day(datetime.date.today() - datetime.timedelta(days=29))

    visitors_total = _visitors_with_me(db, 0)
    visitors_week = _visitors_with_me(db, week)
    visitors_prev = _visitors(db, prev_week, week)
    views_week = _views(db, week)
    first_ts = db.execute("SELECT min(ts) FROM site_hits").fetchone()[0] or now
    days_online = max(1, int((now - first_ts) // 86400) + 1)

    daily = _daily(db, 30)
    daily_visitors = [d["visitors"] for d in daily]
    slope, intercept, r2 = _trend(daily_visitors)
    # Where that straight line lands on the same day five years from now. The line is real
    # least squares; believing it is the reader's problem.
    projection = max(0.0, intercept + slope * (len(daily) - 1 + 5 * 365))

    hours = {int(r["h"]): r["n"] for r in db.execute(
        "SELECT strftime('%H', ts, 'unixepoch', 'localtime') AS h, count(*) AS n "
        "FROM site_hits WHERE is_bot=0 GROUP BY h")}
    by_hour = [{"hour": h, "views": hours.get(h, 0)} for h in range(24)]
    peak_hour = max(by_hour, key=lambda b: b["views"])

    weekday_rows = {int(r["w"]): r["n"] for r in db.execute(
        "SELECT strftime('%w', ts, 'unixepoch', 'localtime') AS w, count(*) AS n "
        "FROM site_hits WHERE is_bot=0 GROUP BY w")}
    weekday_names = ["Dimanche", "Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi"]
    peak_weekday = max(range(7), key=lambda w: weekday_rows.get(w, 0))

    top_pages = []
    total_views = _views(db) or 1
    for r in db.execute("SELECT endpoint, count(*) AS n FROM site_hits WHERE is_bot=0 "
                        "GROUP BY endpoint ORDER BY n DESC LIMIT 8"):
        top_pages.append({"label": ENDPOINT_LABELS.get(r["endpoint"], r["endpoint"]),
                          "views": r["n"], "share": round(100 * r["n"] / total_views, 1)})

    one_page = db.execute(
        "SELECT count(*) FROM (SELECT visitor FROM site_hits WHERE is_bot=0 AND visitor!='' AND ts>=? "
        "GROUP BY visitor HAVING count(*)=1)", (week,)).fetchone()[0]
    loyal = db.execute(
        "SELECT count(*) FROM (SELECT visitor FROM site_hits WHERE is_bot=0 AND visitor!='' "
        "GROUP BY visitor HAVING count(DISTINCT strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime'))>=2)"
    ).fetchone()[0]
    newcomers = db.execute(
        "SELECT count(*) FROM (SELECT visitor FROM site_hits WHERE is_bot=0 AND visitor!='' "
        "GROUP BY visitor HAVING min(ts)>=?)", (week,)).fetchone()[0]

    pdfs = db.execute("SELECT count(*) FROM site_hits WHERE endpoint='export_pdf' AND status=200").fetchone()[0]
    logins_ok = db.execute("SELECT count(*) FROM site_hits WHERE endpoint='login' AND method='POST' "
                           "AND status>=300 AND status<400").fetchone()[0]
    logins_ko = db.execute("SELECT count(*) FROM site_hits WHERE endpoint='login' AND method='POST' "
                           "AND status=200").fetchone()[0]
    refreshes = db.execute("SELECT count(*) FROM events_cache_meta").fetchone()[0]
    bots_week = db.execute("SELECT count(*) FROM site_hits WHERE is_bot=1 AND ts>=?", (week,)).fetchone()[0]
    not_found = db.execute("SELECT count(*) FROM site_hits WHERE status=404").fetchone()[0]

    accounts = db.execute("SELECT count(*) FROM profiles").fetchone()[0]
    cached_lessons = db.execute("SELECT count(*) FROM lessons_cache").fetchone()[0]
    distinct_titles = db.execute("SELECT count(DISTINCT title) FROM lessons_cache").fetchone()[0]
    tickets_total = db.execute("SELECT count(*) FROM tickets").fetchone()[0]
    tickets_open = db.execute("SELECT count(*) FROM tickets WHERE status!='traite'").fetchone()[0]
    replied = [(datetime.datetime.fromisoformat(r["updated_at"]) - datetime.datetime.fromisoformat(r["created_at"]))
               for r in db.execute("SELECT created_at, updated_at FROM tickets WHERE reply!=''")]
    reply_hours = round(sum(d.total_seconds() for d in replied) / len(replied) / 3600, 1) if replied else None

    # Weekly target: the next round number above where we are. Always just out of reach,
    # like every quarterly objective ever written.
    target = max(10, 10 * (visitors_week // 10 + 1))

    return {
        "visitors_week": visitors_week,
        "visitors_prev": visitors_prev,
        "visitors_delta": round(100 * (visitors_week - visitors_prev) / visitors_prev) if visitors_prev else None,
        "visitors_today": _visitors_with_me(db, today),
        "visitors_month": _visitors_with_me(db, month),
        "visitors_total": visitors_total,
        "views_today": _views(db, today),
        "views_week": views_week,
        "views_total": _views(db),
        "views_per_visitor": round(views_week / visitors_week, 2) if visitors_week else 0,
        "views_per_day": round(_views(db) / days_online, 1),
        "bounce_rate": round(100 * one_page / visitors_week) if visitors_week else 0,
        "loyal": loyal,
        "newcomers": newcomers,
        "returning_rate": round(100 * loyal / max(1, visitors_total)),
        "daily": daily,
        "daily_max": max([d["views"] for d in daily] + [1]),
        "daily_stdev": round(statistics.pstdev(daily_visitors), 2) if len(daily_visitors) > 1 else 0,
        "daily_mean": round(statistics.fmean(daily_visitors), 2) if daily_visitors else 0,
        "best_day": max(daily, key=lambda d: d["views"]) if daily else None,
        "quiet_days": sum(1 for d in daily if not d["views"]),
        "by_hour": by_hour,
        "hour_max": max([b["views"] for b in by_hour] + [1]),
        "peak_hour": peak_hour,
        "peak_weekday": weekday_names[peak_weekday],
        "top_pages": top_pages,
        "days_online": days_online,
        "since_label": datetime.datetime.fromtimestamp(first_ts).strftime("%d/%m/%Y"),
        "slope": round(slope, 3),
        "r2": round(r2, 3),
        "projection": round(projection, 1),
        "projection_year": round(projection * 365),
        "target": target,
        "target_progress": min(100, round(100 * visitors_week / target)),
        "pdfs": pdfs,
        "logins_ok": logins_ok,
        "logins_ko": logins_ko,
        "login_rate": round(100 * logins_ok / (logins_ok + logins_ko)) if (logins_ok + logins_ko) else 100,
        "refreshes": refreshes,
        "bots_week": bots_week,
        "not_found": not_found,
        "accounts": accounts,
        "cached_lessons": cached_lessons,
        "distinct_titles": distinct_titles,
        "tickets_total": tickets_total,
        "tickets_open": tickets_open,
        "reply_hours": reply_hours,
        "lines_of_code": _lines_of_code(),
        "loc_per_visitor": round(_lines_of_code() / max(1, visitors_total), 1),
        # A sheet takes about twelve minutes to fill by hand. Multiply, present as a saving,
        # never mention that it is a multiplication.
        "time_saved": _hours_label(pdfs * 12),
        "paper": pdfs,
        "trees": round(pdfs / 8333, 5),
        "valuation": visitors_total * 1000,
        "campus_share": round(100 * min(1.0, visitors_total / 1200), 1),
        "generated_at": datetime.datetime.now().strftime("%d/%m/%Y à %H:%M"),
        "quarter": f"T{(datetime.date.today().month - 1) // 3 + 1} {datetime.date.today().year}",
    }
