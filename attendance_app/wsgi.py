"""
uWSGI entry point. Mounts the Flask app under BASE_URL_PATH (e.g. "/emargement") when
set, so it can sit behind a reverse proxy that forwards a sub-path — DispatcherMiddleware
sets SCRIPT_NAME for the mounted app, so url_for() and redirects come out correctly
prefixed with no other code changes needed. Empty/unset BASE_URL_PATH serves at "/", same
as running the Flask app directly.
"""
import os

from werkzeug.exceptions import NotFound
from werkzeug.middleware.dispatcher import DispatcherMiddleware

from app import app as flask_app

BASE_URL_PATH = os.environ.get("BASE_URL_PATH", "").strip()
if BASE_URL_PATH and not BASE_URL_PATH.startswith("/"):
    BASE_URL_PATH = "/" + BASE_URL_PATH
BASE_URL_PATH = BASE_URL_PATH.rstrip("/")

if BASE_URL_PATH:
    application = DispatcherMiddleware(NotFound(), {BASE_URL_PATH: flask_app})
else:
    application = flask_app
