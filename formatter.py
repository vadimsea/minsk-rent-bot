"""
Форматирование Telegram-постов.

Правила:
- разные шаблоны для квартиры и комнаты;
- если поля нет — строка не выводится;
- описание ограничено ~300 символами;
- не выдумываем факты, только то, что в объявлении;
- никаких телефонов / личных контактов (это уже сделано в normalizer).
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any, Dict, List, Optional


logger = logging.getLogger("formatter")

# Снимаем дублирующий префикс города из адреса перед выводом:
# "Минск Брилевская ул. 37" -> "Брилевская ул. 37"
_CITY_PREFIX_RE = re.compile(r"^\s*(?:г\.?\s*)?минск[,\s]+", re.IGNORECASE)


def _trim_city_prefix(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    return _CITY_PREFIX_RE.sub("", value).strip() or None


TELEGRAM_CAPTION_LIMIT = 1024
TELEGRAM_MESSAGE_LIMIT = 4096
DESCRIPTION_LIMIT = 300


def _h(value: Optional[str]) -> str:
    """HTML-escape для безопасной отправки в Telegram parse_mode=HTML."""
    if value is None:
        return ""
    return html.escape(str(value), quote=False)


def _row(emoji: str, label: str, value: Optional[Any]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "none":
        return None
    return f"{emoji} <b>{label}:</b> {_h(text)}"


def _trim(text: Optional[str], limit: int) -> Optional[str]:
    if not text:
        return None
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def format_listing(listing: Dict[str, Any]) -> str:
    """Возвращает готовый HTML-текст поста (для caption или sendMessage)."""
    listing_type = (listing.get("listing_type") or "apartment").lower()
    title = listing.get("title") or ("Комната в Минске" if listing_type == "room" else "Квартира в Минске")
    url = listing.get("url") or ""
    source = listing.get("source") or ""

    head_emoji = "🚪" if listing_type == "room" else "🏠"
    lines: List[str] = [f"{head_emoji} <b>{_h(title)}</b>", ""]

    info_rows: List[Optional[str]] = []
    info_rows.append(_row("💰", "Цена", listing.get("price")))
    district = listing.get("district")
    if district:
        info_rows.append(_row("📍", "Район", district))
    else:
        # Адрес часто начинается с «Минск ...» — это не информативно, режем.
        address = _trim_city_prefix(listing.get("address"))
        info_rows.append(_row("📍", "Адрес", address))
    info_rows.append(_row("🚇", "Метро", listing.get("metro")))

    if listing_type == "room":
        info_rows.append(_row("📐", "Площадь комнаты", listing.get("area")))
    else:
        info_rows.append(_row("📐", "Площадь", listing.get("area")))
        info_rows.append(_row("🛏", "Комнат", listing.get("rooms")))

    info_rows.append(_row("🏢", "Этаж", listing.get("floor")))

    for row in info_rows:
        if row:
            lines.append(row)

    description = _trim(listing.get("description"), DESCRIPTION_LIMIT)
    if description:
        lines.append("")
        lines.append("📝 <b>Кратко:</b>")
        lines.append(_h(description))

    lines.append("")
    lines.append(f"🔗 <a href=\"{_h(url)}\">Смотреть объявление</a>")
    if source:
        lines.append(f"📡 Источник: <i>{_h(source)}</i>")

    return "\n".join(lines)


def format_for_telegram(listing: Dict[str, Any], *, has_photo: bool) -> str:
    """
    Подгоняем длину текста под лимит Telegram:
    - 1024 символа для caption (sendPhoto)
    - 4096 для обычного сообщения (sendMessage)
    """
    text = format_listing(listing)
    limit = TELEGRAM_CAPTION_LIMIT if has_photo else TELEGRAM_MESSAGE_LIMIT
    if len(text) <= limit:
        return text

    # Урезаем описание агрессивнее и пересобираем
    short = dict(listing)
    short_desc = _trim(listing.get("description"), 200)
    short["description"] = short_desc
    text = format_listing(short)
    if len(text) <= limit:
        return text

    # Совсем без описания
    short["description"] = None
    text = format_listing(short)
    if len(text) <= limit:
        return text

    # Жёсткая обрезка по длине (на крайний случай)
    return text[: limit - 1].rstrip() + "…"


def explain_pass_reason(listing: Dict[str, Any]) -> str:
    """Используется в DRY_RUN: коротко объясняем, почему пропустили объявление."""
    bits = []
    bits.append(f"тип={listing.get('listing_type')}")
    bits.append(f"срок={listing.get('rent_period')}")
    bits.append(f"город={listing.get('city')}")
    bits.append(f"цена={listing.get('price')}")
    bits.append(f"фото={'есть' if listing.get('image_url') else 'нет'}")
    return ", ".join(bits)
