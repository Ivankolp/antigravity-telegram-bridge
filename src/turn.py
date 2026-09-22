"""Turn execution — agy print-mode invocation with streaming, progress bar, and file delivery."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.agy_runner import run_agy
from src.formatter import format_run_footer, normalize_path_for_display
from src.i18n import t
from src.progress import CompactProgressStreamer

if TYPE_CHECKING:
    from src.config import Config
    from src.daemon import _TelegramLike
    from src.state import ChatState
    from src.telegram import InboundMessage

LOG = logging.getLogger("antigravity_telegram_bridge.turn")
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
    """Run one agy turn with typing heartbeat, compact progress streamer and auto file dispatch."""
    hb_stop = asyncio.Event()
    hb_task = asyncio.create_task(_heartbeat(tg, chat_id, hb_stop))
    turn_start = time.perf_counter()
    actual_prompt = prompt or msg.text
    lang = getattr(cs, "language", "ru")

    progress = CompactProgressStreamer(
        tg=tg,
        chat_id=chat_id,
        base_dir=cs.chat_dir,
        lang=lang,
    )
    progress.start()

    accumulated_response = ""
    file_changes: list[dict[str, Any]] = []

    async def handle_tool(tool_name: str, params: dict[str, Any]) -> None:
        meta = progress.update_tool(tool_name, params)
        if meta.get("is_file_change") and meta.get("file_path"):
            file_changes.append(meta)

    async def handle_delta(delta: str, accumulated: str) -> None:
        nonlocal accumulated_response
        accumulated_response = accumulated
        progress.update_responding()

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
        hb_stop.set()
        hb_task.cancel()
        try:
            await hb_task
        except (asyncio.CancelledError, Exception):
            pass

    turn_end = time.perf_counter()
    elapsed_s = turn_end - turn_start

    # Finalize progress bar
    status_msg_id = await progress.finalize(
        delete_on_finish=getattr(cs, "delete_progress_on_finish", False),
        elapsed_s=elapsed_s,
    )

    # Deliver modified/created files as Telegram documents & diff snippets (like OpenCode sendDiffFileAttachments)
    if getattr(cs, "send_diff_files", True) and file_changes:
        sent_paths: set[str] = set()
        for fc in file_changes:
            raw_path = fc.get("file_path", "")
            if not raw_path or raw_path in sent_paths:
                continue
            sent_paths.add(raw_path)

            p = Path(raw_path)
            if not p.is_absolute():
                p = Path(cs.chat_dir) / p
            if p.exists() and p.is_file():
                import html
                disp = normalize_path_for_display(str(p), cs.chat_dir)
                adds = fc.get("additions", 0)
                dels = fc.get("deletions", 0)
                tool = fc.get("tool", "")
                diff_prev = (fc.get("diff_preview") or "").strip()

                # Send interactive diff code snippet so user sees exact changes
                if diff_prev:
                    diff_snippet_msg = (
                        f"📝 <b>Изменения: <code>{disp}</code></b> <code>(+{adds} -{dels})</code>\n\n"
                        f"<pre><code class=\"language-diff\">{html.escape(diff_prev)}</code></pre>"
                    )
                    try:
                        await tg.send_message(chat_id, diff_snippet_msg, parse_mode="HTML")
                    except Exception as diff_err:
                        LOG.warning("Failed to send diff preview for %s: %s", p, diff_err)

                if tool in ("write_to_file", "write"):
                    caption = t("file.caption_write", lang, path=disp, lines=adds)
                elif tool in ("replace_file_content", "edit"):
                    caption = t("file.caption_edit", lang, path=disp, additions=adds, deletions=dels)
                else:
                    size_kb = max(1, p.stat().st_size // 1024)
                    caption = t("file.caption_generic", lang, path=disp, size=f"{size_kb} KB")

                try:
                    await tg.send_document(
                        chat_id=chat_id,
                        file_path=str(p),
                        filename=p.name,
                        caption=caption,
                    )
                except Exception as err:
                    LOG.warning("Failed to auto-send tool file %s: %s", p, err)

    final_text = result.text or accumulated_response

    # Append run footer (mode · model · time) if enabled
    if final_text and getattr(cs, "show_footer", True):
        footer = format_run_footer(
            mode=cs.mode or cfg.agy.mode,
            model=cs.model or cfg.agy.model,
            effort=cs.effort,
            duration_s=elapsed_s,
            lang=lang,
        )
        final_text += footer

    elapsed_ms = int(elapsed_s * 1000)
    LOG.info(
        "turn chat=%d cwd=%s exit=%d ms=%d reply_len=%d tools=%d",
        chat_id, cs.chat_dir, result.exit_code, elapsed_ms, len(final_text or ""), progress.tool_count
    )
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
