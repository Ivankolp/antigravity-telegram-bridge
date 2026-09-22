"""Internationalization (i18n) for Antigravity Telegram Bridge.

Supports Russian (ru) and English (en) localizations.
"""
from __future__ import annotations

from typing import Any

TRANSLATIONS: dict[str, dict[str, str]] = {
    "ru": {
        # Reply Keyboard
        "kb.mode": "🛠 Режим: {mode}",
        "kb.mode_code": "Code",
        "kb.mode_plan": "Plan",
        "kb.limits": "📊 Лимиты",
        "kb.model": "🤖 {model}",
        "kb.thinking": "💭 {effort}",
        "kb.thinking_default": "💭 Авто",

        # Welcome & Help
        "welcome.text": (
            "👋 Привет! Я бот для управления Antigravity (agy CLI).\n\n"
            "Вы можете писать мне запросы текстом, отправлять голосовые сообщения или пользоваться кнопками внизу клавиатуры для управления режимами, моделями и настройками.\n\n"
            "🚀 Готов к работе!"
        ),
        "help.text": (
            "📖 <b>Antigravity Bridge — Справка</b>\n\n"
            "Отправьте любой текст, голос или код для выполнения в agy.\n\n"
            "<b>Нижнее меню быстрого доступа:</b>\n"
            "• 🛠 Режим — переключение между Кодом (Code) и Планом (Plan)\n"
            "• 📊 Лимиты — мгновенная проверка оставшейся квоты\n"
            "• 🤖 Модель — выбор нейросети (Gemini 3.8, Claude, GPT)\n"
            "• 💭 Мышление — уровень глубины рассуждений (High, Medium, Low)\n\n"
            "<b>Команды бота:</b>\n"
            "• /ls [путь] — интерактивный файловый браузер проекта\n"
            "• /settings — подробные параметры (язык, файлы, стриминг)\n"
            "• /sessions — история сессий и переключение\n"
            "• /reset — сбросить контекст и начать с чистого листа\n"
            "• /stop — экстренно остановить текущую задачу\n"
            "• /download [файл] — скачать файл или директорию архивом\n"
            "• /status — подробный статус текущей сессии"
        ),

        # Progress
        "progress.working": "⏳ Работаю...",
        "progress.thinking": "⏳ Думаю...",
        "progress.responding": "⏳ Генерирую ответ...",
        "progress.done": "✅ Работа завершена",
        "progress.summary": "🛠 Вызовы инструментов: {tools} · 📄 Изменённые файлы: {files} · ⏱ {elapsed}s",
        "progress.thinking_header": "💭 Мышление модели:",

        # Tools
        "tool.read": "📖 Читаю: <code>{path}</code>",
        "tool.write": "✍️ Записываю: <code>{path}</code> (+{lines})",
        "tool.edit": "✏️ Редактирую: <code>{path}</code> (+{additions} -{deletions})",
        "tool.bash": "💻 Команда: <code>{cmd}</code>",
        "tool.search": "🌐 Поиск в сети: <i>{query}</i>",
        "tool.grep": "🔍 Поиск по коду: <code>{pattern}</code>",
        "tool.subagent": "🤖 Субагент: <b>{role}</b>",
        "tool.generic": "🛠 Действие: <code>{name}</code>",

        # File Delivery Captions
        "file.caption_write": "✍️ Записан файл: <code>{path}</code> (+{lines} строк)",
        "file.caption_edit": "✏️ Изменён файл: <code>{path}</code> (+{additions} -{deletions})",
        "file.caption_generic": "📄 Файл: <code>{path}</code> ({size})",

        # Footer
        "footer.text": "🛠 {mode} · 🧠 {model} · ⏱ {elapsed}s",

        # Settings
        "settings.title": "⚙️ <b>Настройки Antigravity Bridge</b>",
        "settings.lang": "🌐 Язык: Русский 🇷🇺",
        "settings.streaming": "⚡️ Стриминг",
        "settings.actions": "🔍 Логи шагов",
        "settings.diff_files": "📤 Отправка файлов",
        "settings.delete_progress": "🗑 Удалять прогресс",
        "settings.thinking": "💭 Блок мышления",
        "settings.footer": "🏷 Футер ответа",
        "settings.enabled": "ВКЛ ✅",
        "settings.disabled": "ВЫКЛ ❌",
        "settings.back": "← Назад",
        "settings.refresh": "🔄 Обновить",

        # File Browser
        "browser.title": "📁 <b>Файловый менеджер проекта</b>\nПуть: <code>{path}</code>",
        "browser.empty": "<i>(директория пуста)</i>",
        "browser.up": "⬆️ Вверх",
        "browser.attach_hint": "📎 Файл добавлен к контексту следующего запроса: <code>{path}</code>",
        "browser.downloaded": "📄 Отправляю файл <code>{name}</code>...",

        # System
        "session.new_created": "🧹 Создана новая сессия! Следующий запрос начнётся с чистого листа.",
        "stop.stopped": "🛑 Выполнение задачи остановлено.",
        "stop.no_active": "ℹ️ Сейчас нет активных выполняющихся задач.",
        "error.generic": "⚠️ Ошибка: {error}",
        "error.quota": "⏳ <b>Лимит запросов исчерпан (429 Rate Limit / Quota)</b>\n\n{usage}\n\n💡 <i>Лимиты восстановятся в указанное время. Переключите модель через нижнее меню или подождите сброса.</i>",
    },
    "en": {
        # Reply Keyboard
        "kb.mode": "🛠 Mode: {mode}",
        "kb.mode_code": "Code",
        "kb.mode_plan": "Plan",
        "kb.limits": "📊 Limits",
        "kb.model": "🤖 {model}",
        "kb.thinking": "💭 {effort}",
        "kb.thinking_default": "💭 Auto",

        # Welcome & Help
        "welcome.text": (
            "👋 Hello! I am the Antigravity (agy CLI) Bridge Bot.\n\n"
            "You can send text queries, voice messages, or use the bottom keyboard to manage modes, models, and settings.\n\n"
            "🚀 Ready to assist!"
        ),
        "help.text": (
            "📖 <b>Antigravity Bridge — Help</b>\n\n"
            "Send any prompt, voice message, or code to run in agy.\n\n"
            "<b>Bottom Quick Access Menu:</b>\n"
            "• 🛠 Mode — switch between Code and Plan\n"
            "• 📊 Limits — check quota and remaining limits\n"
            "• 🤖 Model — choose model (Gemini 3.8, Claude, GPT)\n"
            "• 💭 Thinking — reasoning effort level (High, Medium, Low)\n\n"
            "<b>Bot Commands:</b>\n"
            "• /ls [path] — interactive project file browser\n"
            "• /settings — detailed settings (language, files, streaming)\n"
            "• /sessions — session history and switching\n"
            "• /reset — clear context and start fresh\n"
            "• /stop — abort running task\n"
            "• /download [file] — download file or directory as zip\n"
            "• /status — detailed session status"
        ),

        # Progress
        "progress.working": "⏳ Working...",
        "progress.thinking": "⏳ Thinking...",
        "progress.responding": "⏳ Generating reply...",
        "progress.done": "✅ Completed",
        "progress.summary": "🛠 Tool calls: {tools} · 📄 Modified files: {files} · ⏱ {elapsed}s",
        "progress.thinking_header": "💭 Model Thinking:",

        # Tools
        "tool.read": "📖 Reading: <code>{path}</code>",
        "tool.write": "✍️ Writing: <code>{path}</code> (+{lines})",
        "tool.edit": "✏️ Editing: <code>{path}</code> (+{additions} -{deletions})",
        "tool.bash": "💻 Command: <code>{cmd}</code>",
        "tool.search": "🌐 Web search: <i>{query}</i>",
        "tool.grep": "🔍 Code search: <code>{pattern}</code>",
        "tool.subagent": "🤖 Subagent: <b>{role}</b>",
        "tool.generic": "🛠 Action: <code>{name}</code>",

        # File Delivery Captions
        "file.caption_write": "✍️ Written file: <code>{path}</code> (+{lines} lines)",
        "file.caption_edit": "✏️ Edited file: <code>{path}</code> (+{additions} -{deletions})",
        "file.caption_generic": "📄 File: <code>{path}</code> ({size})",

        # Footer
        "footer.text": "🛠 {mode} · 🧠 {model} · ⏱ {elapsed}s",

        # Settings
        "settings.title": "⚙️ <b>Antigravity Bridge Settings</b>",
        "settings.lang": "🌐 Language: English 🇬🇧",
        "settings.streaming": "⚡️ Streaming",
        "settings.actions": "🔍 Step logs",
        "settings.diff_files": "📤 Send files",
        "settings.delete_progress": "🗑 Auto-delete progress",
        "settings.thinking": "💭 Thinking block",
        "settings.footer": "🏷 Reply footer",
        "settings.enabled": "ON ✅",
        "settings.disabled": "OFF ❌",
        "settings.back": "← Back",
        "settings.refresh": "🔄 Refresh",

        # File Browser
        "browser.title": "📁 <b>Project File Browser</b>\nPath: <code>{path}</code>",
        "browser.empty": "<i>(directory is empty)</i>",
        "browser.up": "⬆️ Up",
        "browser.attach_hint": "📎 File attached to next prompt context: <code>{path}</code>",
        "browser.downloaded": "📄 Sending file <code>{name}</code>...",

        # System
        "session.new_created": "🧹 New session created! Your next prompt will start from scratch.",
        "stop.stopped": "🛑 Task execution stopped.",
        "stop.no_active": "ℹ️ No active tasks currently running.",
        "error.generic": "⚠️ Error: {error}",
        "error.quota": "⏳ <b>Rate Limit / Quota Exceeded (429)</b>\n\n{usage}\n\n💡 <i>Limits will recover at the specified time. Switch model via bottom menu or wait for reset.</i>",
    },
}


def t(key: str, lang: str = "ru", **kwargs: Any) -> str:
    """Retrieve translated string with interpolation."""
    lang_dict = TRANSLATIONS.get(lang) or TRANSLATIONS["ru"]
    template = lang_dict.get(key)
    if template is None:
        template = TRANSLATIONS["ru"].get(key, key)
    if kwargs:
        try:
            return template.format(**kwargs)
        except Exception:
            return template
    return template
