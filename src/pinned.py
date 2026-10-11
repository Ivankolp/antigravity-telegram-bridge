"""Pinned session status dashboard for Antigravity Telegram Bridge.

Maintains a single pinned status message at the top of the chat (pinChatMessage
with disable_notification=True), updated in-place on turns, settings changes,
and session switches.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.i18n import t
from src.sessions import get_active_session_info
from src.telegram import InlineKeyboard

if TYPE_CHECKING:
    from src.config import Config
    from src.daemon import _TelegramLike
    from src.state import ChatState

LOG = logging.getLogger("antigravity_telegram_bridge.pinned")


def format_pinned_dashboard(cs: "ChatState", cfg: "Config | None" = None) -> tuple[str, InlineKeyboard]:
    """Render the dashboard text and inline keyboard."""
    lang = getattr(cs, "language", "ru")

    if not cs.has_session:
        session_title = "Новый диалог" if lang == "ru" else "New Session"
    else:
        s_info = get_active_session_info(cs.conversation_id)
        if s_info and s_info.title:
            session_title = s_info.title
        elif cs.conversation_id:
            session_title = f"{cs.conversation_id[:12]}…"
        else:
            session_title = "Активная сессия" if lang == "ru" else "Active Session"

    model = cs.model or (cfg.agy.model if cfg else "gemini-3.8-flash-high")
    effort = cs.effort
    if not effort:
        effort = "High" if "high" in model else ("Medium" if "medium" in model else "Auto")
    else:
        effort = effort.capitalize()

    mode = cs.mode or (cfg.agy.mode if cfg else "code")
    mode_title = "Plan" if mode == "plan" else "Code"

    home = os.path.expanduser("~")
    workdir = cs.chat_dir.replace(home, "~", 1)

    text = (
        f"📌 <b>{t('pinned.title', lang)}</b>\n\n"
        f"📁 <b>{t('pinned.session', lang)}:</b> <code>{session_title}</code>\n"
        f"🤖 <b>{t('pinned.model', lang)}:</b> <code>{model}</code> ({effort})\n"
        f"🛡 <b>{t('pinned.mode', lang)}:</b> <code>{mode_title}</code>\n"
        f"📂 <b>{t('pinned.dir', lang)}:</b> <code>{workdir}</code>\n"
        f"📊 <b>{t('pinned.turns', lang)}:</b> <code>{cs.turn_count}</code>"
    )

    keyboard: InlineKeyboard = [
        [
            {"text": t("pinned.new_session_btn", lang), "callback_data": "pin:new"},
            {"text": t("pinned.sessions_btn", lang), "callback_data": "pin:sessions"},
        ],
        [
            {"text": t("pinned.settings_btn", lang), "callback_data": "pin:settings"},
            {"text": t("pinned.refresh_btn", lang), "callback_data": "pin:refresh"},
        ],
    ]

    return text, keyboard


async def update_pinned_dashboard(
    tg: "_TelegramLike",
    chat_id: int,
    cs: "ChatState",
    cfg: "Config | None" = None,
    force_recreate: bool = False,
) -> int | None:
    """Update or create the pinned dashboard message."""
    enabled = getattr(cs, "pinned_dashboard_enabled", True)

    # If disabled by user, unpin if any exists
    if not enabled:
        if cs.pinned_message_id:
            try:
                await tg.unpin_chat_message(chat_id, cs.pinned_message_id)
            except Exception:
                pass
            cs.pinned_message_id = None
        return None

    text, keyboard = format_pinned_dashboard(cs, cfg)

    # Try updating existing pinned message in-place
    if cs.pinned_message_id and not force_recreate:
        try:
            await tg.edit_message_text(chat_id, cs.pinned_message_id, text, keyboard=keyboard)
            return cs.pinned_message_id
        except Exception as err:
            err_str = str(err).lower()
            if "not modified" in err_str:
                return cs.pinned_message_id
            LOG.debug("Pinned dashboard message %s cannot be edited (%s), resetting...", cs.pinned_message_id, err)
            cs.pinned_message_id = None

    # Send fresh message and pin it
    try:
        mid = await tg.send_message(chat_id, text, keyboard=keyboard)
        if mid:
            cs.pinned_message_id = mid
            if hasattr(tg, "pin_chat_message"):
                try:
                    await tg.pin_chat_message(chat_id, mid, disable_notification=True)
                except Exception as pin_err:
                    LOG.warning("Failed to pin dashboard message %s: %s", mid, pin_err)
            return mid
    except Exception as send_err:
        LOG.error("Failed to send pinned dashboard to chat %s: %s", chat_id, send_err)

    return None
