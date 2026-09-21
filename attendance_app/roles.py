"""Who administrates the site: one PASS login, named by the ADMIN_USERNAME environment
variable. Read at call time, not at import, so a container can be restarted with a new
value (and so tests can set it)."""
import os

from flask import session


def admin_username() -> str:
    return os.environ.get("ADMIN_USERNAME", "").strip()


def is_admin(username: str | None = None) -> bool:
    """Admin only ever means "logged in as ADMIN_USERNAME" — an unset variable means the
    site has no admin area at all, rather than everyone being one."""
    admin = admin_username()
    who = username if username is not None else session.get("username")
    return bool(admin and who and who.strip().casefold() == admin.casefold())
