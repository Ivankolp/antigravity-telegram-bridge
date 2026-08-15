"""Telegram glue: pure helpers + async HTTP client with media + webhook support."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pathlib import Path

from src.config import TelegramConfig


@dataclass(frozen=True)
class InboundMessage:
    update_id: int
    chat_id: int
    user_id: int
    text: str
    photo: list[dict[str, Any]] | None = None
    document: dict[str, Any] | None = None
    voice: dict[str, Any] | None = None
    audio: dict[str, Any] | None = None
    video_note: dict[str, Any] | None = None
    forward_origin: str = ""


@dataclass(frozen=True)
class CallbackQuery:
    update_id: int
    callback_query_id: str
    chat_id: int
    user_id: int
    message_id: int
    data: str


# Telegram inline keyboards are nested lists of dicts: list[list[Button]].
InlineKeyboard = list[list[dict[str, Any]]]
ReplyKeyboard = dict[str, Any]


def _get_forward_origin(msg: dict[str, Any]) -> str:
    # 1. Telegram Bot API 7.0+ forward_origin
    fo = msg.get("forward_origin")
    if isinstance(fo, dict):
        fo_type = fo.get("type")
        if fo_type == "user" and isinstance(fo.get("sender_user"), dict):
            u = fo["sender_user"]
            name = u.get("first_name", "") + (" " + u.get("last_name", "") if u.get("last_name") else "")
            username = f" (@{u.get('username')})" if u.get("username") else ""
            return f"{name}{username}".strip()
        elif fo_type == "chat" and isinstance(fo.get("sender_chat"), dict):
            return str(fo["sender_chat"].get("title") or "Чат")
        elif fo_type == "channel" and isinstance(fo.get("chat"), dict):
            return str(fo["chat"].get("title") or "Канал")
        elif fo_type == "hidden_user":
            return str(fo.get("sender_user_name") or "Пользователь")

    # 2. Legacy forward fields
    ff = msg.get("forward_from")
    if isinstance(ff, dict):
        name = ff.get("first_name", "") + (" " + ff.get("last_name", "") if ff.get("last_name") else "")
        username = f" (@{ff.get('username')})" if ff.get("username") else ""
        return f"{name}{username}".strip()

    ffc = msg.get("forward_from_chat")
    if isinstance(ffc, dict):
        return str(ffc.get("title") or ffc.get("username") or "Чат")

    if msg.get("forward_sender_name"):
        return str(msg.get("forward_sender_name"))

    return ""


def parse_update(update: dict[str, Any]) -> InboundMessage | None:
    msg = update.get("message")
    if not isinstance(msg, dict):
        return None
    text = msg.get("text") or msg.get("caption") or ""
    photo = msg.get("photo")
    doc = msg.get("document")
    voice = msg.get("voice")
    audio = msg.get("audio")
    video_note = msg.get("video_note")
    if not text and photo is None and doc is None and voice is None and audio is None and video_note is None:
        return None
    chat = msg.get("chat") or {}
    sender = msg.get("from") or {}
    cid = chat.get("id")
    uid = sender.get("id")
    uid_n = update.get("update_id")
    if not isinstance(cid, int) or not isinstance(uid, int) or not isinstance(uid_n, int):
        return None
    forward_origin = _get_forward_origin(msg)
    return InboundMessage(
        update_id=uid_n,
        chat_id=cid,
        user_id=uid,
        text=text,
        photo=list(photo) if isinstance(photo, list) else None,
        document=doc if isinstance(doc, dict) else None,
        voice=voice if isinstance(voice, dict) else None,
        audio=audio if isinstance(audio, dict) else None,
        video_note=video_note if isinstance(video_note, dict) else None,
        forward_origin=forward_origin,
    )


def parse_callback_query(update: dict[str, Any]) -> CallbackQuery | None:
    cq = update.get("callback_query")
    if not isinstance(cq, dict):
        return None
    cq_id = cq.get("id")
    sender = cq.get("from") or {}
    user_id = sender.get("id")
    inner_msg = cq.get("message") or {}
    chat = inner_msg.get("chat") or {}
    chat_id = chat.get("id")
    message_id = inner_msg.get("message_id")
    data = cq.get("data")
    update_id = update.get("update_id")
    if (
        not isinstance(cq_id, str)
        or not isinstance(user_id, int)
        or not isinstance(chat_id, int)
        or not isinstance(message_id, int)
        or not isinstance(data, str)
        or not isinstance(update_id, int)
    ):
        return None
    return CallbackQuery(
        update_id=update_id,
        callback_query_id=cq_id,
        chat_id=chat_id,
        user_id=user_id,
        message_id=message_id,
        data=data,
    )


def is_authorized_user(user_id: int, cfg: TelegramConfig) -> bool:
    return user_id in cfg.allowed_user_ids


def is_authorized_chat(chat_id: int, cfg: TelegramConfig) -> bool:
    return not cfg.allowed_chat_ids or chat_id in cfg.allowed_chat_ids


def is_authorized(msg: InboundMessage, cfg: TelegramConfig) -> bool:
    if not is_authorized_user(msg.user_id, cfg):
        return False
    return is_authorized_chat(msg.chat_id, cfg)


def chunk_message(text: str, max_len: int = 4096) -> list[str]:
    if not text:
        return []
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, max_len)
        if cut == -1:
            cut = max_len
        chunks.append(remaining[:cut])
        if cut < len(remaining) and remaining[cut:cut + 1] == "\n":
            remaining = remaining[cut + 1:]
        else:
            remaining = remaining[cut:]
    return chunks


import html
import re
import httpx


def format_for_telegram(text: str) -> str:
    """Format markdown/HTML text into safe Telegram-compatible HTML."""
    if not text:
        return ""

    # 1. Protect existing code blocks (```lang\ncode\n```)
    code_blocks: list[str] = []

    def _save_cb(m: re.Match) -> str:
        lang = (m.group(1) or "").strip()
        code = m.group(2)
        escaped_code = html.escape(code.strip("\n"))
        idx = len(code_blocks)
        if lang:
            tag = f'<pre><code class="language-{lang}">{escaped_code}</code></pre>'
        else:
            tag = f"<pre>{escaped_code}</pre>"
        code_blocks.append(tag)
        return f"\x00CB{idx}\x00"

    res = re.sub(r"```([a-zA-Z0-9_\+\-]*)\n?(.*?)```", _save_cb, text, flags=re.DOTALL)

    # 2. Protect existing inline code (`...`)
    inline_codes: list[str] = []

    def _save_ic(m: re.Match) -> str:
        code = m.group(1)
        escaped = html.escape(code)
        idx = len(inline_codes)
        inline_codes.append(f"<code>{escaped}</code>")
        return f"\x00IC{idx}\x00"

    res = re.sub(r"`([^`\n]+)`", _save_ic, res)

    # 3. Protect allowed Telegram HTML tags already in the text
    tag_pattern = r"(</?(?:b|strong|i|em|u|ins|s|strike|del|a(?:\s+href=\"[^\"]*\")?|code|pre|blockquote(?:\s+expandable)?|tg-spoiler|tg-emoji)>)"
    html_tags: list[str] = []

    def _save_ht(m: re.Match) -> str:
        idx = len(html_tags)
        html_tags.append(m.group(0))
        return f"\x00HT{idx}\x00"

    res = re.sub(tag_pattern, _save_ht, res, flags=re.IGNORECASE)

    # 4. Handle blockquotes (> line1\n> line2)
    def _format_blockquote(m: re.Match) -> str:
        lines = m.group(0).splitlines()
        cleaned = []
        for l in lines:
            if l.startswith("> "):
                cleaned.append(l[2:])
            elif l.startswith(">"):
                cleaned.append(l[1:])
            else:
                cleaned.append(l)
        quote_text = "\n".join(cleaned)
        # If long enough, use expandable blockquote (Telegram Bot API 7.3+)
        if len(lines) >= 3 or len(quote_text) > 150:
            return f"<blockquote expandable>{quote_text}</blockquote>"
        return f"<blockquote>{quote_text}</blockquote>"

    res = re.sub(r"(?m)(?:^>[^\n]*(?:\n>[^\n]*)*)", _format_blockquote, res)
    res = re.sub(r"(</?blockquote(?:\s+expandable)?>)", _save_ht, res, flags=re.IGNORECASE)

    # 5. Escape remaining raw HTML entities (&, <, >)
    res = html.escape(res, quote=False)

    # 6. Apply markdown formatting to regular text
    # Spoilers (||text||)
    res = re.sub(r"\|\|([^\|\n]+)\|\|", r"<tg-spoiler>\1</tg-spoiler>", res)
    # Headers (# Title)
    res = re.sub(r"(?m)^#{1,6}\s+(.+)$", r"<b>\1</b>", res)
    # Bold + Italic (***text*** or ___text___)
    res = re.sub(r"\*\*\*([^\*\n]+)\*\*\*", r"<b><i>\1</i></b>", res)
    # Bold (**text** or __text__)
    res = re.sub(r"\*\*([^\*\n]+)\*\*", r"<b>\1</b>", res)
    res = re.sub(r"__([^_\n]+)__", r"<b>\1</b>", res)
    # Italic (*text* or _text_)
    res = re.sub(r"(?<!\w)\*([^\*\n]+)\*(?!\w)", r"<i>\1</i>", res)
    res = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"<i>\1</i>", res)
    # Strikethrough (~~text~~ only)
    res = re.sub(r"~~([^~\n]+)~~", r"<s>\1</s>", res)
    # Links [title](url)
    res = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", r'<a href="\2">\1</a>', res)

    # 7. Restore protected tokens
    for idx, tag in enumerate(html_tags):
        res = res.replace(f"\x00HT{idx}\x00", tag)
    for idx, tag in enumerate(inline_codes):
        res = res.replace(f"\x00IC{idx}\x00", tag)
    for idx, tag in enumerate(code_blocks):
        res = res.replace(f"\x00CB{idx}\x00", tag)

    return res


class TelegramClient:
    """Thin async wrapper around the Telegram bot HTTP API."""

    def __init__(self, bot_token: str, *, base_url: str = "https://api.telegram.org") -> None:
        self._base = f"{base_url}/bot{bot_token}"
        self._bot_token = bot_token
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "TelegramClient":
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(35.0))
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._client is not None
        await self._client.aclose()
        self._client = None

    async def get_me(self) -> dict[str, Any]:
        assert self._client is not None
        r = await self._client.get(f"{self._base}/getMe")
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram getMe failed: {data.get('description')}")
        return dict(data.get("result") or {})

    async def get_updates(self, offset: int, timeout: int = 30) -> list[dict[str, Any]]:
        assert self._client is not None
        r = await self._client.get(f"{self._base}/getUpdates", params={
            "offset": offset,
            "timeout": timeout,
            "allowed_updates": '["message","callback_query"]',
        })
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram getUpdates failed: {data.get('description')}")
        return list(data.get("result") or [])

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        keyboard: InlineKeyboard | None = None,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = "HTML",
    ) -> int | None:
        assert self._client is not None
        if not text:
            return None

        first_message_id: int | None = None

        # Split on paragraph boundaries when formatting for HTML to keep chunks valid
        if parse_mode == "HTML":
            paragraphs = text.split("\n\n")
            chunks: list[str] = []
            current: list[str] = []
            current_len = 0
            for p in paragraphs:
                p_len = len(p) + 2
                if current and current_len + p_len > 3500:
                    raw_chunk = "\n\n".join(current)
                    chunks.append(format_for_telegram(raw_chunk))
                    current = [p]
                    current_len = p_len
                else:
                    current.append(p)
                    current_len += p_len
            if current:
                raw_chunk = "\n\n".join(current)
                chunks.append(format_for_telegram(raw_chunk))
        else:
            chunks = chunk_message(text)

        for i, chunk in enumerate(chunks):
            payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if i == 0:
                if keyboard:
                    payload["reply_markup"] = {"inline_keyboard": keyboard}
                elif reply_markup is not None:
                    payload["reply_markup"] = reply_markup

            r = await self._client.post(f"{self._base}/sendMessage", json=payload)
            data = r.json()
            if not data.get("ok"):
                desc = str(data.get("description", ""))
                if "can't parse entities" in desc.lower() or "entity" in desc.lower() or "parse" in desc.lower():
                    # Strip tags on parse error
                    plain_payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:3500]}
                    if i == 0:
                        if keyboard:
                            plain_payload["reply_markup"] = {"inline_keyboard": keyboard}
                        elif reply_markup is not None:
                            plain_payload["reply_markup"] = reply_markup
                    r = await self._client.post(f"{self._base}/sendMessage", json=plain_payload)
                    data = r.json()
                if not data.get("ok"):
                    raise RuntimeError(f"sendMessage failed: {data.get('description')}")
            if i == 0:
                result = data.get("result") or {}
                mid = result.get("message_id")
                if isinstance(mid, int):
                    first_message_id = mid
        return first_message_id

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        keyboard: InlineKeyboard | None = None,
        parse_mode: str | None = "HTML",
    ) -> None:
        assert self._client is not None
        formatted_text = format_for_telegram(text) if parse_mode == "HTML" else text
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": formatted_text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if keyboard is not None:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        else:
            payload["reply_markup"] = {"inline_keyboard": []}
        r = await self._client.post(f"{self._base}/editMessageText", json=payload)
        data = r.json()
        if not data.get("ok"):
            desc = str(data.get("description", "")).lower()
            if "not modified" in desc:
                return
            if "can't parse entities" in desc or "entity" in desc or "parse" in desc:
                plain_payload: dict[str, Any] = {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text,
                    "reply_markup": payload.get("reply_markup", {}),
                }
                r = await self._client.post(f"{self._base}/editMessageText", json=plain_payload)
                data = r.json()
                if data.get("ok") or "not modified" in str(data.get("description", "")).lower():
                    return
            raise RuntimeError(f"editMessageText failed: {data.get('description')}")

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str = "",
    ) -> None:
        assert self._client is not None
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        r = await self._client.post(f"{self._base}/answerCallbackQuery", json=payload)
        data = r.json()
        if not data.get("ok"):
            return

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        assert self._client is not None
        r = await self._client.post(
            f"{self._base}/sendChatAction",
            json={"chat_id": chat_id, "action": action},
        )
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"sendChatAction failed: {data.get('description')}")

    async def get_file(self, file_id: str) -> bytes:
        assert self._client is not None
        r = await self._client.get(f"{self._base}/getFile", params={"file_id": file_id})
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"getFile failed: {data.get('description')}")
        file_path = data["result"]["file_path"]
        r2 = await self._client.get(
            f"https://api.telegram.org/file/bot{self._bot_token}/{file_path}")
        return r2.content

    async def set_webhook(self, url: str, secret_token: str = "") -> dict[str, Any]:
        assert self._client is not None
        params: dict[str, Any] = {"url": url}
        if secret_token:
            params["secret_token"] = secret_token
        r = await self._client.get(f"{self._base}/setWebhook", params=params)
        return r.json()

    async def delete_webhook(self) -> dict[str, Any]:
        assert self._client is not None
        r = await self._client.get(f"{self._base}/deleteWebhook")
        return r.json()

    async def send_document(
        self,
        chat_id: int,
        file_path: str,
        *,
        caption: str = "",
        filename: str = "",
    ) -> int | None:
        assert self._client is not None
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"Файл не найден: {file_path}")
        fname = filename or p.name
        data = p.read_bytes()
        files = {"document": (fname, data)}
        data_payload: dict[str, Any] = {"chat_id": chat_id}
        if caption:
            data_payload["caption"] = format_for_telegram(caption)
            data_payload["parse_mode"] = "HTML"
        r = await self._client.post(f"{self._base}/sendDocument", data=data_payload, files=files)
        res = r.json()
        if not res.get("ok"):
            raise RuntimeError(f"sendDocument failed: {res.get('description')}")
        mid = (res.get("result") or {}).get("message_id")
        return mid if isinstance(mid, int) else None
