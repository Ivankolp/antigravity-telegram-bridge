"""Helpers for listing, inspecting, and managing agy sessions (conversations)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_BRAIN_DIR = Path.home() / ".gemini" / "antigravity-cli" / "brain"


@dataclass
class SessionInfo:
    conversation_id: str
    title: str
    last_updated: float
    message_count: int
    created_at_str: str


def list_sessions(brain_dir: Path | None = None, limit: int = 10) -> list[SessionInfo]:
    bdir = brain_dir or DEFAULT_BRAIN_DIR
    if not bdir.exists():
        return []

    # Get directory candidates sorted by mtime first to avoid reading all transcripts
    try:
        entries = [e for e in bdir.iterdir() if e.is_dir()]
        entries.sort(key=lambda e: e.stat().st_mtime, reverse=True)
    except Exception:
        return []

    sessions: list[SessionInfo] = []
    for entry in entries:
        if len(sessions) >= limit:
            break
        transcript = entry / ".system_generated" / "logs" / "transcript.jsonl"
        if not transcript.exists():
            continue

        conv_id = entry.name
        title = "Новый диалог"
        msg_count = 0
        try:
            mtime = transcript.stat().st_mtime
        except Exception:
            continue

        try:
            with open(transcript, "r", encoding="utf-8", errors="replace") as f:
                # Read only up to first 100 lines to find the initial user request
                for line_idx, line in enumerate(f):
                    if line_idx > 100 and title != "Новый диалог":
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        step = json.loads(line)
                        if step.get("type") == "USER_INPUT":
                            msg_count += 1
                            if title == "Новый диалог":
                                content = step.get("content", "")
                                match = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", content, re.DOTALL)
                                raw_text = match.group(1).strip() if match else content.strip()
                                if raw_text:
                                    clean = " ".join(raw_text.split())
                                    title = (clean[:35] + "…") if len(clean) > 35 else clean
                    except Exception:
                        continue
        except Exception:
            pass

        dt = datetime.fromtimestamp(mtime)
        sessions.append(
            SessionInfo(
                conversation_id=conv_id,
                title=title,
                last_updated=mtime,
                message_count=msg_count,
                created_at_str=dt.strftime("%d.%m %H:%M"),
            )
        )

    return sessions


def get_active_session_info(conversation_id: str = "", brain_dir: Path | None = None) -> SessionInfo | None:
    """Find SessionInfo for conversation_id or return the latest active session."""
    bdir = brain_dir or DEFAULT_BRAIN_DIR
    if conversation_id and bdir.exists():
        direct = bdir / conversation_id / ".system_generated" / "logs" / "transcript.jsonl"
        if direct.exists():
            title = "Новый диалог"
            mtime = 0.0
            msg_count = 0
            try:
                mtime = direct.stat().st_mtime
                with open(direct, "r", encoding="utf-8", errors="replace") as f:
                    for line_idx, line in enumerate(f):
                        if line_idx > 60 and title != "Новый диалог":
                            break
                        line = line.strip()
                        if not line:
                            continue
                        step = json.loads(line)
                        if step.get("type") == "USER_INPUT":
                            msg_count += 1
                            if title == "Новый диалог":
                                content = step.get("content", "")
                                match = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", content, re.DOTALL)
                                raw_text = match.group(1).strip() if match else content.strip()
                                if raw_text:
                                    clean = " ".join(raw_text.split())
                                    title = (clean[:35] + "…") if len(clean) > 35 else clean
            except Exception:
                pass
            dt = datetime.fromtimestamp(mtime) if mtime else datetime.now()
            return SessionInfo(
                conversation_id=conversation_id,
                title=title,
                last_updated=mtime,
                message_count=msg_count,
                created_at_str=dt.strftime("%d.%m %H:%M"),
            )

    sessions = list_sessions(brain_dir, limit=5)
    if not sessions:
        if conversation_id:
            return SessionInfo(
                conversation_id=conversation_id,
                title=f"Сессия {conversation_id[:8]}…",
                last_updated=0,
                message_count=0,
                created_at_str="",
            )
        return None
    if conversation_id:
        for s in sessions:
            if s.conversation_id == conversation_id:
                return s
        return SessionInfo(
            conversation_id=conversation_id,
            title=f"Сессия {conversation_id[:8]}…",
            last_updated=0,
            message_count=0,
            created_at_str="",
        )
    return sessions[0]
