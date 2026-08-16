"""Bridge command handlers — text slash commands, bottom reply keyboards, and inline callbacks."""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.media import clean_inbox, list_inbox
from src.sessions import list_sessions
from src.state import is_valid_model
from src.telegram import InlineKeyboard, ReplyKeyboard

if TYPE_CHECKING:
    from src.config import Config
    from src.daemon import _TelegramLike
    from src.state import ChatState
    from src.telegram import CallbackQuery, InboundMessage

DEFAULT_MODEL = "gemini-3.7-flash-low"

MODEL_CHOICES: tuple[str, ...] = (
    "gemini-3.7-flash-low",
    "gemini-3.7-flash-medium",
    "gemini-3.7-flash-high",
    "gemini-3.6-flash-low",
    "gemini-3.6-flash-medium",
    "gemini-3.6-flash-high",
    "gemini-3.5-flash-low",
    "gemini-3.5-flash-medium",
    "gemini-3.5-flash-high",
    "gemini-3.1-pro-low",
    "gemini-3.1-pro-high",
    "claude-sonnet-4-6",
    "claude-opus-4-6-thinking",
    "gpt-oss-120b-medium",
)
MODE_CHOICES: tuple[tuple[str, str], ...] = (
    ("code", "Код (автоматический)"),
    ("plan", "План (только чтение / песочница)"),
)
_DEFAULT_TOKEN = "_DEFAULT"

WELCOME_TEXT = (
    "👋 Привет! Я бот для управления Antigravity (agy CLI).\n\n"
    "Вы можете писать мне запросы текстом или пользоваться кнопками внизу клавиатуры для управления сессиями, моделями и настройками.\n\n"
    "🚀 Готов к работе!"
)

HELP_TEXT = (
    "📖 <b>Antigravity Bridge — Справка</b>\n\n"
    "Отправьте любой текст или код для выполнения в agy.\n\n"
    "<b>Кнопки меню внизу:</b>\n"
    "• 📁 Сессии — выбор старых сессий или создание новой\n"
    "• 📈 Лимиты — мгновенная проверка квоты и оставшихся лимитов\n"
    "• 🤖 Выбор модели — быстрый выбор модели нейросети\n"
    "• ⚙️ Настройки — режим работы, стриминг и логи действий\n"
    "• 📊 Статус — текущее состояние и активная сессия\n"
    "• 🧹 Новый диалог — начать диалог с чистого листа\n\n"
    "<b>Команды управления:</b>\n"
    "• /stream — переключить стриминг текста в реальном времени (ВКЛ/ВЫКЛ)\n"
    "• /actions — переключить отчет о действиях/шагах агента (ВКЛ/ВЫКЛ)\n"
    "• /model — выбор модели нейросети\n"
    "• /settings — панель настроек\n"
    "• /usage — проверка квот и лимитов\n"
    "• /reset — сброс текущей сессии"
)


def get_main_reply_keyboard() -> dict[str, Any]:
    """Bottom persistent reply keyboard for quick access without typing slash commands."""
    return {
        "keyboard": [
            [{"text": "📁 Сессии"}, {"text": "🧹 Новый диалог"}],
            [{"text": "🤖 Выбор модели"}, {"text": "📈 Лимиты"}, {"text": "📊 Статус"}],
            [{"text": "⚙️ Настройки"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


_USAGE_CACHE: dict[str, Any] = {"text": "", "timestamp": 0.0}


def get_agy_usage(agy_path: str = "agy", force: bool = False) -> str:
    """Run agy -p '/usage' with caching for instant (0ms) responses."""
    now = time.time()
    if not force and _USAGE_CACHE["text"] and (now - _USAGE_CACHE["timestamp"]) < 45:
        return _USAGE_CACHE["text"]

    try:
        res = subprocess.run([agy_path, "-p", "/usage"], capture_output=True, text=True, timeout=10)
        raw = res.stdout.strip()
        if not raw:
            return _USAGE_CACHE["text"] or "⚠️ Не удалось получить информацию о лимитах."

        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        out_lines = ["📊 <b>Лимиты Antigravity:</b>\n"]
        grouped: dict[str, list[str]] = {}

        for line in lines:
            if line.lower().startswith("quota"):
                continue
            m = re.match(
                r"^(.+?)\s+(Weekly Limit Remaining|Five Hour Limit Remaining)\s+(\d+%)\s+(.*)$",
                line,
                re.IGNORECASE,
            )
            if m:
                model_group, limit_type, percent, reset_time = m.groups()
                model_name = model_group.strip().replace(" models", "").replace(" Models", "")
                if model_name not in grouped:
                    grouped[model_name] = []

                period = "Неделя" if "weekly" in limit_type.lower() else "5 часов"
                icon = "📅" if "weekly" in limit_type.lower() else "⏳"
                try:
                    dt = datetime.fromisoformat(reset_time.strip().replace("Z", "+00:00"))
                    time_fmt = dt.astimezone().strftime("%d.%m %H:%M")
                except Exception:
                    time_fmt = reset_time.strip().replace("T", " ").replace("Z", "")
                grouped[model_name].append(f"  {icon} {period}: <b>{percent}</b> (до {time_fmt})")
            else:
                out_lines.append(f"<code>{line}</code>")

        for model, items in grouped.items():
            icon_m = "✨" if "gemini" in model.lower() else "⚡"
            out_lines.append(f"{icon_m} <b>{model}:</b>")
            out_lines.extend(items)
            out_lines.append("")

        result = "\n".join(out_lines).strip()
        _USAGE_CACHE["text"] = result
        _USAGE_CACHE["timestamp"] = now
        return result
    except Exception as e:
        if _USAGE_CACHE["text"]:
            return _USAGE_CACHE["text"]
        return f"⚠️ Ошибка при проверке лимитов: {e}"


def _render_usage(cs: "ChatState", cfg: "Config", force: bool = False) -> BridgeReply:
    text = get_agy_usage(force=force)
    keyboard: InlineKeyboard = [
        [{"text": "🔄 Обновить квоту", "callback_data": "nav:usage:force"}],
        [{"text": "← Назад в настройки", "callback_data": "nav:settings"}],
    ]
    return BridgeReply(text=text, keyboard=keyboard, reply_markup=get_main_reply_keyboard())


@dataclass(frozen=True)
class BridgeReply:
    """Daemon's structured reply: text, optional inline keyboard, optional reply_markup, optional toast."""

    text: str
    keyboard: InlineKeyboard | None = None
    reply_markup: dict[str, Any] | None = None
    toast: str = ""


def _effective_model(cs: "ChatState", cfg: "Config") -> tuple[str, str]:
    if cs.model:
        return cs.model, "чат"
    if cfg.agy.model:
        return cfg.agy.model, "конфиг"
    return DEFAULT_MODEL, "по умолчанию"


def _effective_mode(cs: "ChatState", cfg: "Config") -> tuple[str, str]:
    if cs.mode:
        return cs.mode, "чат"
    return cfg.agy.mode, "конфиг"


def render_status(cs: "ChatState", cfg: "Config") -> str:
    model, model_src = _effective_model(cs, cfg)
    mode, mode_src = _effective_mode(cs, cfg)
    effort = getattr(cs, "effort", "") or "по умолчанию"
    session = "активна (продолжение)" if cs.has_session else "новая (начнётся с чистого листа)"
    conv_info = f"\n  Активный ID: <code>{cs.conversation_id}</code>" if cs.conversation_id else ""
    home = os.path.expanduser("~")
    workdir = cs.chat_dir.replace(home, "~", 1)
    streaming_str = "ВКЛЮЧЕН ✅" if getattr(cs, "streaming", True) else "ВЫКЛЮЧЕН ❌"
    actions_str = "ВКЛЮЧЕНЫ ✅" if getattr(cs, "verbose_actions", True) else "ВЫКЛЮЧЕНЫ ❌"
    return (
        "🟢 <b>Antigravity Bridge — Статус</b>\n"
        f"🤖 Модель:     <b>{model}</b> [{model_src}]\n"
        f"🛡 Режим:      <b>{mode}</b> [{mode_src}]\n"
        f"🧠 Мышление:  <b>{effort}</b>\n"
        f"⚡️ Стриминг:   <b>{streaming_str}</b>\n"
        f"🔍 Логи шагов: <b>{actions_str}</b>\n"
        "\n"
        "📁 <b>Этот чат:</b>\n"
        f"  Сессия:   {session}{conv_info}\n"
        f"  Запросов: {cs.turn_count}\n"
        f"  Рабочая папка: <code>{workdir}</code>"
    )


def _settings_keyboard(cs: "ChatState" | None = None) -> InlineKeyboard:
    stream_lbl = "⚡️ Стриминг: ВКЛ ✅" if (cs is None or getattr(cs, "streaming", True)) else "⚡️ Стриминг: ВЫКЛ ❌"
    actions_lbl = "🔍 Действия: ВКЛ ✅" if (cs is None or getattr(cs, "verbose_actions", True)) else "🔍 Действия: ВЫКЛ ❌"
    return [
        [
            {"text": stream_lbl, "callback_data": "tog:streaming"},
            {"text": actions_lbl, "callback_data": "tog:actions"},
        ],
        [
            {"text": "🤖 Выбрать модель", "callback_data": "nav:model"},
            {"text": "🧠 Уровень мышления", "callback_data": "nav:effort"},
        ],
        [
            {"text": "🛡 Выбрать режим", "callback_data": "nav:mode"},
            {"text": "📈 Лимиты", "callback_data": "nav:usage"},
        ],
        [
            {"text": "📁 Управление сессиями", "callback_data": "nav:sessions"},
            {"text": "🧹 Сбросить сессию", "callback_data": "R"},
        ],
        [{"text": "🔄 Обновить", "callback_data": "nav:settings"}],
    ]


def _effort_keyboard(current_effort: str) -> InlineKeyboard:
    choices = [
        ("low", "⚡ Low (Быстрое мышление)"),
        ("medium", "⚖️ Medium (Сбалансированное)"),
        ("high", "🧠 High (Глубокое размышление)"),
    ]
    rows: InlineKeyboard = []
    for val, label in choices:
        marker = "● " if val == current_effort else "○ "
        rows.append([{"text": marker + label, "callback_data": f"eff:{val}"}])
    default_marker = "● " if not current_effort else "○ "
    rows.append([{"text": default_marker + "Использовать по умолчанию", "callback_data": f"eff:{_DEFAULT_TOKEN}"}])
    rows.append([{"text": "← Назад в настройки", "callback_data": "nav:settings"}])
    return rows


def _render_effort_picker(cs: "ChatState") -> BridgeReply:
    eff = getattr(cs, "effort", "") or "по умолчанию"
    return BridgeReply(
        text=f"🧠 <b>Настройка уровня мышления (Reasoning Effort):</b>\n\nТекущий уровень: <b>{eff}</b>\n\nВыберите глубину рассуждений модели:",
        keyboard=_effort_keyboard(getattr(cs, "effort", "")),
        reply_markup=get_main_reply_keyboard(),
    )


def _render_usage(cs: "ChatState", cfg: "Config", force: bool = False) -> BridgeReply:
    text = get_agy_usage()
    keyboard: InlineKeyboard = [
        [{"text": "🔄 Обновить квоту", "callback_data": "nav:usage"}],
        [{"text": "← Назад в настройки", "callback_data": "nav:settings"}],
    ]
    return BridgeReply(text=text, keyboard=keyboard, reply_markup=get_main_reply_keyboard())


def _model_keyboard(current_per_chat: str) -> InlineKeyboard:
    rows: InlineKeyboard = []
    # Two models per row for compactness
    row: list[dict[str, Any]] = []
    for m in MODEL_CHOICES:
        marker = "● " if m == current_per_chat else "○ "
        short_name = m.replace("gemini-", "G-").replace("claude-", "C-")
        row.append({"text": marker + short_name, "callback_data": f"m:{m}"})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    default_marker = "● " if not current_per_chat else "○ "
    rows.append([
        {"text": default_marker + "Использовать по умолчанию (конфиг)", "callback_data": f"m:{_DEFAULT_TOKEN}"}
    ])
    rows.append([{"text": "← Назад в настройки", "callback_data": "nav:settings"}],)
    return rows


def _mode_keyboard(current_per_chat: str) -> InlineKeyboard:
    cells: list[dict[str, object]] = []
    for value, label in MODE_CHOICES:
        marker = "● " if value == current_per_chat else "○ "
        cells.append({"text": marker + label, "callback_data": f"M:{value}"})
    default_marker = "● " if not current_per_chat else "○ "
    return [
        cells,
        [{"text": default_marker + "Использовать по умолчанию (конфиг)", "callback_data": f"M:{_DEFAULT_TOKEN}"}],
        [{"text": "← Назад в настройки", "callback_data": "nav:settings"}],
    ]


def _sessions_keyboard(active_conv_id: str) -> InlineKeyboard:
    sessions = list_sessions(limit=8)
    rows: InlineKeyboard = [
        [{"text": "➕ Начать новую сессию", "callback_data": "s:new"}]
    ]
    for s in sessions:
        marker = "● " if s.conversation_id == active_conv_id else "○ "
        btn_text = f"{marker}{s.created_at_str} | {s.title}"
        rows.append([{"text": btn_text, "callback_data": f"s:{s.conversation_id}"}])
    rows.append([{"text": "🔄 Обновить список", "callback_data": "nav:sessions"}])
    rows.append([{"text": "← Назад в настройки", "callback_data": "nav:settings"}])
    return rows


def _render_settings(cs: "ChatState", cfg: "Config") -> BridgeReply:
    return BridgeReply(
        text=render_status(cs, cfg),
        keyboard=_settings_keyboard(cs),
        reply_markup=get_main_reply_keyboard(),
    )


def _render_sessions_picker(cs: "ChatState") -> BridgeReply:
    active = cs.conversation_id or "(самая последняя)"
    return BridgeReply(
        text=f"📁 <b>История сессий Antigravity:</b>\n\nТекущая сессия: <code>{active}</code>\n\nВыберите сессию из списка ниже, чтобы переключиться на неё, или создайте новую:",
        keyboard=_sessions_keyboard(cs.conversation_id),
        reply_markup=get_main_reply_keyboard(),
    )


def _render_model_picker(cs: "ChatState", cfg: "Config") -> BridgeReply:
    cur, src = _effective_model(cs, cfg)
    return BridgeReply(
        text=f"🤖 Выберите модель для этого чата\n\nТекущая: {cur} [{src}]",
        keyboard=_model_keyboard(cs.model),
        reply_markup=get_main_reply_keyboard(),
    )


def _render_mode_picker(cs: "ChatState", cfg: "Config") -> BridgeReply:
    cur, src = _effective_mode(cs, cfg)
    return BridgeReply(
        text=f"🛡 Выберите режим работы для этого чата\n\nТекущий: {cur} [{src}]",
        keyboard=_mode_keyboard(cs.mode),
        reply_markup=get_main_reply_keyboard(),
    )


async def handle_text_command(
    msg: "InboundMessage",
    cs: "ChatState",
    cfg: "Config",
) -> BridgeReply | None:
    """Return a reply for a slash command or bottom menu button, else None (forward to agy)."""
    raw_text = msg.text.strip()
    if not raw_text:
        return None

    # Handle Bottom Keyboard clicks
    if raw_text == "📁 Сессии":
        return _render_sessions_picker(cs)
    if raw_text == "🧹 Новый диалог":
        cs.has_session = False
        cs.conversation_id = ""
        return BridgeReply(
            "🧹 Создана новая сессия! Следующий ваш запрос начнётся с чистого листа.",
            reply_markup=get_main_reply_keyboard(),
        )
    if raw_text in ("🤖 Выбор модели", "🤖 Модель"):
        return _render_model_picker(cs, cfg)
    if raw_text in ("📈 Лимиты", "📊 Лимиты", "Лимиты", "Квота"):
        return _render_usage(cs, cfg)
    if raw_text in ("⚙️ Настройки", "Настройки"):
        return _render_settings(cs, cfg)
    if raw_text in ("📊 Статус", "Статус"):
        return BridgeReply(render_status(cs, cfg), reply_markup=get_main_reply_keyboard())

    # Handle Slash Commands
    parts = raw_text.split(maxsplit=1)
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    if cmd == "/start":
        return BridgeReply(WELCOME_TEXT, reply_markup=get_main_reply_keyboard())
    if cmd == "/help":
        return BridgeReply(HELP_TEXT, reply_markup=get_main_reply_keyboard())
    if cmd in ("/status", "/info"):
        return BridgeReply(render_status(cs, cfg), reply_markup=get_main_reply_keyboard())
    if cmd in ("/usage", "/limits", "/quota", "/yousage", "/you_sage") or raw_text.lower().startswith("/you sage"):
        return _render_usage(cs, cfg)
    if cmd == "/sessions":
        return _render_sessions_picker(cs)
    if cmd == "/settings":
        return _render_settings(cs, cfg)
    if cmd in ("/stream", "/streaming"):
        cs.streaming = not getattr(cs, "streaming", True)
        state_str = "<b>ВКЛЮЧЕН</b> ✅" if cs.streaming else "<b>ВЫКЛЮЧЕН</b> ❌"
        return BridgeReply(f"⚡️ Стриминг ответов в реальном времени: {state_str}", reply_markup=get_main_reply_keyboard())
    if cmd in ("/actions", "/verbose", "/steps"):
        cs.verbose_actions = not getattr(cs, "verbose_actions", True)
        state_str = "<b>ВКЛЮЧЕНЫ</b> ✅" if cs.verbose_actions else "<b>ВЫКЛЮЧЕНЫ</b> ❌"
        return BridgeReply(f"🔍 Детальные логи действий (шаги инструментов): {state_str}", reply_markup=get_main_reply_keyboard())
    if cmd == "/model":
        if args:
            if is_valid_model(args):
                cs.model = args
                return BridgeReply(f"🤖 Модель установлена: {args}", reply_markup=get_main_reply_keyboard())
            return BridgeReply(f"⚠️ Неподдерживаемая модель: {args}", reply_markup=get_main_reply_keyboard())
        return _render_model_picker(cs, cfg)
    if cmd == "/mode":
        if args:
            if args in {"code", "plan"}:
                cs.mode = args
                return BridgeReply(f"🛡 Режим установлен: {args}", reply_markup=get_main_reply_keyboard())
            return BridgeReply(f"⚠️ Неподдерживаемый режим: {args}", reply_markup=get_main_reply_keyboard())
        return _render_mode_picker(cs, cfg)
    if cmd == "/reset":
        cs.has_session = False
        cs.conversation_id = ""
        return BridgeReply("🧹 Сессия сброшена. Следующее сообщение начнет диалог заново.", reply_markup=get_main_reply_keyboard())
    if cmd in ("/effort", "/thinking", "/мышление", "/reasoning"):
        mode = args.strip().lower()
        if mode in ("low", "medium", "high"):
            cs.effort = mode
            return BridgeReply(f"🧠 Уровень мышления установлен: <b>{mode}</b>", reply_markup=get_main_reply_keyboard())
        elif mode in ("off", "default", "none", "0"):
            cs.effort = ""
            return BridgeReply("🧠 Уровень мышления сброшен на значение по умолчанию.", reply_markup=get_main_reply_keyboard())
        return _render_effort_picker(cs)
    if cmd == "/compact":
        return BridgeReply("🗜️ Сжатие контекста не поддерживается в текущем режиме agy.", reply_markup=get_main_reply_keyboard())
    if cmd == "/image":
        mode = args.strip().lower()
        if mode in ("on", "true", "1"):
            cs.photo_enabled = True  # type: ignore[attr-defined]
            return BridgeReply("📸 Обработка фото: ВКЛ", reply_markup=get_main_reply_keyboard())
        if mode in ("off", "false", "0"):
            cs.photo_enabled = False  # type: ignore[attr-defined]
            return BridgeReply("📸 Обработка фото: ВЫКЛ", reply_markup=get_main_reply_keyboard())
        return BridgeReply("📸 Переключение обработки фото недоступно в этой сборке.", reply_markup=get_main_reply_keyboard())
    if cmd in ("/download", "/get", "/file"):
        if not args:
            return BridgeReply("ℹ️ Укажите имя файла для скачивания, например: <code>/download main.py</code>", reply_markup=get_main_reply_keyboard())
        p = Path(args)
        if not p.is_absolute():
            p = Path(cs.chat_dir) / p
        if not p.exists():
            inbox_p = Path(cs.chat_dir) / ".bridge-inbox" / args
            if inbox_p.exists():
                p = inbox_p
            else:
                return BridgeReply(f"⚠️ Файл не найден: <code>{args}</code>", reply_markup=get_main_reply_keyboard())
        if p.is_dir():
            import shutil
            import tempfile
            zip_tmp = Path(tempfile.gettempdir()) / f"{p.name}"
            archive_path = shutil.make_archive(str(zip_tmp), "zip", str(p))
            return BridgeReply(text=f"SEND_FILE:{archive_path}:{p.name}.zip", reply_markup=get_main_reply_keyboard())
        return BridgeReply(text=f"SEND_FILE:{p}:{p.name}", reply_markup=get_main_reply_keyboard())

    if cmd in ("/stop", "/cancel") or raw_text.lower() in ("стоп", "отмена", "отменить"):
        return BridgeReply(text="INTERNAL_STOP", reply_markup=get_main_reply_keyboard())

    if cmd == "/files":
        wd = cs.chat_dir or (cfg.agy.default_workdir if hasattr(cfg.agy, "default_workdir") else "")
        files = list_inbox(wd)
        if not files:
            return BridgeReply("📂 Папка входящих файлов пуста.", reply_markup=get_main_reply_keyboard())
        return BridgeReply("📂 Недавние файлы:\n" + "\n".join(f"• {f}" for f in files), reply_markup=get_main_reply_keyboard())
    if cmd == "/queue":
        return BridgeReply("📋 Статус очереди доступен через внутренние службы.", reply_markup=get_main_reply_keyboard())
    return None


def handle_callback(
    cq: "CallbackQuery",
    cs: "ChatState",
    cfg: "Config",
) -> BridgeReply:
    """Handle inline-keyboard button taps. Always returns a reply to render."""
    data = cq.data

    if data == "nav:status":
        return BridgeReply(render_status(cs, cfg), reply_markup=get_main_reply_keyboard())
    if data == "nav:settings":
        return _render_settings(cs, cfg)
    if data == "tog:streaming":
        cs.streaming = not getattr(cs, "streaming", True)
        toast = "Стриминг: ВКЛ" if cs.streaming else "Стриминг: ВЫКЛ"
        rep = _render_settings(cs, cfg)
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(), toast=toast)
    if data == "tog:actions":
        cs.verbose_actions = not getattr(cs, "verbose_actions", True)
        toast = "Логи действий: ВКЛ" if cs.verbose_actions else "Логи действий: ВЫКЛ"
        rep = _render_settings(cs, cfg)
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(), toast=toast)
    if data == "nav:usage":
        return _render_usage(cs, cfg, force=False)
    if data == "nav:usage:force":
        return _render_usage(cs, cfg, force=True)
    if data == "nav:sessions":
        return _render_sessions_picker(cs)
    if data == "nav:model":
        return _render_model_picker(cs, cfg)
    if data == "nav:mode":
        return _render_mode_picker(cs, cfg)
    if data == "nav:effort":
        return _render_effort_picker(cs)
    if data.startswith("eff:"):
        choice = data[4:]
        if choice == _DEFAULT_TOKEN:
            cs.effort = ""
            toast = "Мышление: по умолчанию"
        else:
            cs.effort = choice
            toast = f"Мышление: {choice}"
        rep = _render_effort_picker(cs)
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(), toast=toast)
    if data == "R":
        cs.has_session = False
        cs.conversation_id = ""
        rep = _render_settings(cs, cfg)
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(), toast="Сессия сброшена")

    # Session selection
    if data.startswith("s:"):
        choice = data[2:]
        if choice == "new":
            cs.has_session = False
            cs.conversation_id = ""
            toast = "Создана новая сессия"
        else:
            cs.conversation_id = choice
            cs.has_session = True
            toast = f"Выбрана сессия {choice[:8]}…"
        rep = _render_sessions_picker(cs)
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(), toast=toast)

    # Model selection
    if data.startswith("m:"):
        choice = data[2:]
        if choice == _DEFAULT_TOKEN:
            cs.model = ""
            toast = "По умолчанию (конфиг)"
        elif choice in MODEL_CHOICES:
            cs.model = choice
            toast = f"Модель: {choice}"
        else:
            toast = "Неизвестный выбор"
        rep = _render_model_picker(cs, cfg)
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(), toast=toast)

    # Mode selection
    if data.startswith("M:"):
        choice = data[2:]
        valid_modes = {v for v, _ in MODE_CHOICES}
        if choice == _DEFAULT_TOKEN:
            cs.mode = ""
            toast = "По умолчанию (конфиг)"
        elif choice in valid_modes:
            cs.mode = choice
            toast = f"Режим: {choice}"
        else:
            toast = "Неизвестный выбор"
        rep = _render_mode_picker(cs, cfg)
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(), toast=toast)

    return _render_settings(cs, cfg)
