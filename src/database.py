"""SQLite Database with WAL mode for Antigravity Telegram Bridge (Python)."""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from src.state import ChatState, State

logger = logging.getLogger("bridge.database")


_DB_CACHE: dict[str, Database] = {}


def get_database(db_path: Path) -> Database:
    resolved = str(db_path.resolve())
    if resolved not in _DB_CACHE:
        _DB_CACHE[resolved] = Database(db_path)
    return _DB_CACHE[resolved]


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                timeout=10.0,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            # Enable WAL mode and performance pragmas
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_connection()
        with conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS chat_states (
                    chat_id INTEGER PRIMARY KEY,
                    chat_dir TEXT NOT NULL,
                    has_session INTEGER NOT NULL DEFAULT 0,
                    model TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT '',
                    effort TEXT NOT NULL DEFAULT '',
                    photo_enabled INTEGER NOT NULL DEFAULT 1,
                    streaming INTEGER NOT NULL DEFAULT 1,
                    verbose_actions INTEGER NOT NULL DEFAULT 1,
                    turn_count INTEGER NOT NULL DEFAULT 0,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS system_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS turn_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    prompt TEXT,
                    reply TEXT,
                    model TEXT,
                    duration_ms INTEGER,
                    exit_code INTEGER,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_turns_chat ON turn_logs(chat_id, created_at);
            """)
            # Schema migrations for existing databases
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(chat_states)")
            cols = {row["name"] for row in cur.fetchall()}
            if "streaming" not in cols:
                conn.execute("ALTER TABLE chat_states ADD COLUMN streaming INTEGER NOT NULL DEFAULT 1")
            if "verbose_actions" not in cols:
                conn.execute("ALTER TABLE chat_states ADD COLUMN verbose_actions INTEGER NOT NULL DEFAULT 1")
        logger.info(f"SQLite database initialized at {self.db_path} (WAL mode)")

    def get_last_update_id(self) -> int:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT value FROM system_state WHERE key = 'last_update_id'")
        row = cur.fetchone()
        if row:
            try:
                return int(row["value"])
            except (ValueError, TypeError):
                return 0
        return 0

    def set_last_update_id(self, update_id: int) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute(
                "INSERT INTO system_state (key, value) VALUES ('last_update_id', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(update_id),),
            )

    def get_chat_state(self, chat_id: int, chats_root: Path) -> ChatState:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT chat_dir, has_session, model, mode, effort, photo_enabled, streaming, verbose_actions, turn_count, conversation_id
            FROM chat_states WHERE chat_id = ?
        """, (chat_id,))
        row = cur.fetchone()
        if row:
            from src.state import _safe_effort, _safe_mode, _safe_model, _safe_turn_count
            return ChatState(
                chat_dir=row["chat_dir"],
                has_session=bool(row["has_session"]),
                model=_safe_model(row["model"]),
                mode=_safe_mode(row["mode"]),
                effort=_safe_effort(row["effort"]),
                photo_enabled=bool(row["photo_enabled"]),
                streaming=bool(row["streaming"] if "streaming" in row.keys() else 1),
                verbose_actions=bool(row["verbose_actions"] if "verbose_actions" in row.keys() else 1),
                turn_count=_safe_turn_count(row["turn_count"]),
                conversation_id=row["conversation_id"] or "",
            )
        # Default fresh state
        default_dir = chats_root / str(chat_id)
        default_dir.mkdir(parents=True, exist_ok=True)
        cs = ChatState(
            chat_dir=str(default_dir),
            has_session=False,
            mode="",
            photo_enabled=True,
            streaming=True,
            verbose_actions=True,
        )
        self.save_chat_state(chat_id, cs)
        return cs

    def save_chat_state(self, chat_id: int, cs: ChatState) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute("""
                INSERT INTO chat_states (
                    chat_id, chat_dir, has_session, model, mode, effort,
                    photo_enabled, streaming, verbose_actions, turn_count, conversation_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    chat_dir = excluded.chat_dir,
                    has_session = excluded.has_session,
                    model = excluded.model,
                    mode = excluded.mode,
                    effort = excluded.effort,
                    photo_enabled = excluded.photo_enabled,
                    streaming = excluded.streaming,
                    verbose_actions = excluded.verbose_actions,
                    turn_count = excluded.turn_count,
                    conversation_id = excluded.conversation_id,
                    updated_at = excluded.updated_at
            """, (
                chat_id,
                cs.chat_dir,
                1 if cs.has_session else 0,
                cs.model or "",
                cs.mode or "",
                cs.effort or "",
                1 if cs.photo_enabled else 0,
                1 if getattr(cs, "streaming", True) else 0,
                1 if getattr(cs, "verbose_actions", True) else 0,
                cs.turn_count,
                cs.conversation_id or "",
                time.time(),
            ))

    def log_turn(
        self,
        chat_id: int,
        prompt: str,
        reply: str,
        model: str,
        duration_ms: int,
        exit_code: int,
    ) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute("""
                INSERT INTO turn_logs (
                    chat_id, prompt, reply, model, duration_ms, exit_code, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                chat_id,
                prompt[:10000],
                reply[:10000],
                model,
                duration_ms,
                exit_code,
                time.time(),
            ))

    def migrate_from_json_if_empty(self, json_path: Path, chats_root: Path) -> None:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM chat_states")
        if cur.fetchone()["cnt"] > 0:
            return  # already populated

        if not json_path.exists():
            return

        try:
            raw = json.loads(json_path.read_text())
        except Exception as e:
            logger.warning(f"Could not read state.json for migration: {e}")
            return

        last_id = int(raw.get("last_update_id", 0))
        if last_id:
            self.set_last_update_id(last_id)

        chats_raw = raw.get("chats") or {}
        from src.state import _safe_chat_state
        for k, v in chats_raw.items():
            try:
                cid = int(k)
                cs = _safe_chat_state(chats_root, v)
                if cs is not None:
                    self.save_chat_state(cid, cs)
                    logger.info(f"Migrated chat {cid} from state.json to SQLite")
            except Exception as ex:
                logger.warning(f"Error migrating chat {k}: {ex}")
