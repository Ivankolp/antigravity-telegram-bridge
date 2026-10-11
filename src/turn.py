"""Turn execution — agy invocation with live token streaming and distinct tool action reporting."""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.agy_runner import _format_tool_action, run_agy
from src.formatter import clean_tool_params, format_run_footer, normalize_path_for_display
from src.i18n import t

if TYPE_CHECKING:
    from src.config import Config
    from src.daemon import _TelegramLike
    from src.state import ChatState
    from src.telegram import InboundMessage

LOG = logging.getLogger("antigravity_telegram_bridge.turn")
# Unlimited timeout: allow long-running compiles, workflows, and tasks to run as long as needed without being cut off
AGY_TIMEOUT_S: float | None = None


class TurnResult(tuple):
    """Backwards-compatible tuple (text, exit_code) with rich attributes."""
    def __new__(
        cls,
        text: str,
        exit_code: int,
        stderr: str = "",
        status_msg_id: int | None = None,
        already_delivered: bool = False,
    ):
        instance = super().__new__(cls, (text, exit_code))
        instance._stderr = stderr
        instance._status_msg_id = status_msg_id
        instance._already_delivered = already_delivered
        return instance

    @property
    def text(self) -> str:
        return self[0]

    @property
    def exit_code(self) -> int:
        return self[1]

    @property
    def stderr(self) -> str:
        return getattr(self, "_stderr", "")

    @property
    def status_msg_id(self) -> int | None:
        return getattr(self, "_status_msg_id", None)

    @property
    def already_delivered(self) -> bool:
        return getattr(self, "_already_delivered", False)


def clean_turn_text(text: str, show_thinking: bool = True, is_live_stream: bool = False) -> str:
    """Clean internal protocol tags and file dispatch tags, cleanly format thinking."""
    s = text or ""
    # 1. Strip protocol tags
    s = re.sub(r"(?is)<\s*NOTIFICATION\b[^>]*>.*?</\s*NOTIFICATION\s*>", "", s)
    s = re.sub(r"(?is)<\s*SYSTEM_MESSAGE\b[^>]*>.*?</\s*SYSTEM_MESSAGE\s*>", "", s)
    s = re.sub(r"(?is)<\s*system-reminder\b[^>]*>.*?</\s*system-reminder\s*>", "", s)
    s = re.sub(r"(?is)<\s*context\b[^>]*>.*?</\s*context\s*>", "", s)
    # 2. Strip bridge file dispatch tags from chat output
    s = re.sub(r"\[(?:SEND_FILE|SEND_DOCUMENT|FILE):\s*[^\]]+\]", "", s, flags=re.IGNORECASE)
    if is_live_stream:
        s = re.sub(r"\[(?:SEND_FILE|SEND_DOCUMENT|FILE):[^\]]*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"(?is)<\s*FINAL_RESPONSE\b[^>]*>(.*?)</\s*FINAL_RESPONSE\s*>", r"\1", s)
    s = re.sub(r"(?is)</?FINAL_RESPONSE\b[^>]*>", "", s)

    # 3. Handle thinking blocks
    if show_thinking:
        def _fmt_thought(m: re.Match) -> str:
            th = m.group(1).strip()
            if not th:
                return ""
            return f"<blockquote expandable><b>Мышление модели:</b>\n{html.escape(th)}</blockquote>\n\n"

        s = re.sub(r"(?is)<\s*thought\b[^>]*>(.*?)</\s*thought\s*>", _fmt_thought, s)

        if is_live_stream and "<thought" in s.lower() and "</thought" not in s.lower():
            parts = re.split(r"(?i)<\s*thought\b[^>]*>", s, maxsplit=1)
            before = parts[0]
            current_th = parts[1].strip() if len(parts) > 1 else ""
            if current_th:
                s = f"{before}<blockquote expandable><b>Мышление модели:</b>\n{html.escape(current_th)}</blockquote>"
    else:
        s = re.sub(r"(?is)<\s*thought\b[^>]*>.*?</\s*thought\s*>", "", s)
        if is_live_stream:
            s = re.sub(r"(?is)<\s*thought\b[^>]*>.*$", "", s)

    return s.strip()


def _clean_stream_preview(text: str) -> str:
    return clean_turn_text(text, show_thinking=True, is_live_stream=True)


async def execute_agy(
    tg: "_TelegramLike",
    chat_id: int,
    msg: "InboundMessage",
    cs: "ChatState",
    cfg: "Config",
    agy_path: str,
    prompt: str = "",
) -> TurnResult:
    """Run one agy turn with typing heartbeat, separate tool action messages and real-time token streaming."""
    hb_stop = asyncio.Event()
    hb_task = asyncio.create_task(_heartbeat(tg, chat_id, hb_stop))
    turn_start = time.perf_counter()
    actual_prompt = prompt or msg.text
    lang = getattr(cs, "language", "ru")

    accumulated_response = ""
    file_changes: list[dict[str, Any]] = []
    stream_msg_id: int | None = None
    stream_delivered = False
    new_delta_event = asyncio.Event()
    flusher_stop = asyncio.Event()

    is_streaming_enabled = getattr(cs, "streaming", True)
    is_verbose_actions_enabled = getattr(cs, "verbose_actions", True)

    async def handle_tool(tool_name: str, params: dict[str, Any]) -> None:
        nonlocal stream_msg_id
        cleaned = clean_tool_params(params)
        is_fc = tool_name in ("write_to_file", "replace_file_content") or "TargetFile" in cleaned or "file_path" in cleaned
        if is_fc:
            fpath = cleaned.get("TargetFile") or cleaned.get("file_path") or ""
            if fpath:
                file_changes.append({"tool": tool_name, "file_path": str(fpath)})

        # Break stream continuity so response text after tools appears in fresh message
        if stream_msg_id is not None:
            stream_msg_id = None

        if is_verbose_actions_enabled:
            act_str = _format_tool_action(tool_name, cleaned)
            if act_str:
                try:
                    await tg.send_message(chat_id, act_str, parse_mode="HTML")
                except Exception as e:
                    LOG.warning("Failed to send tool action message chat=%s: %s", chat_id, e)

    async def handle_delta(delta: str, accumulated: str) -> None:
        nonlocal accumulated_response
        accumulated_response = accumulated
        if is_streaming_enabled:
            new_delta_event.set()

    async def _stream_flusher() -> None:
        nonlocal stream_msg_id
        last_edit_time = 0.0
        while not flusher_stop.is_set():
            try:
                await asyncio.wait_for(new_delta_event.wait(), timeout=0.6)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            new_delta_event.clear()

            if not is_streaming_enabled or flusher_stop.is_set():
                continue

            now = time.perf_counter()
            preview = clean_turn_text(accumulated_response, show_thinking=getattr(cs, "show_thinking", True), is_live_stream=True)
            if not preview:
                continue

            if len(preview) > 3800:
                preview_text = preview[:3800] + " ..."
            else:
                preview_text = preview + " ▌"

            if stream_msg_id is None:
                # Send first message immediately without artificial delays
                try:
                    stream_msg_id = await tg.send_message(chat_id, preview_text, parse_mode="HTML")
                    last_edit_time = now
                except Exception as err:
                    LOG.debug("Failed initial stream message HTML: %s, trying plain text", err)
                    try:
                        stream_msg_id = await tg.send_message(chat_id, preview_text, parse_mode=None)
                        last_edit_time = now
                    except Exception as err2:
                        LOG.warning("Failed initial stream message plain text: %s", err2)
            elif now - last_edit_time >= 0.6:
                last_edit_time = now
                try:
                    await tg.edit_message_text(chat_id, stream_msg_id, preview_text, parse_mode="HTML")
                except Exception as err:
                    LOG.debug("Failed editing stream message: %s", err)
                    if "not found" in str(err).lower():
                        stream_msg_id = None

    flusher_task = asyncio.create_task(_stream_flusher())

    try:
        result = await run_agy(
            prompt=actual_prompt,
            chat_dir=cs.chat_dir,
            has_session=cs.has_session,
            model=cs.model or cfg.agy.model,
            mode=cs.mode or cfg.agy.mode,
            effort=cs.effort,
            conversation_id=cs.conversation_id,
            agy_path=agy_path,
            timeout=AGY_TIMEOUT_S,
            on_tool=handle_tool,
            on_delta=handle_delta,
            show_thinking=getattr(cs, "show_thinking", True),
            lang=lang,
        )
    finally:
        flusher_stop.set()
        flusher_task.cancel()
        try:
            await flusher_task
        except (asyncio.CancelledError, Exception):
            pass
        hb_stop.set()
        hb_task.cancel()
        try:
            await hb_task
        except (asyncio.CancelledError, Exception):
            pass

    turn_end = time.perf_counter()
    elapsed_s = turn_end - turn_start

    final_text = result.text or accumulated_response

    # If stream_msg_id exists and final text fits in one message, finalize in-place
    clean_final = clean_turn_text(final_text, show_thinking=getattr(cs, "show_thinking", True), is_live_stream=False)
    if stream_msg_id is not None and clean_final and len(clean_final) <= 3900:
        try:
            await tg.edit_message_text(chat_id, stream_msg_id, clean_final, parse_mode="HTML")
            stream_delivered = True
        except Exception as e:
            LOG.warning("Failed to finalize stream edit chat=%s: %s, falling back to plain", chat_id, e)
            try:
                await tg.edit_message_text(chat_id, stream_msg_id, clean_final, parse_mode=None)
                stream_delivered = True
            except Exception as e2:
                LOG.warning("Plain finalize edit failed: %s, deleting stream msg", e2)
                try:
                    await tg.delete_message(chat_id, stream_msg_id)
                except Exception:
                    pass
                stream_msg_id = None
                stream_delivered = False
    elif stream_msg_id is not None and not stream_delivered:
        # Message too long for a single edit, delete stream placeholder so chunks are sent clean
        try:
            await tg.delete_message(chat_id, stream_msg_id)
        except Exception:
            pass
        stream_msg_id = None

    elapsed_ms = int(elapsed_s * 1000)
    LOG.info(
        "turn chat=%d cwd=%s exit=%d ms=%d reply_len=%d delivered=%s",
        chat_id, cs.chat_dir, result.exit_code, elapsed_ms, len(final_text or ""), stream_delivered
    )
    return TurnResult(final_text or "", result.exit_code, result.stderr or "", stream_msg_id, stream_delivered)


async def _heartbeat(
    tg: "_TelegramLike", chat_id: int, stop_event: asyncio.Event
) -> None:
    """Typing indicator refresh every 4 s."""
    try:
        while not stop_event.is_set():
            try:
                await tg.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=4.0)
            except asyncio.TimeoutError:
                pass
    except asyncio.CancelledError:
        return
