"""Compact in-place progress streamer for Telegram.

Inspired by opencode-telegram-bot's compact-progress-streamer.ts.
Updates a single progress message with throttled edits (~800ms) and
either converts it to a completion summary or deletes it on finish.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from src.formatter import format_tool_summary
from src.i18n import t

if TYPE_CHECKING:
    from src.daemon import _TelegramLike

LOG = logging.getLogger("antigravity_telegram_bridge.progress")


class CompactProgressStreamer:
    """Manages throttled Telegram status message for turn execution."""

    def __init__(
        self,
        tg: "_TelegramLike",
        chat_id: int,
        base_dir: str = "",
        lang: str = "ru",
        throttle_s: float = 0.85,
    ) -> None:
        self.tg = tg
        self.chat_id = chat_id
        self.base_dir = base_dir
        self.lang = lang
        self.throttle_s = throttle_s

        self.message_id: int | None = None
        self.latest_text: str = t("progress.working", lang)
        self.tool_count: int = 0
        self.modified_files: set[str] = set()

        self._last_edit_time: float = 0.0
        self._stop_event = asyncio.Event()
        self._dirty_event = asyncio.Event()
        self._flusher_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def start(self) -> None:
        """Start background throttled flusher."""
        self._stop_event.clear()
        self._dirty_event.clear()
        self._flusher_task = asyncio.create_task(self._flusher_loop())

    def update_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Record tool action and trigger throttled update. Returns meta."""
        self.tool_count += 1
        icon, short_line, meta = format_tool_summary(tool_name, params, self.base_dir, self.lang)
        if meta.get("is_file_change") and meta.get("file_path"):
            self.modified_files.add(meta["file_path"])

        self.latest_text = f"⏳ <b>{t('progress.working', self.lang)}</b>\n{short_line}"
        self._dirty_event.set()
        return meta

    def update_activity(self, activity_text: str) -> None:
        """Update status line directly."""
        self.latest_text = f"⏳ <b>{t('progress.working', self.lang)}</b>\n{activity_text}"
        self._dirty_event.set()

    def update_responding(self) -> None:
        """Set state to generating response."""
        self.latest_text = f"⏳ <b>{t('progress.responding', self.lang)}</b>"
        self._dirty_event.set()

    async def _sync_message(self, text: str) -> None:
        """Send or edit message in Telegram."""
        now = time.time()
        if self.message_id is None:
            try:
                self.message_id = await self.tg.send_message(self.chat_id, text, parse_mode="HTML")
                self._last_edit_time = now
            except Exception as err:
                LOG.warning("Failed to send initial progress message: %s", err)
        else:
            try:
                await self.tg.edit_message_text(self.chat_id, self.message_id, text, parse_mode="HTML")
                self._last_edit_time = now
            except Exception as err:
                if "not found" in str(err).lower() or "message to edit not found" in str(err).lower():
                    self.message_id = None
                LOG.debug("Progress message edit skipped or failed: %s", err)

    async def _flusher_loop(self) -> None:
        """Background loop to periodically sync progress to Telegram."""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._dirty_event.wait(), timeout=self.throttle_s)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break

            if self._stop_event.is_set():
                break

            now = time.time()
            if self._dirty_event.is_set() and (now - self._last_edit_time >= self.throttle_s):
                self._dirty_event.clear()
                text = self.latest_text
                await self._sync_message(text)

    async def finalize(
        self,
        delete_on_finish: bool = False,
        elapsed_s: float = 0.0,
        files_details: list[str] | None = None,
    ) -> int | None:
        """Stop flusher, finalize status message in Telegram."""
        self._stop_event.set()
        self._dirty_event.set()
        if self._flusher_task:
            try:
                await self._flusher_task
            except (asyncio.CancelledError, Exception):
                pass

        details_block = ""
        if files_details:
            details_block = "\n" + "\n".join(files_details)

        if self.message_id is not None:
            if delete_on_finish:
                try:
                    await self.tg.delete_message(self.chat_id, self.message_id)
                except Exception:
                    pass
                mid = self.message_id
                self.message_id = None
                return mid
            else:
                try:
                    await self.tg.edit_message_text(
                        self.chat_id,
                        self.message_id,
                        self.latest_text + details_block,
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                return self.message_id

        return None
