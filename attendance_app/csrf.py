"""Per-browser-session CSRF token — see generate_csrf() for why login is the case that
actually needs this."""
import secrets

from flask import request, session


def generate_csrf() -> str:
    """Created once and reused for every form render — login is the one POST reachable
    by a logged-out visitor, so it's the one CSRF can target before there's even a PASS
    session to protect."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return session["csrf_token"]


def check_csrf() -> bool:
    submitted = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    return bool(expected) and secrets.compare_digest(submitted, expected)
