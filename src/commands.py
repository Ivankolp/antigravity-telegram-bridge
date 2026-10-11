"""Bridge command handlers — text slash commands, bottom reply keyboards, and inline callbacks."""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.file_browser import decode_path, encode_path, render_file_browser
from src.i18n import t
from src.media import clean_inbox, list_inbox
from src.sessions import list_sessions
from src.state import is_valid_model
from src.telegram import InlineKeyboard, ReplyKeyboard

if TYPE_CHECKING:
    from src.config import Config
    from src.daemon import _TelegramLike
    from src.state import ChatState
    from src.telegram import CallbackQuery, InboundMessage

DEFAULT_MODEL = "gemini-3.7-flash-high"

DEFAULT_FALLBACK_MODELS = (
    "gemini-3.8-flash-high",
    "gemini-3.8-flash-medium",
    "gemini-3.8-flash-low",
    "gemini-3.7-flash-high",
    "gemini-3.7-flash-medium",
    "gemini-3.7-flash-low",
    "gemini-3.6-flash-high",
    "gemini-3.6-flash-medium",
    "gemini-3.6-flash-low",
    "gemini-3.1-pro-high",
    "gemini-3.1-pro-low",
    "claude-sonnet-4-6",
    "claude-opus-4-6-thinking",
    "gpt-oss-120b-medium",
)

MODEL_CHOICES = DEFAULT_FALLBACK_MODELS

_MODELS_CACHE: dict[str, Any] = {"models": [], "timestamp": 0.0}


def get_agy_models(agy_path: str = "agy", force: bool = False) -> list[tuple[str, str]]:
    """Fetch live available models dynamically from `agy models` with 5-minute caching."""
    now = time.time()
    if not force and _MODELS_CACHE["models"] and (now - _MODELS_CACHE["timestamp"]) < 300:
        return _MODELS_CACHE["models"]

    try:
        res = subprocess.run([agy_path, "models"], capture_output=True, text=True, timeout=10)
        output = res.stdout.strip()
        models = []
        for line in output.splitlines():
            clean_line = re.sub(r"^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏\s\.]*Fetching available models\.\.\.", "", line).strip()
            if not clean_line:
                continue
            parts = clean_line.split(maxsplit=1)
            if parts:
                mid = parts[0]
                mtitle = parts[1] if len(parts) > 1 else mid
                if re.match(r"^[a-zA-Z0-9._][a-zA-Z0-9._\-]*$", mid):
                    models.append((mid, mtitle))

        if models:
            _MODELS_CACHE["models"] = models
            _MODELS_CACHE["timestamp"] = now
            return models
    except Exception:
        pass

    if _MODELS_CACHE["models"]:
        return _MODELS_CACHE["models"]

    return [(m, m) for m in DEFAULT_FALLBACK_MODELS]


def sync_antigravity_settings(model_id: str) -> None:
    """Keep ~/.gemini/antigravity-cli/settings.json in sync with the selected model."""
    settings_file = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
    if not settings_file.exists():
        return
    try:
        import json
        data = json.loads(settings_file.read_text(encoding="utf-8"))
        models_dict = dict(get_agy_models())
        title = models_dict.get(model_id, model_id) if model_id else "Gemini 3.8 Flash (High)"
        data["model"] = title
        settings_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


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


def get_main_reply_keyboard(cs: "ChatState" | None = None, cfg: "Config" | None = None) -> dict[str, Any]:
    """Dynamic 2x2 bottom reply keyboard matching opencode-telegram-bot UX:
    Row 1: [ 🛠 Режим: Code ]  [ 📊 Лимиты ]
    Row 2: [ 🤖 Gemini 3.8 Flash ]  [ 💭 High ]
    """
    lang = getattr(cs, "language", "ru") if cs else "ru"

    mode = getattr(cs, "mode", "") or (cfg.agy.mode if cfg else "code")
    mode_title = "Plan" if mode == "plan" else "Code"
    btn_mode = t("kb.mode", lang, mode=mode_title)

    btn_limits = t("kb.limits", lang)

    model = getattr(cs, "model", "") or (cfg.agy.model if cfg else "gemini-3.8-flash-high")
    clean_model = model.replace("gemini-", "").replace("claude-", "").replace("-thinking", "")
    if "flash" in clean_model.lower():
        clean_model = "Gemini Flash"
    elif "pro" in clean_model.lower():
        clean_model = "Gemini Pro"
    elif "opus" in clean_model.lower():
        clean_model = "Claude Opus"
    elif "sonnet" in clean_model.lower():
        clean_model = "Claude Sonnet"
    elif not clean_model:
        clean_model = "Gemini Flash"
    btn_model = t("kb.model", lang, model=clean_model)

    effort = getattr(cs, "effort", "")
    if effort:
        effort_title = effort.capitalize()
    else:
        effort_title = "High" if "high" in model else ("Medium" if "medium" in model else "Low" if "low" in model else "Auto")
    btn_thinking = t("kb.thinking", lang, effort=effort_title)

    btn_sessions = t("kb.sessions", lang)
    btn_new_session = t("kb.new_session", lang)

    return {
        "keyboard": [
            [{"text": btn_mode}, {"text": btn_limits}],
            [{"text": btn_model}, {"text": btn_thinking}],
            [{"text": btn_sessions}, {"text": btn_new_session}],
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
        f"⚡️ Стриминг:   <b>{streaming_str}</b>\n"
        f"🔍 Логи шагов: <b>{actions_str}</b>\n"
        "\n"
        "📁 <b>Этот чат:</b>\n"
        f"  Сессия:   {session}{conv_info}\n"
        f"  Запросов: {cs.turn_count}\n"
        f"  Рабочая папка: <code>{workdir}</code>"
    )


def _settings_keyboard(cs: "ChatState" | None = None) -> InlineKeyboard:
    lang = getattr(cs, "language", "ru") if cs else "ru"
    lang_btn = "🌐 Язык: RU 🇷🇺" if lang == "ru" else "🌐 Language: EN 🇬🇧"

    stream_on = cs is None or getattr(cs, "streaming", True)
    stream_lbl = f"⚡️ {t('settings.streaming', lang)}: {t('settings.enabled' if stream_on else 'settings.disabled', lang)}"

    actions_on = cs is None or getattr(cs, "verbose_actions", True)
    actions_lbl = f"🔍 {t('settings.actions', lang)}: {t('settings.enabled' if actions_on else 'settings.disabled', lang)}"

    diff_on = cs is None or getattr(cs, "send_diff_files", True)
    diff_lbl = f"📤 {t('settings.diff_files', lang)}: {t('settings.enabled' if diff_on else 'settings.disabled', lang)}"

    del_prog_on = cs is not None and getattr(cs, "delete_progress_on_finish", False)
    del_prog_lbl = f"🗑 {t('settings.delete_progress', lang)}: {t('settings.enabled' if del_prog_on else 'settings.disabled', lang)}"

    think_on = cs is None or getattr(cs, "show_thinking", True)
    think_lbl = f"💭 {t('settings.thinking', lang)}: {t('settings.enabled' if think_on else 'settings.disabled', lang)}"

    footer_on = cs is None or getattr(cs, "show_footer", True)
    footer_lbl = f"🏷 {t('settings.footer', lang)}: {t('settings.enabled' if footer_on else 'settings.disabled', lang)}"

    pin_on = cs is None or getattr(cs, "pinned_dashboard_enabled", True)
    pin_lbl = f"{t('settings.pinned', lang)}: {t('settings.enabled' if pin_on else 'settings.disabled', lang)}"

    return [
        [{"text": lang_btn, "callback_data": "tog:language"}],
        [
            {"text": stream_lbl, "callback_data": "tog:streaming"},
            {"text": actions_lbl, "callback_data": "tog:actions"},
        ],
        [
            {"text": diff_lbl, "callback_data": "tog:diff_files"},
            {"text": del_prog_lbl, "callback_data": "tog:delete_progress"},
        ],
        [
            {"text": think_lbl, "callback_data": "tog:thinking"},
            {"text": footer_lbl, "callback_data": "tog:footer"},
        ],
        [{"text": pin_lbl, "callback_data": "tog:pinned_dashboard"}],
        [
            {"text": "🤖 Модель", "callback_data": "nav:model"},
            {"text": "🛡 Режим", "callback_data": "nav:mode"},
            {"text": "💭 Мышление", "callback_data": "nav:effort"},
        ],
        [
            {"text": "📈 Лимиты", "callback_data": "nav:usage"},
            {"text": "📁 Сессии", "callback_data": "nav:sessions"},
        ],
        [
            {"text": "🧹 Сбросить сессию", "callback_data": "R"},
            {"text": "🔄 Обновить", "callback_data": "nav:settings"},
        ],
    ]


def _render_usage(cs: "ChatState", cfg: "Config", force: bool = False) -> BridgeReply:
    text = get_agy_usage(force=force)
    keyboard: InlineKeyboard = [
        [{"text": "🔄 Обновить квоту", "callback_data": "nav:usage:force"}],
        [{"text": "← Назад в настройки", "callback_data": "nav:settings"}],
    ]
    return BridgeReply(text=text, keyboard=keyboard, reply_markup=get_main_reply_keyboard(cs, cfg))


def _model_keyboard(current_per_chat: str) -> InlineKeyboard:
    rows: InlineKeyboard = []
    models_list = get_agy_models()
    # Two models per row for compactness
    row: list[dict[str, Any]] = []
    for mid, mtitle in models_list:
        marker = "● " if mid == current_per_chat else "○ "
        short_name = mid.replace("gemini-", "G-").replace("claude-", "C-").replace("-thinking", "-think")
        row.append({"text": marker + short_name, "callback_data": f"m:{mid}"})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    default_marker = "● " if not current_per_chat else "○ "
    rows.append([
        {"text": default_marker + "Использовать по умолчанию (конфиг)", "callback_data": f"m:{_DEFAULT_TOKEN}"}
    ])
    rows.append([
        {"text": "🔄 Обновить список моделей", "callback_data": "nav:model:force"},
        {"text": "← Назад в настройки", "callback_data": "nav:settings"},
    ])
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


def _effort_keyboard(current_effort: str) -> InlineKeyboard:
    efforts = [
        ("high", "💭 High (Глубокое)"),
        ("medium", "💭 Medium (Сбалансированное)"),
        ("low", "💭 Low (Быстрое)"),
        ("off", "🚫 Off (Выключено)"),
        ("", "✨ По умолчанию"),
    ]
    rows: InlineKeyboard = []
    for val, label in efforts:
        marker = "● " if current_effort == val else "○ "
        rows.append([{"text": marker + label, "callback_data": f"eff:{val or _DEFAULT_TOKEN}"}])
    rows.append([{"text": "← Назад в настройки", "callback_data": "nav:settings"}])
    return rows


def _render_effort_picker(cs: "ChatState", cfg: "Config") -> BridgeReply:
    cur = cs.effort or "по умолчанию"
    return BridgeReply(
        text=f"💭 <b>Уровень глубины рассуждений (Thinking Effort)</b>\n\nТекущий: <b>{cur.capitalize()}</b>",
        keyboard=_effort_keyboard(cs.effort),
        reply_markup=get_main_reply_keyboard(cs, cfg),
    )


def _render_settings(cs: "ChatState", cfg: "Config") -> BridgeReply:
    return BridgeReply(
        text=render_status(cs, cfg),
        keyboard=_settings_keyboard(cs),
        reply_markup=get_main_reply_keyboard(cs, cfg),
    )


def _render_sessions_picker(cs: "ChatState", cfg: "Config | None" = None) -> BridgeReply:
    active = cs.conversation_id or "(самая последняя)"
    return BridgeReply(
        text=f"📁 <b>История сессий Antigravity:</b>\n\nТекущая сессия: <code>{active}</code>\n\nВыберите сессию из списка ниже, чтобы переключиться на неё, или создайте новую:",
        keyboard=_sessions_keyboard(cs.conversation_id),
        reply_markup=get_main_reply_keyboard(cs, cfg),
    )


def _render_model_picker(cs: "ChatState", cfg: "Config") -> BridgeReply:
    cur, src = _effective_model(cs, cfg)
    return BridgeReply(
        text=f"🤖 Выберите модель для этого чата\n\nТекущая: {cur} [{src}]",
        keyboard=_model_keyboard(cs.model),
        reply_markup=get_main_reply_keyboard(cs, cfg),
    )


def _render_mode_picker(cs: "ChatState", cfg: "Config") -> BridgeReply:
    cur, src = _effective_mode(cs, cfg)
    return BridgeReply(
        text=f"🛡 Выберите режим работы для этого чата\n\nТекущий: {cur} [{src}]",
        keyboard=_mode_keyboard(cs.mode),
        reply_markup=get_main_reply_keyboard(cs, cfg),
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

    lang = getattr(cs, "language", "ru")

    # Handle Bottom 2x2 Reply Keyboard clicks (only when not a slash command)
    if not raw_text.startswith("/"):
        if raw_text.startswith("🛠") or raw_text.startswith("Режим:") or raw_text.startswith("Mode:") or raw_text in ("Режим", "Mode", "🛠 Режим", "🛠 Mode"):
            return _render_mode_picker(cs, cfg)
        if raw_text.startswith("📊") or raw_text in ("Лимиты", "Limits", "Квота", "Quota", "📈 Лимиты"):
            return _render_usage(cs, cfg)
        if raw_text.startswith("🤖") or raw_text in ("Модель", "Model", "Выбор модели", "🤖 Выбор модели") or any(raw_text.startswith(m) for m in ("Gemini", "Claude", "GPT", "DeepSeek")):
            return _render_model_picker(cs, cfg)
        if raw_text.startswith("💭") or raw_text in ("Мышление", "Thinking", "Уровень мышления", "High", "Medium", "Low", "Auto", "Авто", "Off"):
            return _render_effort_picker(cs, cfg)

        # Sessions & New Session buttons
        if raw_text in ("📁 Сессии", "Сессии", "📁 Sessions", "Sessions"):
            return _render_sessions_picker(cs, cfg)
        if raw_text in ("➕ Новая сессия", "Новая сессия", "➕ New Session", "New Session", "🧹 Новый диалог", "Новый диалог"):
            cs.has_session = False
            cs.conversation_id = ""
            try:
                import asyncio
                from src.agy_runner import stop_resident_worker
                asyncio.create_task(stop_resident_worker(cs.chat_dir))
            except Exception:
                pass
            return BridgeReply(
                t("session.new_created", lang),
                reply_markup=get_main_reply_keyboard(cs, cfg),
            )
        if raw_text in ("⚙️ Настройки", "Настройки"):
            return _render_settings(cs, cfg)
        if raw_text in ("📊 Статус", "Статус"):
            return BridgeReply(render_status(cs, cfg), reply_markup=get_main_reply_keyboard(cs, cfg))

    # Handle Slash Commands
    parts = raw_text.split(maxsplit=1)
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    if cmd == "/start":
        return BridgeReply(t("welcome.text", lang), reply_markup=get_main_reply_keyboard(cs, cfg))
    if cmd == "/help":
        return BridgeReply(t("help.text", lang), reply_markup=get_main_reply_keyboard(cs, cfg))
    if cmd in ("/status", "/info"):
        return BridgeReply(render_status(cs, cfg), reply_markup=get_main_reply_keyboard(cs, cfg))
    if cmd in ("/usage", "/limits", "/quota", "/yousage", "/you_sage") or raw_text.lower().startswith("/you sage"):
        return _render_usage(cs, cfg)
    if cmd == "/sessions":
        return _render_sessions_picker(cs, cfg)
    if cmd in ("/pin", "/dashboard"):
        from src.pinned import format_pinned_dashboard
        dash_text, dash_kbd = format_pinned_dashboard(cs, cfg)
        return BridgeReply(dash_text, keyboard=dash_kbd, reply_markup=get_main_reply_keyboard(cs, cfg))
    if cmd == "/settings":
        return _render_settings(cs, cfg)
    if cmd in ("/stream", "/streaming"):
        cs.streaming = not getattr(cs, "streaming", True)
        state_str = "<b>ВКЛЮЧЕН</b> ✅" if cs.streaming else "<b>ВЫКЛЮЧЕН</b> ❌"
        return BridgeReply(f"⚡️ Стриминг ответов в реальном времени: {state_str}", reply_markup=get_main_reply_keyboard(cs, cfg))
    if cmd in ("/actions", "/verbose", "/steps"):
        cs.verbose_actions = not getattr(cs, "verbose_actions", True)
        state_str = "<b>ВКЛЮЧЕНЫ</b> ✅" if cs.verbose_actions else "<b>ВЫКЛЮЧЕНЫ</b> ❌"
        return BridgeReply(f"🔍 Детальные логи действий: {state_str}", reply_markup=get_main_reply_keyboard(cs, cfg))
    if cmd in ("/lang", "/language"):
        cs.language = "en" if getattr(cs, "language", "ru") == "ru" else "ru"
        return BridgeReply(f"🌐 Язык интерфейса: <b>{cs.language.upper()}</b>", reply_markup=get_main_reply_keyboard(cs, cfg))
    if cmd in ("/ls", "/dir", "/browser", "/browse"):
        req_path = args.strip() or cs.chat_dir
        header, kbd = render_file_browser(req_path, cs.chat_dir, page=1, lang=lang)
        return BridgeReply(text=header, keyboard=kbd, reply_markup=get_main_reply_keyboard(cs, cfg))
    if cmd in ("/model", "/models"):
        if args:
            if is_valid_model(args):
                cs.model = args
                sync_antigravity_settings(args)
                return BridgeReply(f"🤖 Модель установлена: {args}", reply_markup=get_main_reply_keyboard(cs, cfg))
            return BridgeReply(f"⚠️ Неподдерживаемая модель: {args}", reply_markup=get_main_reply_keyboard(cs, cfg))
        return _render_model_picker(cs, cfg)
    if cmd == "/mode":
        if args:
            if args in {"code", "plan"}:
                cs.mode = args
                return BridgeReply(f"🛡 Режим установлен: {args}", reply_markup=get_main_reply_keyboard(cs, cfg))
            return BridgeReply(f"⚠️ Неподдерживаемый режим: {args}", reply_markup=get_main_reply_keyboard(cs, cfg))
        return _render_mode_picker(cs, cfg)
    if cmd in ("/reset", "/new"):
        cs.has_session = False
        cs.conversation_id = ""
        try:
            import asyncio
            from src.agy_runner import stop_resident_worker
            asyncio.create_task(stop_resident_worker(cs.chat_dir))
        except Exception:
            pass
        return BridgeReply(t("session.new_created", lang), reply_markup=get_main_reply_keyboard(cs, cfg))
    if cmd == "/compact":
        return BridgeReply("🗜️ Сжатие контекста выполняется автоматически движком Antigravity.", reply_markup=get_main_reply_keyboard(cs, cfg))
    if cmd in ("/download", "/get", "/file"):
        if not args:
            return BridgeReply("ℹ️ Укажите имя файла для скачивания, например: <code>/download main.py</code>", reply_markup=get_main_reply_keyboard(cs, cfg))
        p = Path(args)
        if not p.is_absolute():
            p = Path(cs.chat_dir) / p
        if not p.exists():
            inbox_p = Path(cs.chat_dir) / ".bridge-inbox" / args
            if inbox_p.exists():
                p = inbox_p
            else:
                return BridgeReply(f"⚠️ Файл не найден: <code>{args}</code>", reply_markup=get_main_reply_keyboard(cs, cfg))
        if p.is_dir():
            import shutil
            import tempfile
            zip_tmp = Path(tempfile.gettempdir()) / f"{p.name}"
            archive_path = shutil.make_archive(str(zip_tmp), "zip", str(p))
            return BridgeReply(text=f"SEND_FILE:{archive_path}:{p.name}.zip", reply_markup=get_main_reply_keyboard(cs, cfg))
        return BridgeReply(text=f"SEND_FILE:{p}:{p.name}", reply_markup=get_main_reply_keyboard(cs, cfg))

    if cmd in ("/thinking", "/effort", "/reasoning"):
        effort_val = args.strip().lower()
        if effort_val in ("low", "medium", "high", "off", "none", ""):
            cs.effort = effort_val
            return BridgeReply(f"🧠 Уровень мышления установлен: <b>{effort_val or 'по умолчанию'}</b>", reply_markup=get_main_reply_keyboard(cs, cfg))
        if not args:
            return _render_effort_picker(cs, cfg)
        return BridgeReply(f"⚠️ Неверный уровень мышления: <code>{args}</code> (доступно: low, medium, high, off)", reply_markup=get_main_reply_keyboard(cs, cfg))

    if cmd in ("/stop", "/cancel", "/kill", "/abort") or raw_text.lower() in (
        "стоп", "отмена", "отменить", "остановить", "🛑 остановить", "прервать", "pause", "пауза", "стоп!", "стоп."
    ):
        return BridgeReply(text="INTERNAL_STOP", reply_markup=get_main_reply_keyboard(cs, cfg))

    if cmd == "/files":
        req_path = cs.chat_dir
        header, kbd = render_file_browser(req_path, cs.chat_dir, page=1, lang=lang)
        return BridgeReply(text=header, keyboard=kbd, reply_markup=get_main_reply_keyboard(cs, cfg))

    return None


def handle_callback(
    cq: "CallbackQuery",
    cs: "ChatState",
    cfg: "Config",
) -> BridgeReply:
    """Handle inline-keyboard button taps. Always returns a reply to render."""
    data = cq.data
    lang = getattr(cs, "language", "ru")

    if data == "nav:status":
        return BridgeReply(render_status(cs, cfg), reply_markup=get_main_reply_keyboard(cs, cfg))
    if data == "nav:settings":
        return _render_settings(cs, cfg)
    if data == "tog:language":
        cs.language = "en" if getattr(cs, "language", "ru") == "ru" else "ru"
        toast = f"Language: {cs.language.upper()}"
        rep = _render_settings(cs, cfg)
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(cs, cfg), toast=toast)
    if data == "tog:streaming":
        cs.streaming = not getattr(cs, "streaming", True)
        toast = "Стриминг: ВКЛ" if cs.streaming else "Стриминг: ВЫКЛ"
        rep = _render_settings(cs, cfg)
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(cs, cfg), toast=toast)
    if data == "tog:actions":
        cs.verbose_actions = not getattr(cs, "verbose_actions", True)
        toast = "Логи действий: ВКЛ" if cs.verbose_actions else "Логи действий: ВЫКЛ"
        rep = _render_settings(cs, cfg)
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(cs, cfg), toast=toast)
    if data == "tog:diff_files":
        cs.send_diff_files = not getattr(cs, "send_diff_files", True)
        toast = "Отправка файлов: ВКЛ" if cs.send_diff_files else "Отправка файлов: ВЫКЛ"
        rep = _render_settings(cs, cfg)
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(cs, cfg), toast=toast)
    if data == "tog:delete_progress":
        cs.delete_progress_on_finish = not getattr(cs, "delete_progress_on_finish", False)
        toast = "Удаление прогресса: ВКЛ" if cs.delete_progress_on_finish else "Удаление прогресса: ВЫКЛ"
        rep = _render_settings(cs, cfg)
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(cs, cfg), toast=toast)
    if data == "tog:thinking":
        cs.show_thinking = not getattr(cs, "show_thinking", True)
        toast = "Блок мышления: ВКЛ" if cs.show_thinking else "Блок мышления: ВЫКЛ"
        rep = _render_settings(cs, cfg)
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(cs, cfg), toast=toast)
    if data == "tog:footer":
        cs.show_footer = not getattr(cs, "show_footer", True)
        toast = "Футер ответа: ВКЛ" if cs.show_footer else "Футер ответа: ВЫКЛ"
        rep = _render_settings(cs, cfg)
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(cs, cfg), toast=toast)
    if data == "tog:pinned_dashboard":
        cs.pinned_dashboard_enabled = not getattr(cs, "pinned_dashboard_enabled", True)
        toast = "Дашборд: ВКЛ" if cs.pinned_dashboard_enabled else "Дашборд: ВЫКЛ"
        rep = _render_settings(cs, cfg)
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(cs, cfg), toast=toast)

    # Pinned Dashboard callbacks
    if data == "pin:refresh":
        from src.pinned import format_pinned_dashboard
        text, kbd = format_pinned_dashboard(cs, cfg)
        return BridgeReply(text=text, keyboard=kbd, reply_markup=get_main_reply_keyboard(cs, cfg), toast="Дашборд обновлён 🔄")

    if data == "pin:new":
        cs.has_session = False
        cs.conversation_id = ""
        try:
            import asyncio
            from src.agy_runner import stop_resident_worker
            asyncio.create_task(stop_resident_worker(cs.chat_dir))
        except Exception:
            pass
        from src.pinned import format_pinned_dashboard
        text, kbd = format_pinned_dashboard(cs, cfg)
        return BridgeReply(text=text, keyboard=kbd, reply_markup=get_main_reply_keyboard(cs, cfg), toast="Новая сессия создана ✅")

    if data == "pin:sessions":
        rep = _render_sessions_picker(cs, cfg)
        kbd = list(rep.keyboard or [])
        kbd.append([{"text": "← Назад к дашборду", "callback_data": "pin:refresh"}])
        return BridgeReply(text=rep.text, keyboard=kbd, reply_markup=get_main_reply_keyboard(cs, cfg), toast="История сессий")

    if data == "pin:settings":
        rep = _render_settings(cs, cfg)
        kbd = list(rep.keyboard or [])
        kbd.append([{"text": "← Назад к дашборду", "callback_data": "pin:refresh"}])
        return BridgeReply(text=rep.text, keyboard=kbd, reply_markup=get_main_reply_keyboard(cs, cfg), toast="Настройки")

    if data == "nav:usage":
        return _render_usage(cs, cfg, force=False)
    if data == "nav:usage:force":
        return _render_usage(cs, cfg, force=True)
    if data == "nav:sessions":
        return _render_sessions_picker(cs, cfg)
    if data == "nav:model":
        return _render_model_picker(cs, cfg)
    if data == "nav:model:force":
        get_agy_models(force=True)
        rep = _render_model_picker(cs, cfg)
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(cs, cfg), toast="Список моделей обновлён")
    if data == "nav:mode":
        return _render_mode_picker(cs, cfg)
    if data == "nav:effort":
        return _render_effort_picker(cs, cfg)

    if data.startswith("eff:"):
        choice = data[4:]
        if choice == _DEFAULT_TOKEN:
            cs.effort = ""
            toast = "Мышление: Авто"
        else:
            cs.effort = choice
            toast = f"Мышление: {choice.capitalize()}"
        rep = _render_effort_picker(cs, cfg)
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(cs, cfg), toast=toast)

    if data == "R":
        cs.has_session = False
        cs.conversation_id = ""
        try:
            import asyncio
            from src.agy_runner import stop_resident_worker
            asyncio.create_task(stop_resident_worker(cs.chat_dir))
        except Exception:
            pass
        rep = _render_settings(cs, cfg)
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(cs, cfg), toast="Сессия сброшена")

    # Session selection
    if data.startswith("s:"):
        choice = data[2:]
        if choice == "new":
            cs.has_session = False
            cs.conversation_id = ""
            try:
                import asyncio
                from src.agy_runner import stop_resident_worker
                asyncio.create_task(stop_resident_worker(cs.chat_dir))
            except Exception:
                pass
            toast = "Создана новая сессия"
        else:
            cs.conversation_id = choice
            cs.has_session = True
            toast = f"Выбрана сессия {choice[:8]}…"

        is_pinned = (cq.message_id == getattr(cs, "pinned_message_id", None))
        if is_pinned:
            from src.pinned import format_pinned_dashboard
            dash_text, dash_kbd = format_pinned_dashboard(cs, cfg)
            return BridgeReply(text=dash_text, keyboard=dash_kbd, reply_markup=get_main_reply_keyboard(cs, cfg), toast=toast)

        rep = _render_sessions_picker(cs, cfg)
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(cs, cfg), toast=toast)

    # Model selection
    if data.startswith("m:"):
        choice = data[2:]
        available_model_ids = {mid for mid, _ in get_agy_models()}
        if choice == _DEFAULT_TOKEN:
            cs.model = ""
            toast = "По умолчанию (конфиг)"
        elif choice in available_model_ids or is_valid_model(choice):
            cs.model = choice
            sync_antigravity_settings(choice)
            toast = f"Модель: {choice}"
        else:
            toast = "Неизвестный выбор"
        rep = _render_model_picker(cs, cfg)
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(cs, cfg), toast=toast)

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
        return BridgeReply(text=rep.text, keyboard=rep.keyboard, reply_markup=get_main_reply_keyboard(cs, cfg), toast=toast)

    # File browser actions
    if data.startswith("fb:"):
        fb_parts = data.split(":")
        action = fb_parts[1]
        if action == "d":
            target_path = decode_path(fb_parts[2])
            target_page = int(fb_parts[3]) if len(fb_parts) > 3 else 1
            header, kbd = render_file_browser(target_path, cs.chat_dir, page=target_page, lang=lang)
            return BridgeReply(text=header, keyboard=kbd, reply_markup=get_main_reply_keyboard(cs, cfg))
        elif action == "f":
            fpath = decode_path(fb_parts[2])
            fname = Path(fpath).name
            return BridgeReply(text=f"SEND_FILE:{fpath}:{fname}", reply_markup=get_main_reply_keyboard(cs, cfg), toast=f"Отправка {fname}...")
        elif action == "close":
            return BridgeReply(text="📁 Файловый браузер закрыт.", reply_markup=get_main_reply_keyboard(cs, cfg))
        elif action == "noop":
            return BridgeReply(text="", toast="")

    return _render_settings(cs, cfg)
