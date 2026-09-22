"""Interactive project file browser (/ls) for Antigravity Telegram Bridge.

Inspired by opencode-telegram-bot's file-browser-menu.ts.
Allows navigating project directories, downloading files with a single tap,
and paging through file listings.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from src.i18n import t

PAGE_SIZE = 10


def encode_path(p: str) -> str:
    """Safely URL/B64 encode a file path for callback_data (cap 64 bytes)."""
    return base64.urlsafe_b64encode(p.encode("utf-8")).decode("ascii").rstrip("=")


def decode_path(token: str) -> str:
    """Decode path token from callback_data."""
    padding = "=" * ((4 - len(token) % 4) % 4)
    return base64.urlsafe_b64decode(token + padding).decode("utf-8")


def render_file_browser(
    current_dir: str,
    base_dir: str = "",
    page: int = 1,
    lang: str = "ru",
) -> tuple[str, list[list[dict[str, Any]]]]:
    """Render folder contents as interactive inline keyboard."""
    path_obj = Path(current_dir).resolve()
    if not path_obj.exists() or not path_obj.is_dir():
        path_obj = Path(base_dir or os.path.expanduser("~")).resolve()

    items: list[Path] = []
    try:
        items = sorted(
            list(path_obj.iterdir()),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
    except Exception as e:
        return f"⚠️ Ошибка чтения директории: {e}", [[{"text": "❌ Закрыть", "callback_data": "fb:close"}]]

    # Filter out hidden or internal bridge folders (.git, __pycache__, .bridge-inbox)
    filtered: list[Path] = []
    for item in items:
        if item.name.startswith(".") and item.name not in (".env.example",):
            continue
        if item.name in ("__pycache__", "node_modules", "venv", ".venv"):
            continue
        filtered.append(item)

    total_items = len(filtered)
    total_pages = max(1, (total_items + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    page_items = filtered[start_idx:end_idx]

    disp_path = str(path_obj)
    home = os.path.expanduser("~")
    if disp_path.startswith(home):
        disp_path = "~" + disp_path[len(home):]

    header = t("browser.title", lang, path=disp_path)
    if not filtered:
        header += f"\n\n{t('browser.empty', lang)}"

    keyboard: list[list[dict[str, Any]]] = []

    # Navigation bar: Up button
    parent_path = path_obj.parent
    if parent_path != path_obj and str(path_obj) != "/":
        encoded_parent = encode_path(str(parent_path))
        keyboard.append([{"text": f"⬆️ {t('browser.up', lang)}", "callback_data": f"fb:d:{encoded_parent}:1"}])

    # File / Directory Buttons
    for item in page_items:
        encoded_item = encode_path(str(item))
        if item.is_dir():
            btn_text = f"📁 {item.name}/"
            cb_data = f"fb:d:{encoded_item}:1"
        else:
            size_kb = max(1, item.stat().st_size // 1024)
            size_str = f"{size_kb} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
            btn_text = f"📄 {item.name} ({size_str})"
            cb_data = f"fb:f:{encoded_item}"
        keyboard.append([{"text": btn_text, "callback_data": cb_data}])

    # Pagination row
    if total_pages > 1:
        pag_row: list[dict[str, Any]] = []
        encoded_curr = encode_path(str(path_obj))
        if page > 1:
            pag_row.append({"text": "◀️", "callback_data": f"fb:d:{encoded_curr}:{page - 1}"})
        pag_row.append({"text": f"{page} / {total_pages}", "callback_data": f"fb:noop"})
        if page < total_pages:
            pag_row.append({"text": "▶️", "callback_data": f"fb:d:{encoded_curr}:{page + 1}"})
        keyboard.append(pag_row)

    # Footer row
    encoded_curr = encode_path(str(path_obj))
    keyboard.append([
        {"text": "🔄", "callback_data": f"fb:d:{encoded_curr}:{page}"},
        {"text": "❌ Закрыть", "callback_data": "fb:close"},
    ])

    return header, keyboard
