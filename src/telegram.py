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
    message_id: int = 0
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
    mid = msg.get("message_id") or 0
    if not isinstance(cid, int) or not isinstance(uid, int) or not isinstance(uid_n, int):
        return None
    forward_origin = _get_forward_origin(msg)
    return InboundMessage(
        update_id=uid_n,
        chat_id=cid,
        user_id=uid,
        text=text,
        message_id=mid,
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


def balance_telegram_html(s: str) -> str:
    """Ensure all opened Telegram HTML tags are properly matched and closed."""
    tag_re = re.compile(r"</?([a-z0-9\-]+)(?:\s+[^>]*)?>", re.IGNORECASE)
    valid_tags = {
        "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
        "a", "code", "pre", "blockquote", "tg-spoiler", "tg-emoji"
    }
    stack: list[str] = []
    for m in tag_re.finditer(s):
        full_tag = m.group(0)
        tag_name = m.group(1).lower()
        if tag_name not in valid_tags:
            continue
        if not full_tag.startswith("</"):
            stack.append(tag_name)
        else:
            if stack and stack[-1] == tag_name:
                stack.pop()
    for tag_name in reversed(stack):
        s += f"</{tag_name}>"
    return s


def format_for_telegram(text: str) -> str:
    """Format markdown/HTML text into safe Telegram-compatible HTML."""
    if not text:
        return ""

    # 1. Protect existing HTML <pre> blocks and Markdown code blocks (```lang\ncode\n```)
    code_blocks: list[str] = []

    def _save_existing_pre(m: re.Match) -> str:
        idx = len(code_blocks)
        code_blocks.append(m.group(0))
        return f"\x00CB{idx}\x00"

    res = re.sub(r"(?is)<pre(?:\s+[^>]*)?>.*?</pre>", _save_existing_pre, text)

    def _save_cb(m: re.Match) -> str:
        lang = (m.group(1) or "").strip()
        code = m.group(2)
        escaped_code = html.escape(code.strip("\n"), quote=False)
        idx = len(code_blocks)
        if lang:
            tag = f'<pre><code class="language-{lang}">{escaped_code}</code></pre>'
        else:
            tag = f"<pre>{escaped_code}</pre>"
        code_blocks.append(tag)
        return f"\x00CB{idx}\x00"

    res = re.sub(r"```([a-zA-Z0-9_\+\-]*)\n?(.*?)```", _save_cb, res, flags=re.DOTALL)

    # 2. Protect existing HTML <code> blocks and Markdown inline code (`...`)
    inline_codes: list[str] = []

    def _save_existing_code(m: re.Match) -> str:
        idx = len(inline_codes)
        inline_codes.append(m.group(0))
        return f"\x00IC{idx}\x00"

    res = re.sub(r"(?is)<code(?:\s+[^>]*)?>.*?</code>", _save_existing_code, res)

    def _save_ic(m: re.Match) -> str:
        code = m.group(1)
        escaped = html.escape(code, quote=False)
        idx = len(inline_codes)
        inline_codes.append(f"<code>{escaped}</code>")
        return f"\x00IC{idx}\x00"

    res = re.sub(r"`([^`\n]+)`", _save_ic, res)

    # 3. Protect allowed Telegram HTML tags already in the text
    tag_pattern = r"(</?(?:b|strong|i|em|u|ins|s|strike|del|a(?:\s+href=\"[^\"]*\")?|code(?:\s+class=\"[^\"]*\")?|pre|blockquote(?:\s+expandable)?|tg-spoiler|tg-emoji(?:\s+emoji-id=\"[^\"]*\")?)>)"
    html_tags: list[str] = []

    def _save_ht(m: re.Match) -> str:
        idx = len(html_tags)
        html_tags.append(m.group(0))
        return f"\x00HT{idx}\x00"

    res = re.sub(tag_pattern, _save_ht, res, flags=re.IGNORECASE)

    # 4. Handle blockquotes & GitHub Alerts (> [!NOTE])
    def _format_blockquote(m: re.Match) -> str:
        lines = m.group(0).splitlines()
        cleaned: list[str] = []
        for l in lines:
            l = l.strip()
            if l.startswith("> "):
                cleaned.append(l[2:])
            elif l.startswith(">"):
                cleaned.append(l[1:])
            else:
                cleaned.append(l)

        if cleaned:
            first = cleaned[0]
            if first.startswith("[!NOTE]"):
                cleaned[0] = "💡 <b>Примечание:</b> " + first[7:].strip()
            elif first.startswith("[!TIP]"):
                cleaned[0] = "💡 <b>Совет:</b> " + first[6:].strip()
            elif first.startswith("[!IMPORTANT]"):
                cleaned[0] = "📌 <b>Важно:</b> " + first[12:].strip()
            elif first.startswith("[!WARNING]"):
                cleaned[0] = "⚠️ <b>Внимание:</b> " + first[10:].strip()
            elif first.startswith("[!CAUTION]"):
                cleaned[0] = "🛑 <b>Осторожно:</b> " + first[10:].strip()

        quote_text = "\n".join(cleaned)
        if len(lines) >= 4 or len(quote_text) > 160:
            tag = f"<blockquote expandable>{quote_text}</blockquote>"
        else:
            tag = f"<blockquote>{quote_text}</blockquote>"
        idx = len(html_tags)
        html_tags.append(tag)
        return f"\x00HT{idx}\x00"

    res = re.sub(r"(?m)(?:^[ \t]*>[^\n]*(?:\n[ \t]*>[^\n]*)*)", _format_blockquote, res)

    # 5. Replace dividers
    res = re.sub(r"(?m)^[ \t]*(?:---|\*\*\*|___|- - -|\* \* \*)[ \t]*$", "— — —", res)

    # 6. Escape remaining raw HTML entities (&, <, >)
    res = html.escape(res, quote=False)

    # 7. Apply markdown formatting to regular text
    res = re.sub(r"(?m)^#{1,6}[ \t]+(.+)$", r"<b>\1</b>", res)
    res = re.sub(r"\*\*\*([^\*\n]+?)\*\*\*", r"<b><i>\1</i></b>", res)
    res = re.sub(r"\*\*([^\*\n]+?)\*\*", r"<b>\1</b>", res)
    res = re.sub(r"__([^_\n]+?)__", r"<b>\1</b>", res)
    res = re.sub(r"(^|[^\*\w])\*([^\*\s\n][^\*\n]*?[^\*\s\n]|[^\*\s\n])\*([^\*\w]|$)", r"\1<i>\2</i>\3", res)
    res = re.sub(r"(^|[^\w])_([^_\s\n][^_\n]*?[^_\s\n]|[^_\s\n])_([^\w]|$)", r"\1<i>\2</i>\3", res)
    res = re.sub(r"~~([^~\n]+?)~~", r"<s>\1</s>", res)
    res = re.sub(r"\|\|([^\|\n]+?)\|\|", r"<tg-spoiler>\1</tg-spoiler>", res)
    res = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", r'<a href="\2">\1</a>', res)

    # 8. Restore protected tokens
    for idx, tag in enumerate(html_tags):
        res = res.replace(f"\x00HT{idx}\x00", tag)
    for idx, tag in enumerate(inline_codes):
        res = res.replace(f"\x00IC{idx}\x00", tag)
    for idx, tag in enumerate(code_blocks):
        res = res.replace(f"\x00CB{idx}\x00", tag)

    # 9. Ensure HTML tag balance
    res = balance_telegram_html(res)

    return res


def html_to_markdown_for_rich(text: str) -> str:
    """Convert Telegram HTML tags to markdown equivalents for Rich Message blocks."""
    text = re.sub(r"</?(?:b|strong)>", "**", text)
    text = re.sub(r"</?(?:i|em)>", "_", text)
    text = re.sub(r"</?code>", "`", text)
    text = re.sub(r"</?(?:s|strike|del)>", "~~", text)
    text = re.sub(r"<a\s+href=\"([^\"]*)\">([^<]*)</a>", r"[\2](\1)", text)
    text = re.sub(r"<blockquote(?:\s+expandable)?>", "> ", text)
    text = re.sub(r"</blockquote>", "\n", text)
    return text


def markdown_to_rich_blocks(text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    text = (text or "").strip()
    if not text:
        return blocks

    text = html_to_markdown_for_rich(text)
    lines = text.split("\n")
    current_paragraph: list[str] = []
    current_quote: list[str] = []
    current_list_items: list[dict[str, Any]] = []
    in_code = False
    code_lang = ""
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal current_paragraph
        if current_paragraph:
            p_text = "\n".join(current_paragraph).strip()
            if p_text:
                blocks.append({"type": "paragraph", "text": p_text})
            current_paragraph = []

    def flush_quote() -> None:
        nonlocal current_quote
        if current_quote:
            q_text = "\n".join(current_quote).strip()
            if q_text:
                blocks.append({
                    "type": "blockquote",
                    "blocks": [{"type": "paragraph", "text": q_text}]
                })
            current_quote = []

    def flush_list() -> None:
        nonlocal current_list_items
        if current_list_items:
            blocks.append({
                "type": "list",
                "items": current_list_items
            })
            current_list_items = []

    def flush_all() -> None:
        flush_paragraph()
        flush_quote()
        flush_list()

    for line in lines:
        trimmed = line.strip()

        # 1. Inside Code Block
        if in_code:
            if trimmed.startswith("```"):
                in_code = False
                blocks.append({
                    "type": "pre",
                    "text": "\n".join(code_lines),
                    "language": code_lang or "text",
                })
                code_lines = []
                code_lang = ""
            else:
                code_lines.append(line)
            continue

        # 2. Start Code Block
        if trimmed.startswith("```"):
            flush_all()
            in_code = True
            code_lang = trimmed[3:].strip()
            code_lines = []
            continue

        # 3. Dividers (---, ***, ___)
        if trimmed in ("---", "***", "___", "- - -", "* * *"):
            flush_all()
            blocks.append({"type": "paragraph", "text": "— — —"})
            continue

        # 4. Headings (# ... ######)
        if trimmed.startswith("#"):
            h_level = 0
            while h_level < len(trimmed) and trimmed[h_level] == "#":
                h_level += 1
            if 0 < h_level <= 6 and h_level < len(trimmed) and trimmed[h_level] == " ":
                flush_all()
                blocks.append({
                    "type": "heading",
                    "text": trimmed[h_level:].strip(),
                    "size": h_level,
                })
                continue

        # 5. Blockquotes (> ...)
        if trimmed.startswith(">"):
            flush_paragraph()
            flush_list()
            q_line = trimmed[1:]
            if q_line.startswith(" "):
                q_line = q_line[1:]
            current_quote.append(q_line)
            continue
        elif current_quote:
            flush_quote()

        # 6. List items (*, -, +, 1., etc.)
        is_bullet = trimmed.startswith(("* ", "- ", "+ ", "• "))
        is_numbered = False
        if not is_bullet:
            dot_idx = trimmed.find(". ")
            if 0 < dot_idx <= 4 and trimmed[:dot_idx].isdigit():
                is_numbered = True

        if is_bullet or is_numbered:
            flush_paragraph()
            if is_bullet:
                item_text = trimmed[2:].strip()
            else:
                dot_idx = trimmed.find(". ")
                item_text = trimmed[dot_idx + 2:].strip()
            current_list_items.append({
                "blocks": [{"type": "paragraph", "text": item_text}]
            })
            continue
        elif current_list_items and not trimmed:
            flush_list()
            continue

        # 7. Blank lines
        if not trimmed:
            flush_paragraph()
            continue

        # 8. Regular text
        current_paragraph.append(line)

    if in_code:
        blocks.append({
            "type": "pre",
            "text": "\n".join(code_lines),
            "language": code_lang or "text",
        })
    flush_all()

    if not blocks:
        blocks.append({"type": "paragraph", "text": text})
    return blocks


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

    async def send_rich_message(
        self,
        chat_id: int,
        text: str,
        *,
        keyboard: InlineKeyboard | None = None,
        reply_markup: dict[str, Any] | None = None,
        reply_to_message_id: int | None = None,
    ) -> int | None:
        assert self._client is not None
        blocks = markdown_to_rich_blocks(text)

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "rich_message": {"blocks": blocks},
        }
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        elif reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id

        try:
            r = await self._client.post(f"{self._base}/sendRichMessage", json=payload)
            data = r.json()
            if data.get("ok"):
                res = data.get("result") or {}
                mid = res.get("message_id")
                if isinstance(mid, int):
                    return mid
        except Exception:
            pass
        return None

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

        # 1. For long articles (> 3500 chars up to 32k), deliver as a single native Rich Message
        if len(text) > 3500 and len(text) <= 32000:
            rich_mid = await self.send_rich_message(
                chat_id, text, keyboard=keyboard, reply_markup=reply_markup
            )
            if rich_mid is not None:
                return rich_mid

        first_message_id: int | None = None

        # Standard clean HTML message handling
        if parse_mode == "HTML":
            formatted = format_for_telegram(text)
            if len(formatted) <= 3900:
                chunks = [formatted]
            else:
                paragraphs = text.split("\n\n")
                chunks = []
                current: list[str] = []
                current_len = 0
                for p in paragraphs:
                    p_len = len(p) + 2
                    if current and current_len + p_len > 3200:
                        raw_chunk = "\n\n".join(current)
                        if raw_chunk.count("```") % 2 != 0:
                            raw_chunk += "\n```"
                            current = ["```\n" + p]
                        else:
                            current = [p]
                        chunks.append(format_for_telegram(raw_chunk))
                        current_len = p_len
                    else:
                        current.append(p)
                        current_len += p_len
                if current:
                    raw_chunk = "\n\n".join(current)
                    chunks.append(format_for_telegram(raw_chunk))
        else:
            chunks = chunk_message(text)

        import asyncio

        for i, chunk in enumerate(chunks):
            payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if i == len(chunks) - 1:
                if keyboard:
                    payload["reply_markup"] = {"inline_keyboard": keyboard}
                elif reply_markup is not None:
                    payload["reply_markup"] = reply_markup

            r = await self._client.post(f"{self._base}/sendMessage", json=payload)
            data = r.json()
            if not data.get("ok"):
                desc = str(data.get("description", ""))
                if "can't parse entities" in desc.lower() or "entity" in desc.lower() or "parse" in desc.lower():
                    # Fallback on parse error: send as plain text
                    plain_payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
                    if i == len(chunks) - 1:
                        if keyboard:
                            plain_payload["reply_markup"] = {"inline_keyboard": keyboard}
                        elif reply_markup is not None:
                            plain_payload["reply_markup"] = reply_markup
                    r = await self._client.post(f"{self._base}/sendMessage", json=plain_payload)
                    data = r.json()
                if not data.get("ok"):
                    # Retry as rich message if it was long or if sendMessage failed
                    rich_mid = await self.send_rich_message(
                        chat_id, text, keyboard=keyboard, reply_markup=reply_markup
                    )
                    if rich_mid is not None:
                        return rich_mid
                    raise RuntimeError(f"sendMessage failed: {data.get('description')}")
            if i == 0:
                result = data.get("result") or {}
                mid = result.get("message_id")
                if isinstance(mid, int):
                    first_message_id = mid

            if len(chunks) > 1 and i < len(chunks) - 1:
                await asyncio.sleep(0.35)

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
