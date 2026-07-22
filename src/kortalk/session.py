"""Dialog-mode session persistence.

Deliberately minimal: each dialog-mode conversation is one row in a small
SQLite file under the user's cache directory — a title (derived from the
first message), the full message history as JSON, and a timestamp used to
order the session list newest-first. No manual rename, no folders or tags:
just enough to keep several conversations around and switch between them.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "kortalk"
DB_FILE = CACHE_DIR / "session.sqlite3"

_TITLE_MAX = 40


@dataclass
class SessionMeta:
    id: int
    title: str
    updated_at: str  # ISO 8601, UTC


def _connect() -> sqlite3.Connection:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dialog_sessions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "title TEXT NOT NULL, "
        "history TEXT NOT NULL, "
        "updated_at TEXT NOT NULL)"
    )
    _migrate_single_session(conn)
    return conn


def _migrate_single_session(conn: sqlite3.Connection) -> None:
    """Carries over the one conversation stored by the earlier, single-
    session version of this feature into the new table, then drops the old
    one — a silent one-time upgrade, nothing the user needs to act on."""
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='dialog_session'"
    ).fetchone()
    if not exists:
        return
    row = conn.execute("SELECT history FROM dialog_session WHERE id = 1").fetchone()
    if row:
        try:
            history = json.loads(row[0])
        except json.JSONDecodeError:
            history = None
        if history:
            conn.execute(
                "INSERT INTO dialog_sessions (title, history, updated_at) VALUES (?, ?, ?)",
                (_derive_title(history), json.dumps(history, ensure_ascii=False), _now()),
            )
    conn.execute("DROP TABLE dialog_session")
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _derive_title(history: list[dict]) -> str:
    for m in history:
        content = str(m.get("content", "")).strip() if m.get("role") == "user" else ""
        if content:
            text = " ".join(content.split())
            return text[:_TITLE_MAX] + ("…" if len(text) > _TITLE_MAX else "")
    return "Dialog"


def list_sessions() -> list[SessionMeta]:
    try:
        with closing(_connect()) as conn, conn:
            rows = conn.execute(
                "SELECT id, title, updated_at FROM dialog_sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [SessionMeta(id=r[0], title=r[1], updated_at=r[2]) for r in rows]
    except (sqlite3.Error, OSError):
        return []


def load_session(session_id: int) -> list[dict]:
    """Best-effort: a missing or corrupt row just means an empty dialog,
    never a startup failure."""
    try:
        with closing(_connect()) as conn, conn:
            row = conn.execute(
                "SELECT history FROM dialog_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row:
            history = json.loads(row[0])
            if isinstance(history, list):
                return history
    except (sqlite3.Error, json.JSONDecodeError, OSError):
        pass
    return []


def create_session(title: str, history: list[dict]) -> int | None:
    """Persistence is a nice-to-have here, not a requirement — a write
    failure (disk full, permissions) must never interrupt the dialog; the
    conversation just won't survive a restart or show up in the list."""
    try:
        with closing(_connect()) as conn, conn:
            cursor = conn.execute(
                "INSERT INTO dialog_sessions (title, history, updated_at) VALUES (?, ?, ?)",
                (title, json.dumps(history, ensure_ascii=False), _now()),
            )
            return cursor.lastrowid
    except (sqlite3.Error, OSError):
        return None


def save_session(session_id: int, history: list[dict]) -> None:
    try:
        with closing(_connect()) as conn, conn:
            conn.execute(
                "UPDATE dialog_sessions SET history = ?, updated_at = ? WHERE id = ?",
                (json.dumps(history, ensure_ascii=False), _now(), session_id),
            )
    except (sqlite3.Error, OSError):
        pass


def delete_session(session_id: int) -> None:
    try:
        with closing(_connect()) as conn, conn:
            conn.execute("DELETE FROM dialog_sessions WHERE id = ?", (session_id,))
    except (sqlite3.Error, OSError):
        pass
