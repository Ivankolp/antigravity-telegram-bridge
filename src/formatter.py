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
    """Count non-empty lines in text, handling both real and escaped newlines."""
    if not text:
        return 0
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\n", "\n")
    return len(text.splitlines())


def clean_tool_params(raw: Any) -> dict[str, Any]:
    """Normalize and unwrap tool parameters from agy stream."""
    import json
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        return {}

    cleaned: dict[str, Any] = {}
    for k, v in raw.items():
        if isinstance(v, str):
            v_str = v.strip()
            # If wrapped in literal outer quotes: "\"foo\"" or "'foo'"
            if len(v_str) >= 2 and ((v_str[0] == '"' and v_str[-1] == '"') or (v_str[0] == "'" and v_str[-1] == "'")):
                try:
                    v_str = json.loads(v_str)
                except Exception:
                    v_str = v_str[1:-1]
            if isinstance(v_str, str) and "\\n" in v_str and "\n" not in v_str:
                try:
                    v_str = v_str.encode("utf-8").decode("unicode_escape", errors="replace")
                except Exception:
                    v_str = v_str.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
            cleaned[k] = v_str
        else:
            cleaned[k] = v
    return cleaned


def generate_diff_preview(
    target_content: str,
    replacement_content: str,
    file_path: str = "",
    max_lines: int = 25,
) -> str:
    """Generate a clean unified diff preview with + and - markers for Telegram."""
    if not target_content and not replacement_content:
        return ""

    import difflib

    t_lines = target_content.splitlines(keepends=True)
    r_lines = replacement_content.splitlines(keepends=True)

    fname = Path(file_path).name if file_path else "file"
    diff = list(
        difflib.unified_diff(
            t_lines,
            r_lines,
            fromfile=f"a/{fname}",
            tofile=f"b/{fname}",
            n=1,
        )
    )

    if not diff:
        diff_lines = []
        for line in target_content.splitlines()[:10]:
            diff_lines.append(f"- {line}")
        for line in replacement_content.splitlines()[:15]:
            diff_lines.append(f"+ {line}")
        diff = diff_lines

    # Filter or truncate to max_lines
    if len(diff) > max_lines:
        half = max_lines // 2
        diff = diff[:half] + ["@@ ... @@\n"] + diff[-half:]

    return "".join(diff).strip()


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
    metadata includes file paths, diff stats (+lines, -lines), and diff preview.
    """
    icon = get_tool_icon(tool_name)
    p = clean_tool_params(params)
    meta: dict[str, Any] = {
        "tool": tool_name,
        "is_file_change": False,
        "file_path": "",
        "additions": 0,
        "deletions": 0,
        "diff_preview": "",
    }

    if tool_name in ("view_file", "read"):
        path = str(p.get("AbsolutePath") or p.get("path") or p.get("TargetFile") or "")
        disp = normalize_path_for_display(path, base_dir)
        meta["file_path"] = path
        short = f"{icon} read {disp}"
        return icon, short, meta

    elif tool_name in ("write_to_file", "write"):
        path = str(p.get("TargetFile") or p.get("path") or p.get("file") or "")
        disp = normalize_path_for_display(path, base_dir)
        content = str(p.get("CodeContent") or p.get("content") or "")
        lines = count_lines(content)
        meta["is_file_change"] = True
        meta["file_path"] = path
        meta["additions"] = lines
        short = f"{icon} write {disp} (+{lines})"
        return icon, short, meta

    elif tool_name in ("replace_file_content", "edit"):
        path = str(p.get("TargetFile") or p.get("path") or p.get("file") or "")
        disp = normalize_path_for_display(path, base_dir)
        target = str(p.get("TargetContent") or p.get("target_content") or p.get("target") or "")
        replace = str(p.get("ReplacementContent") or p.get("replacement_content") or p.get("replacement") or p.get("content") or "")
        del_lines = count_lines(target)
        add_lines = count_lines(replace)
        start_l = p.get("StartLine")
        end_l = p.get("EndLine")
        if del_lines == 0 and start_l and end_l:
            try:
                del_lines = max(1, int(end_l) - int(start_l) + 1)
            except Exception:
                pass
        diff_preview = generate_diff_preview(target, replace, path)
        meta["is_file_change"] = True
        meta["file_path"] = path
        meta["additions"] = add_lines
        meta["deletions"] = del_lines
        meta["diff_preview"] = diff_preview
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
