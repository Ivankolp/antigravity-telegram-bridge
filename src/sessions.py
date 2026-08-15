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

    sessions: list[SessionInfo] = []
    for entry in bdir.iterdir():
        if not entry.is_dir():
            continue
        transcript = entry / ".system_generated" / "logs" / "transcript.jsonl"
        if not transcript.exists():
            continue

        conv_id = entry.name
        title = "Новый диалог"
        msg_count = 0
        mtime = transcript.stat().st_mtime

        try:
            with open(transcript, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
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
                                if match:
                                    raw_text = match.group(1).strip()
                                else:
                                    raw_text = content.strip()
                                if raw_text:
                                    clean = " ".join(raw_text.split())
                                    title = (clean[:35] + "…") if len(clean) > 35 else clean
                    except json.JSONDecodeError:
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

    # Sort newest first
    sessions.sort(key=lambda s: s.last_updated, reverse=True)
    return sessions[:limit]
