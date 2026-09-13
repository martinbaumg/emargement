"""SQLite schema, connection handling, and migrations for the attendance app."""
import os
import sqlite3

from flask import g

DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "attendance.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons_cache (
    lesson_id TEXT NOT NULL,
    owner_username TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    title TEXT NOT NULL,
    details_json TEXT NOT NULL,
    PRIMARY KEY (lesson_id, owner_username)
);
-- Freshness marker for the last live PASS agenda fetch per student, backing
-- fetch_events_cached()'s TTL check. The events themselves live in lessons_cache; this
-- table only records *when* that happened and which date range those rows cover, so
-- a cache hit can be reconstructed by re-reading lessons_cache instead of duplicating
-- the events in a second store. DB-backed (not an in-memory dict) so the cache is
-- shared across uwsgi's multiple worker processes instead of fragmented per-process.
CREATE TABLE IF NOT EXISTS events_cache_meta (
    owner_username TEXT NOT NULL,
    week_start TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    date_min TEXT NOT NULL,
    date_max TEXT NOT NULL,
    PRIMARY KEY (owner_username, week_start)
);
CREATE TABLE IF NOT EXISTS profiles (
    owner_username TEXT PRIMARY KEY,
    nom TEXT NOT NULL DEFAULT '',
    prenom TEXT NOT NULL DEFAULT '',
    formation TEXT NOT NULL DEFAULT '',
    taf TEXT NOT NULL DEFAULT '',
    campus TEXT NOT NULL DEFAULT '',
    apprentissage INTEGER NOT NULL DEFAULT 0,
    ue_table TEXT NOT NULL DEFAULT '',
    show_total_hours INTEGER NOT NULL DEFAULT 1
);
-- Sessions the student left off the PDF (e.g. « Travail Autonomie » slots, which need
-- no signature). Still listed on /lessons, only skipped by export_pdf.
CREATE TABLE IF NOT EXISTS lesson_exclusions (
    lesson_id TEXT NOT NULL,
    owner_username TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (lesson_id, owner_username)
);
-- Same, by exact course title: covers every session with that title, in every week.
CREATE TABLE IF NOT EXISTS title_exclusions (
    owner_username TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_username, title)
);
CREATE TABLE IF NOT EXISTS live_sessions (
    token TEXT PRIMARY KEY,
    owner_username TEXT NOT NULL,
    cookies_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _migrate_events_cache_meta(db):
    """events_cache_meta used to hold one row per student, back when only the current week
    was ever fetched; browsing to another week now needs its own freshness marker, so the
    PK gained a week_start column. The table is a pure cache marker (the events themselves
    live in lessons_cache), so the migration just drops the old shape rather than trying to
    guess which week those rows described — the cost is one extra live fetch per student."""
    cols = {r["name"] for r in db.execute("PRAGMA table_info(events_cache_meta)")}
    if not cols or "week_start" in cols:
        return
    db.executescript(
        "DROP TABLE events_cache_meta;"
        "CREATE TABLE events_cache_meta ("
        "  owner_username TEXT NOT NULL, week_start TEXT NOT NULL, fetched_at REAL NOT NULL,"
        "  date_min TEXT NOT NULL, date_max TEXT NOT NULL,"
        "  PRIMARY KEY (owner_username, week_start));"
    )
    db.commit()


def _migrate_profiles_show_total_hours(db):
    """CREATE TABLE IF NOT EXISTS doesn't add columns to an existing profiles table —
    add the "Total heures de formation" PDF switch in place, on by default so existing
    PDFs don't change."""
    cols = {r["name"] for r in db.execute("PRAGMA table_info(profiles)")}
    if not cols or "show_total_hours" in cols:
        return
    db.execute("ALTER TABLE profiles ADD COLUMN show_total_hours INTEGER NOT NULL DEFAULT 1")
    db.commit()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        # Self-heals if the db file was deleted/recreated/corrupted out from under a
        # running process (e.g. two instances of this app fighting over the same
        # file) — cheap enough (CREATE TABLE IF NOT EXISTS) to run every request.
        g.db.executescript(SCHEMA)
        g.db.commit()
        _migrate_events_cache_meta(g.db)
        _migrate_profiles_show_total_hours(g.db)
    return g.db


def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate_events_cache_meta(conn)
    _migrate_profiles_show_total_hours(conn)
    conn.close()
