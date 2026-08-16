"""Media handling — photo download, file upload, inbox management."""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from src.config import Config
    from src.state import ChatState
    from src.telegram import InboundMessage, TelegramClient

MAX_PHOTO_SIZE = 10 * 1024 * 1024
MAX_FILE_SIZE = 50 * 1024 * 1024

ALLOWED_IMAGE_MIMES = frozenset({
    "image/jpeg", "image/png", "image/webp", "image/gif",
})

ALLOWED_DOC_MIMES = frozenset({
    "text/plain", "text/x-python", "text/x-script.python",
    "text/javascript", "text/x-go", "text/x-rust",
    "text/markdown", "text/csv", "text/html", "text/css",
    "application/pdf", "application/json", "application/x-yaml",
    "application/x-toml", "application/xml", "application/zip",
    "application/x-tar", "application/gzip",
})

INBOX_DIR_NAME = ".bridge-inbox"


def is_allowed_image(mime: str) -> bool:
    return mime in ALLOWED_IMAGE_MIMES


def is_allowed_document(mime: str, filename: str = "") -> bool:
    if mime in ALLOWED_DOC_MIMES:
        return True
    ext = Path(filename).suffix.lower() if filename else ""
    safe_exts = {".py", ".js", ".ts", ".go", ".rs", ".md",
                 ".json", ".yaml", ".yml", ".toml", ".txt",
                 ".csv", ".html", ".css", ".pdf", ".xml", ".sh", ".env",
                 ".zip", ".tar", ".gz", ".sql", ".log"}
    return ext in safe_exts and not ext.startswith(".com")


async def download_photo(tg: "TelegramClient", file_id: str) -> bytes:
    return await tg.get_file(file_id)


async def download_document(tg: "TelegramClient", file_id: str) -> bytes:
    return await tg.get_file(file_id)


async def transcribe_voice(
    tg: "TelegramClient",
    file_id: str,
    api_key: str,
    mime_type: str = "audio/ogg",
) -> str:
    data = await tg.get_file(file_id)
    # Use language=multi for seamless Russian + English code-switching in voice messages
    url = "https://api.deepgram.com/v1/listen?model=nova-3&language=multi&smart_format=true&paragraphs=true&numerals=true"
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": mime_type,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=headers, content=data)
        if resp.status_code != 200:
            raise RuntimeError(f"Deepgram error {resp.status_code}: {resp.text}")
        payload = resp.json()
        results = payload.get("results") or {}
        channels = results.get("channels") or []
        if channels:
            alts = channels[0].get("alternatives") or []
            if alts:
                return alts[0].get("transcript", "").strip()
    return ""


def save_to_inbox(workdir: str, filename: str, data: bytes) -> Path:
    inbox = Path(workdir) / INBOX_DIR_NAME
    inbox.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    dest = inbox / f"{ts}_{filename}"
    dest.write_bytes(data)
    # Also copy clean file directly to workdir for easy access
    direct_dest = Path(workdir) / filename
    try:
        direct_dest.write_bytes(data)
    except Exception:
        pass
    return dest


def clean_inbox(workdir: str, max_age_hours: int = 24) -> int:
    inbox = Path(workdir) / INBOX_DIR_NAME
    if not inbox.exists():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for f in inbox.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            f.unlink()
            removed += 1
    return removed


def list_inbox(workdir: str, limit: int = 5) -> list[str]:
    inbox = Path(workdir) / INBOX_DIR_NAME
    if not inbox.exists():
        return []
    files = sorted(inbox.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
    result: list[str] = []
    for f in files[:limit]:
        size = f.stat().st_size
        sz = f"{size}B" if size < 1024 else f"{size//1024}K"
        result.append(f"{f.name} ({sz})")
    return result


async def build_media_prompt(
    msg: "InboundMessage",
    tg: "TelegramClient",
    state: "ChatState",
    cfg: "Config",
) -> str | None:
    """Build prompt from text + media + voice transcription. Returns None if rejected."""
    parts: list[str] = []
    
    # 1. Forwarded message header
    fw_orig = getattr(msg, "forward_origin", None)
    if fw_orig:
        if getattr(msg, "text", ""):
            parts.append(f"[Пересланное сообщение от {fw_orig}]:\n{msg.text}")
        else:
            parts.append(f"[Пересланное сообщение от {fw_orig}]")
    elif getattr(msg, "text", ""):
        parts.append(msg.text)

    wd = state.chat_dir

    # 2. Voice / Audio / Video Note Transcription
    voice_obj = getattr(msg, "voice", None) or getattr(msg, "audio", None) or getattr(msg, "video_note", None)
    if voice_obj:
        deepgram_key = getattr(cfg.telegram, "deepgram_api_key", "") if hasattr(cfg, "telegram") else ""
        if deepgram_key:
            try:
                await tg.send_chat_action(msg.chat_id, "record_voice")
                file_id = voice_obj.get("file_id") if isinstance(voice_obj, dict) else None
                if file_id:
                    mime = "audio/ogg" if getattr(msg, "voice", None) else ("video/mp4" if getattr(msg, "video_note", None) else "audio/mpeg")
                    transcript = await transcribe_voice(tg, file_id, deepgram_key, mime_type=mime)
                    if transcript:
                        fwd_note = f" <i>(переслано от {fw_orig})</i>" if fw_orig else ""
                        await tg.send_message(msg.chat_id, f"🎙 <i>«{transcript}»</i>{fwd_note}")
                        parts.append(transcript)
                    else:
                        await tg.send_message(msg.chat_id, "⚠️ Голосовое сообщение не удалось распознать (пустой текст).")
                        return None
            except Exception as err:
                await tg.send_message(msg.chat_id, f"⚠️ Ошибка транскрибации: {err}")
                return None
        else:
            await tg.send_message(msg.chat_id, "⚠️ Голосовые сообщения не настроены.")
            return None

    # 3. Photos
    msg_photo = getattr(msg, "photo", None)
    if msg_photo and getattr(state, "photo_enabled", True):
        prompt = await _handle_photo(msg, tg, wd)
        if prompt is None:
            return None
        parts.append(prompt)

    # 4. Documents
    msg_doc = getattr(msg, "document", None)
    if msg_doc:
        prompt = await _handle_document(msg, tg, wd)
        if prompt is None:
            return None
        parts.append(prompt)

    return " ".join(parts) if parts else None


async def _handle_photo(msg: "InboundMessage", tg: "TelegramClient", workdir: str) -> str | None:
    if not msg.photo:
        return None
    largest = max(msg.photo, key=lambda p: p.get("file_size", 0))
    if largest.get("file_size", 0) > MAX_PHOTO_SIZE:
        await tg.send_message(msg.chat_id, "📸 Фото слишком большое (максимум 10MB)")
        return None
    try:
        data = await download_photo(tg, largest["file_id"])
        path = save_to_inbox(workdir, f"photo_{largest['file_id'][:12]}.jpg", data)
        clean_inbox(workdir)
        return f"[Фото сохранено: {path} — {len(data)//1024}KB]"
    except Exception as e:
        await tg.send_message(msg.chat_id, f"⚠️ Не удалось загрузить фото: {e}")
        return None


async def _handle_document(msg: "InboundMessage", tg: "TelegramClient", workdir: str) -> str | None:
    if not msg.document:
        return None
    doc = msg.document
    fname = doc.get("file_name", "unknown")
    mime = doc.get("mime_type", "")
    fsize = doc.get("file_size", 0)
    if fsize > MAX_FILE_SIZE:
        await tg.send_message(msg.chat_id, "📎 Файл слишком большой (максимум 50MB)")
        return None
    if not is_allowed_document(mime, fname):
        await tg.send_message(msg.chat_id, f"⛔ Неподдерживаемый тип файла: {fname}")
        return None
    try:
        data = await download_document(tg, doc["file_id"])
        path = save_to_inbox(workdir, fname, data)
        clean_inbox(workdir)
        await tg.send_message(msg.chat_id, f"📂 Файл <code>{fname}</code> сохранён в рабочий каталог проекта.")
        return f"[Файл: {path} — {len(data)//1024}KB]"
    except Exception as e:
        await tg.send_message(msg.chat_id, f"⚠️ Не удалось загрузить файл: {e}")
        return None
