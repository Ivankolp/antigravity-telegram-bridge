# 🚀 Antigravity CLI Telegram Bridge

Production-ready Telegram Bot Bridge for **Google Antigravity CLI (`agy`)**. Provides an asynchronous mobile & desktop interface for pair-programming, terminal execution, speech-to-text input, and project management.

---

## ✨ Features

- 🎙 **Voice Messages (Deepgram Nova-3):** Instant Russian speech-to-text recognition with smart punctuation.
- 💬 **Clean Markdown & Rich HTML:** Full support for syntax-highlighted code blocks, bold/italic formatting, blockquotes, and spoilers.
- 🛑 **Task Cancellation (`/stop`):** Gracefully interrupt long-running generations or terminal runs on the fly.
- 📁 **File Management (`/download` & Inbound Uploads):** Send files directly to workspace and download project files or zipped directories.
- 📈 **Quota & Rate Limit Monitor (`/usage`):** Instant cached quota checker with local timezone reset timestamps.
- ⏳ **Intelligent 429 Error Handling:** Intercepts rate limit/quota errors and provides exact countdown/reset times.
- 🛡 **Default-Deny Access Control:** Strict authorization by Telegram `user_id` and `chat_id`.

---

## 🛠 Tech Stack

- **Runtime:** Python 3.12+ (`asyncio`, `httpx`)
- **Speech Engine:** Deepgram Nova-3 API
- **AI Core:** Google Antigravity CLI (`agy`)
- **Process Manager:** `systemd`

---

## 🚀 Quick Start

### 1. Installation
```bash
git clone https://github.com/Ivankolp/antigravity-telegram-bridge.git
cd antigravity-telegram-bridge
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configuration (`config.json`)
Create `config.json` in the root directory:
```json
{
  "telegram": {
    "bot_token": "YOUR_TELEGRAM_BOT_TOKEN",
    "allowed_user_ids": [YOUR_USER_ID],
    "allowed_chat_ids": [YOUR_CHAT_ID],
    "deepgram_api_key": "YOUR_DEEPGRAM_API_KEY"
  },
  "agy": {
    "chats_root": "/root/agy_chats",
    "default_workdir": "/root/Projects",
    "model": "",
    "mode": "code"
  }
}
```

### 3. Run
```bash
# Run standalone
python -m src.daemon

# Or manage via systemd
systemctl start antigravity-bridge
```

---

## 📱 Bot Commands

| Command | Description |
| :--- | :--- |
| `📁 Сессии` (`/sessions`) | View session history and switch dialogs |
| `🧹 Новый диалог` (`/reset`) | Reset session and start clean context |
| `🤖 Выбор модели` (`/model`) | Switch model (Gemini 2.5 Flash, Claude 3.5 Sonnet, etc.) |
| `📈 Лимиты` (`/usage`) | Check 5-hour and weekly quota resets |
| `🛑 Отмена` (`/stop`, `стоп`) | Abort active execution |
| `📁 Скачать` (`/download <file>`) | Download file or zipped folder |

---

## 📄 License
MIT License. Created by [Ivankolp](https://github.com/Ivankolp).
