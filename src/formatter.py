"""Tool and event formatter for Antigravity Telegram Bridge.

Inspired by opencode-telegram-bot formatting style.
Provides clean icons, path normalization, diff summaries, and truncated command strings.
"""
from __future__ import annotations

import html
import os
import re
from pathlib import Path
from typing import Any

from src.i18n import t


def normalize_path_for_display(file_path: str, base_dir: str = "") -> str:
    """Make paths relative to project root or user home directory."""
    if not file_path:
        return ""
    p = Path(file_path)
    if base_dir:
        try:
            rel = p.resolve().relative_to(Path(base_dir).resolve())
            return str(rel)
        except (ValueError, OSError):
            pass
    home = os.path.expanduser("~")
    norm = str(p)
    if norm.startswith(home):
        return "~" + norm[len(home):]
    return norm


def get_tool_icon(tool_name: str) -> str:
    """Return an aesthetic emoji icon matching the tool."""
    mapping = {
        "view_file": "📖",
        "read": "📖",
        "write_to_file": "✍️",
        "write": "✍️",
        "replace_file_content": "✏️",
        "edit": "✏️",
        "run_command": "💻",
        "bash": "💻",
        "list_dir": "📁",
        "directory_analysis": "📁",
        "search_web": "🌐",
        "web_search": "🔎",
        "read_url_content": "🌐",
        "grep_search": "🔍",
        "code_search": "🔍",
        "invoke_subagent": "🤖",
        "task": "🤖",
        "generate_image": "🎨",
        "schedule": "⏰",
        "ask_question": "❓",
    }
    return mapping.get(tool_name.lower(), "🛠️")


def count_lines(text: str) -> int:
    """Count non-empty lines in text."""
    if not text:
        return 0
    return len(text.splitlines())


def truncate_command(cmd: str, max_len: int = 70) -> str:
    """Truncate shell command nicely with ellipsis."""
    cmd = " ".join(cmd.split())
    if len(cmd) <= max_len:
        return cmd
    return cmd[:max_len - 3].rstrip() + "..."


def format_tool_summary(
    tool_name: str,
    params: dict[str, Any],
    base_dir: str = "",
    lang: str = "ru",
) -> tuple[str, str, dict[str, Any]]:
    """Format tool execution into (icon, short_line, metadata).

    short_line is used for live compact progress streaming.
    metadata includes file paths, diff stats (+lines, -lines), etc.
    """
    icon = get_tool_icon(tool_name)
    meta: dict[str, Any] = {
        "tool": tool_name,
        "is_file_change": False,
        "file_path": "",
        "additions": 0,
        "deletions": 0,
    }

    if tool_name in ("view_file", "read"):
        path = str(params.get("AbsolutePath") or params.get("path") or "")
        disp = normalize_path_for_display(path, base_dir)
        meta["file_path"] = path
        short = f"{icon} read {disp}"
        return icon, short, meta

    elif tool_name in ("write_to_file", "write"):
        path = str(params.get("TargetFile") or params.get("path") or "")
        disp = normalize_path_for_display(path, base_dir)
        content = str(params.get("CodeContent") or params.get("content") or "")
        lines = count_lines(content)
        meta["is_file_change"] = True
        meta["file_path"] = path
        meta["additions"] = lines
        short = f"{icon} write {disp} (+{lines})"
        return icon, short, meta

    elif tool_name in ("replace_file_content", "edit"):
        path = str(params.get("TargetFile") or params.get("path") or "")
        disp = normalize_path_for_display(path, base_dir)
        target = str(params.get("TargetContent") or "")
        replace = str(params.get("ReplacementContent") or "")
        del_lines = count_lines(target)
        add_lines = count_lines(replace)
        meta["is_file_change"] = True
        meta["file_path"] = path
        meta["additions"] = add_lines
        meta["deletions"] = del_lines
        short = f"{icon} edit {disp} (+{add_lines} -{del_lines})"
        return icon, short, meta

    elif tool_name in ("run_command", "bash"):
        cmd = str(params.get("CommandLine") or params.get("command") or "").strip()
        trunc = truncate_command(cmd, 60)
        short = f"{icon} bash: {trunc}"
        return icon, short, meta

    elif tool_name in ("search_web", "web_search"):
        q = str(params.get("query") or "").strip()
        short = f"{icon} search: {truncate_command(q, 45)}"
        return icon, short, meta

    elif tool_name in ("grep_search", "code_search"):
        q = str(params.get("Query") or params.get("pattern") or "").strip()
        short = f"{icon} grep: {truncate_command(q, 45)}"
        return icon, short, meta

    elif tool_name in ("list_dir", "directory_analysis"):
        path = str(params.get("DirectoryPath") or params.get("path") or "").strip()
        disp = normalize_path_for_display(path, base_dir)
        short = f"{icon} ls {disp}"
        return icon, short, meta

    elif tool_name in ("invoke_subagent", "task"):
        role = str(params.get("Role") or "Subagent").strip()
        short = f"{icon} agent: {role}"
        return icon, short, meta

    else:
        # Fallback to summary / action
        summary = str(params.get("toolSummary") or params.get("toolAction") or tool_name).strip()
        short = f"{icon} {truncate_command(summary, 50)}"
        return icon, short, meta


def format_thinking_block(raw_thought: str, lang: str = "ru") -> str:
    """Format internal chain-of-thought into expandable HTML blockquote."""
    clean = raw_thought.strip()
    if not clean:
        return ""
    header = t("progress.thinking_header", lang)
    escaped = html.escape(clean)
    return f'<blockquote expandable><b>{header}</b>\n{escaped}</blockquote>\n\n'


def format_run_footer(
    mode: str,
    model: str,
    effort: str,
    duration_s: float,
    lang: str = "ru",
) -> str:
    """Create aesthetic run footer: 🛠 Code · 🧠 Gemini 3.8 Flash (High) · ⏱ 2.4s."""
    mode_name = t("kb.mode_plan" if mode == "plan" else "kb.mode_code", lang)
    clean_model = model.replace("gemini-", "").replace("claude-", "").replace("-thinking", "")
    if "flash" in clean_model.lower():
        clean_model = "Gemini Flash"
    elif "pro" in clean_model.lower():
        clean_model = "Gemini Pro"
    elif "opus" in clean_model.lower():
        clean_model = "Claude Opus"
    elif "sonnet" in clean_model.lower():
        clean_model = "Claude Sonnet"

    if effort:
        clean_model = f"{clean_model} ({effort.capitalize()})"

    return f"\n\n<code>🛠 {mode_name} · 🧠 {clean_model} · ⏱ {duration_s:.1f}s</code>"
