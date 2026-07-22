"""Dialog-mode session persistence.

Deliberately minimal: a single ongoing conversation (dialog mode's message
history) survives app restarts, stored as one JSON blob in a tiny SQLite
file under the user's cache directory. This is not a history browser or a
multi-session store — just enough so closing the window (or the app) and
coming back doesn't lose the conversation you were in.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "kortalk"
DB_FILE = CACHE_DIR / "session.sqlite3"


def _connect() -> sqlite3.Connection:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dialog_session ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), "
        "history TEXT NOT NULL)"
    )
    return conn


def load_dialog() -> list[dict]:
    """Best-effort: a missing or corrupt session file just means starting
    from an empty dialog, never a startup failure."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT history FROM dialog_session WHERE id = 1"
            ).fetchone()
        if row:
            history = json.loads(row[0])
            if isinstance(history, list):
                return history
    except (sqlite3.Error, json.JSONDecodeError, OSError):
        pass
    return []


def save_dialog(history: list[dict]) -> None:
    """Persistence is a nice-to-have here, not a requirement — a write
    failure (disk full, permissions) must never interrupt the dialog."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO dialog_session (id, history) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET history = excluded.history",
                (json.dumps(history, ensure_ascii=False),),
            )
    except (sqlite3.Error, OSError):
        pass


def clear_dialog() -> None:
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM dialog_session WHERE id = 1")
    except (sqlite3.Error, OSError):
        pass
