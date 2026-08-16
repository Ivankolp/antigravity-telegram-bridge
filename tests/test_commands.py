"""Unit tests for src.commands — bridge command handlers."""
from __future__ import annotations

from src.commands import MODEL_CHOICES, handle_callback, handle_text_command
from src.config import AgyConfig, Config, TelegramConfig
from src.state import ChatState
from src.telegram import CallbackQuery


def _cfg(model: str = "", mode: str = "code") -> Config:
    return Config(
        telegram=TelegramConfig(bot_token="t", allowed_user_ids=[42]),
        agy=AgyConfig(default_workdir="/tmp", model=model, mode=mode),
    )


def _state() -> ChatState:
    return ChatState(chat_dir="/tmp/chat")


class _FakeMsg:
    def __init__(self, text: str, chat_id: int = 42, user_id: int = 42) -> None:
        self.text = text
        self.chat_id = chat_id
        self.user_id = user_id


async def test_start_command() -> None:
    reply = await handle_text_command(_FakeMsg("/start"), _state(), _cfg())
    assert reply is not None
    assert "Привет" in reply.text or "Welcome" in reply.text


async def test_help_command() -> None:
    reply = await handle_text_command(_FakeMsg("/help"), _state(), _cfg())
    assert reply is not None
    assert "Справка" in reply.text or "Commands" in reply.text


async def test_info_shows_default_model() -> None:
    reply = await handle_text_command(_FakeMsg("/info"), _state(), _cfg(model="gemini-3.7-flash-low"))
    assert reply is not None
    assert "gemini-3.7-flash-low" in reply.text


async def test_info_no_session() -> None:
    reply = await handle_text_command(_FakeMsg("/info"), _state(), _cfg())
    assert reply is not None
    assert "чистого листа" in reply.text.lower() or "fresh" in reply.text.lower()


async def test_thinking_command() -> None:
    reply = await handle_text_command(_FakeMsg("/thinking high"), _state(), _cfg())
    assert reply is not None
    assert "мышления" in reply.text.lower() or "thinking" in reply.text.lower()


async def test_streaming_toggle_command() -> None:
    cs = _state()
    reply = await handle_text_command(_FakeMsg("/stream"), cs, _cfg())
    assert reply is not None
    assert "стриминг" in reply.text.lower()


async def test_actions_toggle_command() -> None:
    cs = _state()
    reply = await handle_text_command(_FakeMsg("/actions"), cs, _cfg())
    assert reply is not None
    assert "действий" in reply.text.lower()


async def test_model_picker() -> None:
    reply = await handle_text_command(_FakeMsg("/model"), _state(), _cfg(model="gemini-3.7-flash-low"))
    assert reply is not None
    assert reply.keyboard is not None


async def test_model_set() -> None:
    cs = _state()
    reply = await handle_text_command(_FakeMsg("/model gemini-3.7-flash-low"), cs, _cfg())
    assert reply is not None
    assert cs.model == "gemini-3.7-flash-low"


async def test_reset_clears_session() -> None:
    cs = _state()
    cs.has_session = True
    reply = await handle_text_command(_FakeMsg("/reset"), cs, _cfg())
    assert reply is not None
    assert cs.has_session is False


async def test_unknown_returns_none() -> None:
    reply = await handle_text_command(_FakeMsg("hello world"), _state(), _cfg())
    assert reply is None


async def test_callback_nav_settings() -> None:
    cq = CallbackQuery(update_id=1, callback_query_id="q", chat_id=42, user_id=42, message_id=1, data="nav:settings")
    reply = handle_callback(cq, _state(), _cfg())
    assert reply.keyboard is not None


async def test_callback_model_choice() -> None:
    cs = _state()
    m = MODEL_CHOICES[0]
    cq = CallbackQuery(update_id=1, callback_query_id="q", chat_id=42, user_id=42, message_id=1, data=f"m:{m}")
    reply = handle_callback(cq, cs, _cfg())
    assert cs.model == m
    assert m in reply.toast


async def test_callback_reset() -> None:
    cs = _state()
    cs.has_session = True
    cq = CallbackQuery(update_id=1, callback_query_id="q", chat_id=42, user_id=42, message_id=1, data="R")
    reply = handle_callback(cq, cs, _cfg())
    assert cs.has_session is False
    assert "сброшена" in reply.toast.lower() or "reset" in reply.toast.lower()


async def test_callback_mode_choice() -> None:
    cs = _state()
    cq = CallbackQuery(update_id=1, callback_query_id="q", chat_id=42, user_id=42, message_id=1, data="M:plan")
    reply = handle_callback(cq, cs, _cfg())
    assert cs.mode == "plan"
    assert "plan" in reply.toast.lower() or "план" in reply.toast.lower()


async def test_callback_streaming_toggle() -> None:
    cs = _state()
    initial = cs.streaming
    cq = CallbackQuery(update_id=1, callback_query_id="q", chat_id=42, user_id=42, message_id=1, data="tog:streaming")
    reply = handle_callback(cq, cs, _cfg())
    assert cs.streaming is not initial
    assert "стриминг" in reply.toast.lower()


async def test_callback_actions_toggle() -> None:
    cs = _state()
    initial = cs.verbose_actions
    cq = CallbackQuery(update_id=1, callback_query_id="q", chat_id=42, user_id=42, message_id=1, data="tog:actions")
    reply = handle_callback(cq, cs, _cfg())
    assert cs.verbose_actions is not initial
    assert "действий" in reply.toast.lower()
