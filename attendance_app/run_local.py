#!/usr/bin/env python3
"""Local dev server entrypoint. `python3 run_local.py` (from attendance_app/).

Kept separate from app.py: routes_student.py does `from app
import app`, so running app.py itself as __main__ would make Python execute it a
second time under the module name "app" to satisfy that import — every route would
register on that shadow app instance while app.run() served an empty one, 404ing on
everything. This file imports app.py cleanly (as the single "app" module) and only
runs it, so no shadow copy is ever created.
"""
import atexit
import os
import sys

from app import app

LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".app.lock")


def acquire_lock_or_exit():
    """Refuse to start a second instance against the same attendance.db — running
    two at once is exactly what caused the intermittent 'no such table' errors
    (both writing to the same SQLite file). Stale locks (dead PID) auto-clear."""
    if os.path.exists(LOCK_PATH):
        try:
            old_pid = int(open(LOCK_PATH).read().strip())
        except (ValueError, OSError):
            old_pid = None
        if old_pid:
            try:
                os.kill(old_pid, 0)
            except OSError:
                pass  # stale lock, that process is dead
            else:
                print(f"Another instance is already running (PID {old_pid}).")
                print(f"Stop it first (kill {old_pid}), or delete {LOCK_PATH} if you're sure it's stale.")
                sys.exit(1)
    with open(LOCK_PATH, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(lambda: os.path.exists(LOCK_PATH) and os.remove(LOCK_PATH))


if __name__ == "__main__":
    # With the debug reloader, this script re-execs itself as a child process that
    # does the actual serving (WERKZEUG_RUN_MAIN=true); the initial parent process
    # just spawns and watches it, so only the child needs the lock.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        acquire_lock_or_exit()
    app.run(host="127.0.0.1", port=5001, debug=True)
