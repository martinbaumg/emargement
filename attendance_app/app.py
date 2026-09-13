#!/usr/bin/env python3
"""
Local demo "signed attendance log" app, built on top of pass_schedule.py.

Model:
- The STUDENT (account owner) logs in with their own PASS/SSO credentials (same
  login() flow already used for the personal schedule fetcher). Their lessons are
  fetched live from their own PASS agenda and cached locally.
- The app pre-fills the weekly "feuille d'émargement" PDF from those lessons and the
  student's profile. Both signature columns are left blank: the sheet is printed and
  signed by hand (teacher and student).

Local-only: binds to 127.0.0.1. The student's PASS password is used once to open
a session in server memory, never written to disk.

This module wires up the Flask app itself (secret key, DB init, session-cookie
persistence) and pulls in the route module for its side effect of registering
routes. See db.py, pass_session.py, lesson_utils.py, layout.py, csrf.py and
routes_student.py for the actual logic.
"""
import os
import secrets
import sys

from flask import Flask, request, session
from werkzeug.exceptions import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import layout
import pass_session

SECRET_KEY_PATH = os.path.join(db.DATA_DIR, ".secret_key")


def _load_or_create_secret_key() -> str:
    """A fixed key across reloads/restarts — regenerating it on every start (the old
    behavior) invalidated every browser's Flask session cookie on each reload, which
    silently dropped the login->token mapping and made LIVE_SESSIONS look like it
    wasn't persisting even once the DB-backed cache below was added."""
    if os.path.exists(SECRET_KEY_PATH):
        return open(SECRET_KEY_PATH, encoding="utf-8").read().strip()
    key = secrets.token_hex(32)
    fd = os.open(SECRET_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)
    return key


app = Flask(__name__)
app.secret_key = _load_or_create_secret_key()
app.jinja_env.globals["get_flashed_message"] = layout.get_flashed_message

db.init_db()  # idempotent (CREATE TABLE IF NOT EXISTS) — run at import time so the schema is
              # guaranteed current regardless of how/when the process was started or reloaded


@app.teardown_appcontext
def close_db(exc):
    db.close_db(exc)


@app.errorhandler(Exception)
def _log_exception_with_user(exc):
    """Flask's own default crash logging ("ERROR in app: Exception on /x") has no idea
    which student hit it, so a bug report is just a stack trace with no way to
    reproduce. Log the session's username + path ourselves and take over the plain
    500 response — 404s and other intentional HTTPExceptions pass through unchanged."""
    if isinstance(exc, HTTPException):
        return exc
    app.logger.error("Unhandled exception user=%s path=%s", session.get("username"), request.path, exc_info=exc)
    return "Internal Server Error", 500


@app.after_request
def _persist_live_session_cookies(response):
    """Keeps the `live_sessions` DB row current after every authenticated request, not
    just at login — PASS itself can rotate its ASP.NET session cookies mid-session, and
    a reload/restart must resume with whatever cookie jar is actually still valid."""
    token = session.get("token")
    username = session.get("username")
    if token and username and token in pass_session.LIVE_SESSIONS:
        pass_session.persist_live_session(db.get_db(), token, username, pass_session.LIVE_SESSIONS[token])
    return response


# Imported for side effect (route registration on `app`) — must come after `app` is
# defined above, since the route module does `from app import app`.
import routes_student  # noqa: E402,F401

# No `if __name__ == "__main__":` here on purpose: routes_student does
# `from app import app`, so running this file directly (`python3 app.py`) makes Python
# execute it a second time under the module name "app" to satisfy that import — routes
# then register on that shadow app instance while `app.run()` here would've served an
# empty one, 404ing on every route. Use run_local.py to start the dev server instead;
# it imports this module cleanly (no self-import) and only runs it.
