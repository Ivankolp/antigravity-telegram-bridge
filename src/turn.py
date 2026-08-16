"""Turn execution — agy print-mode invocation with streaming and tool action reporting."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from src.agy_runner import run_agy

if TYPE_CHECKING:
    from src.config import Config
    from src.daemon import _TelegramLike
    from src.state import ChatState
    from src.telegram import InboundMessage

LOG = logging.getLogger("antigravity_telegram_bridge")
AGY_TIMEOUT_S = 900.0


class TurnResult(tuple):
    """Backwards-compatible tuple (text, exit_code) with rich attributes."""
    def __new__(cls, text: str, exit_code: int, stderr: str = "", status_msg_id: int | None = None):
        instance = super().__new__(cls, (text, exit_code))
        instance._stderr = stderr
        instance._status_msg_id = status_msg_id
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


async def execute_agy(
    tg: "_TelegramLike", chat_id: int, msg: "InboundMessage",
    cs: "ChatState", cfg: "Config", agy_path: str,
    prompt: str = "",
) -> TurnResult:
    """Run one agy turn with typing heartbeat, tool action reporting and live streaming."""
    hb_stop = asyncio.Event()
    hb_task = asyncio.create_task(_heartbeat(tg, chat_id, hb_stop))
    turn_start = time.perf_counter()
    actual_prompt = prompt or msg.text

    status_msg_id: int | None = None
    last_edit_time = 0.0
    recent_actions: list[str] = []
    accumulated_response = ""

    is_streaming_enabled = getattr(cs, "streaming", True)
    is_verbose_actions_enabled = getattr(cs, "verbose_actions", True)

    if is_streaming_enabled or is_verbose_actions_enabled:
        try:
            status_msg_id = await tg.send_message(
                chat_id,
                "💭 <i>Думаю...</i>",
                parse_mode="HTML"
            )
        except Exception:
            status_msg_id = None

    async def handle_action(action_str: str) -> None:
        nonlocal last_edit_time, recent_actions
        if not is_verbose_actions_enabled or status_msg_id is None:
            return
        if not recent_actions or recent_actions[-1] != action_str:
            recent_actions.append(action_str)
            if len(recent_actions) > 4:
                recent_actions = recent_actions[-4:]

        now = time.time()
        if now - last_edit_time >= 0.7:
            last_edit_time = now
            action_lines = "\n".join([f"• {a}" for a in recent_actions])
            status_text = f"⚡️ <b>Выполняю действия:</b>\n{action_lines}"
            if accumulated_response:
                preview = accumulated_response[-250:].strip()
                status_text += f"\n\n💬 <i>{preview}...</i>"
            try:
                await tg.edit_message_text(chat_id, status_msg_id, status_text, parse_mode="HTML")
            except Exception:
                pass

    async def handle_delta(delta: str, accumulated: str) -> None:
        nonlocal last_edit_time, accumulated_response
        accumulated_response = accumulated
        if not is_streaming_enabled or status_msg_id is None:
            return

        now = time.time()
        if now - last_edit_time >= 0.8:
            last_edit_time = now
            stream_preview = accumulated
            if len(stream_preview) > 3500:
                stream_preview = stream_preview[:3500] + " ⏳..."
            else:
                stream_preview += " ▌"
            try:
                await tg.edit_message_text(chat_id, status_msg_id, stream_preview, parse_mode="HTML")
            except Exception:
                pass

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
            on_action=handle_action,
            on_delta=handle_delta,
        )
    finally:
        hb_stop.set()
        hb_task.cancel()
        try:
            await hb_task
        except (asyncio.CancelledError, Exception):
            pass

    elapsed = int((time.perf_counter() - turn_start) * 1000)
    final_text = result.text or accumulated_response
    LOG.info("turn chat=%d cwd=%s exit=%d ms=%d reply_len=%d",
             chat_id, cs.chat_dir, result.exit_code, elapsed, len(final_text or ""))
    return TurnResult(final_text or "", result.exit_code, result.stderr or "", status_msg_id)


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
