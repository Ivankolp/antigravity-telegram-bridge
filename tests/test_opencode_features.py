"""Tests for new opencode-inspired UX features:
- i18n localization
- formatter tool icons and diff stats
- CompactProgressStreamer
- File browser (/ls)
- Expandable thought block formatting
- Dynamic 2x2 keyboard
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.commands import get_main_reply_keyboard
from src.file_browser import decode_path, encode_path, render_file_browser
from src.formatter import (
    format_run_footer,
    format_thinking_block,
    format_tool_summary,
    get_tool_icon,
    normalize_path_for_display,
)
from src.i18n import t
from src.progress import CompactProgressStreamer
from src.state import ChatState


def test_i18n_basic() -> None:
    assert t("kb.limits", "ru") == "📊 Лимиты"
    assert t("kb.limits", "en") == "📊 Limits"
    assert "Работа завершена" in t("progress.done", "ru")
    assert "Completed" in t("progress.done", "en")
    assert t("tool.write", "ru", path="test.py", lines=10) == "✍️ Записываю: <code>test.py</code> (+10)"


def test_formatter_icons() -> None:
    assert get_tool_icon("write_to_file") == "✍️"
    assert get_tool_icon("replace_file_content") == "✏️"
    assert get_tool_icon("run_command") == "💻"
    assert get_tool_icon("view_file") == "📖"
    assert get_tool_icon("search_web") == "🌐"
    assert get_tool_icon("invoke_subagent") == "🤖"


def test_formatter_tool_summary() -> None:
    icon, short, meta = format_tool_summary(
        "write_to_file",
        {"TargetFile": "/repo/src/main.py", "CodeContent": "line1\nline2\nline3"},
        base_dir="/repo",
    )
    assert icon == "✍️"
    assert "src/main.py" in short
    assert "(+3)" in short
    assert meta["is_file_change"] is True
    assert meta["additions"] == 3


def test_formatter_thinking_block() -> None:
    raw = "I need to check the file contents first."
    block = format_thinking_block(raw, lang="ru")
    assert "<blockquote expandable>" in block
    assert "Мышление модели:" in block
    assert "I need to check the file contents first." in block


def test_formatter_run_footer() -> None:
    footer = format_run_footer(
        mode="code",
        model="gemini-3.8-flash-high",
        effort="high",
        duration_s=2.45,
        lang="ru",
    )
    assert "Code" in footer
    assert "Gemini Flash (High)" in footer
    assert "2.5s" in footer


def test_file_browser_encoding() -> None:
    path = "/var/log/test/file.py"
    token = encode_path(path)
    assert decode_path(token) == path


def test_file_browser_rendering(tmp_path: Path) -> None:
    (tmp_path / "subfolder").mkdir()
    (tmp_path / "hello.txt").write_text("world")

    header, kbd = render_file_browser(str(tmp_path), str(tmp_path), page=1, lang="ru")
    assert "hello.txt" in str(kbd)
    assert "subfolder/" in str(kbd)


def test_dynamic_reply_keyboard() -> None:
    cs = ChatState(
        chat_dir="/tmp",
        mode="plan",
        model="gemini-3.8-flash-high",
        effort="high",
        language="ru",
    )
    kb = get_main_reply_keyboard(cs)
    buttons = [btn["text"] for row in kb["keyboard"] for btn in row]
    assert any("Plan" in b for b in buttons)
    assert any("Лимиты" in b for b in buttons)
    assert any("Gemini" in b for b in buttons)
    assert any("High" in b for b in buttons)


@pytest.mark.asyncio
async def test_compact_progress_streamer() -> None:
    tg_mock = AsyncMock()
    tg_mock.send_message.return_value = 101
    tg_mock.edit_message_text.return_value = True

    streamer = CompactProgressStreamer(tg=tg_mock, chat_id=123, throttle_s=0.01)
    streamer.start()

    meta = streamer.update_tool(
        "run_command",
        {"CommandLine": "pytest tests/"},
    )
    assert streamer.tool_count == 1
    assert "pytest" in streamer.latest_text

    import asyncio
    await asyncio.sleep(0.03)

    final_id = await streamer.finalize(delete_on_finish=False, elapsed_s=1.2)
    assert final_id == 101
    tg_mock.edit_message_text.assert_called()
