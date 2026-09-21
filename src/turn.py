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
    accumulated_response = ""
    latest_action: str | None = None
    new_action_event = asyncio.Event()
    new_delta_event = asyncio.Event()
    flusher_stop = asyncio.Event()

    is_streaming_enabled = getattr(cs, "streaming", True)
    is_verbose_actions_enabled = getattr(cs, "verbose_actions", True)

    async def handle_action(action_str: str) -> None:
        nonlocal latest_action
        if not is_verbose_actions_enabled or accumulated_response:
            return
        latest_action = action_str
        new_action_event.set()

    async def handle_delta(delta: str, accumulated: str) -> None:
        nonlocal accumulated_response
        accumulated_response = accumulated
        if not is_streaming_enabled:
            return
        new_delta_event.set()

    async def _stream_flusher() -> None:
        nonlocal status_msg_id
        last_edit_time = 0.0
        while not flusher_stop.is_set():
            try:
                await asyncio.sleep(0.9)
            except asyncio.CancelledError:
                break

            now = time.time()
            if new_delta_event.is_set():
                new_delta_event.clear()
                import re
                stream_preview = re.sub(r"(?is)<\s*NOTIFICATION\b[^>]*>.*?</\s*NOTIFICATION\s*>", "", accumulated_response)
                stream_preview = re.sub(r"(?is)<\s*SYSTEM_MESSAGE\b[^>]*>.*?</\s*SYSTEM_MESSAGE\s*>", "", stream_preview)
                stream_preview = re.sub(r"(?is)<\s*system-reminder\b[^>]*>.*?</\s*system-reminder\s*>", "", stream_preview)
                stream_preview = re.sub(r"(?is)<\s*context\b[^>]*>.*?</\s*context\s*>", "", stream_preview)
                stream_preview = re.sub(r"(?is)<\s*thought\b[^>]*>.*?</\s*thought\s*>", "", stream_preview)
                stream_preview = re.sub(r"(?is)<\s*FINAL_RESPONSE\b[^>]*>(.*?)</\s*FINAL_RESPONSE\s*>", r"\1", stream_preview)
                stream_preview = re.sub(r"(?is)</?FINAL_RESPONSE\b[^>]*>", "", stream_preview).strip()
                if not stream_preview:
                    continue

                if len(stream_preview) > 3500:
                    stream_preview = stream_preview[:3500] + " ⏳..."
                else:
                    stream_preview += " ▌"

                if status_msg_id is None:
                    try:
                        status_msg_id = await tg.send_message(chat_id, stream_preview, parse_mode="HTML")
                        last_edit_time = now
                    except Exception as err:
                        LOG.warning("Failed to send stream message: %s", err)
                elif now - last_edit_time >= 0.8:
                    last_edit_time = now
                    try:
                        await tg.edit_message_text(chat_id, status_msg_id, stream_preview, parse_mode="HTML")
                    except Exception as err:
                        LOG.warning("Failed to edit stream message: %s", err)
                        if "not found" in str(err).lower():
                            status_msg_id = None

            elif new_action_event.is_set() and not accumulated_response:
                new_action_event.clear()
                act_str = latest_action
                if not act_str:
                    continue
                if status_msg_id is None:
                    try:
                        status_msg_id = await tg.send_message(chat_id, act_str, parse_mode="HTML")
                        last_edit_time = now
                    except Exception as err:
                        LOG.warning("Failed to send action message: %s", err)
                elif now - last_edit_time >= 0.8:
                    last_edit_time = now
                    try:
                        await tg.edit_message_text(chat_id, status_msg_id, act_str, parse_mode="HTML")
                    except Exception as err:
                        LOG.warning("Failed to edit action message: %s", err)
                        if "not found" in str(err).lower():
                            status_msg_id = None

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
            on_action=handle_action,
            on_delta=handle_delta,
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
