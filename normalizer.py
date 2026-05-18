"""
Нормализация объявлений: общий вид для всех источников.

Что делаем:
- чистим пробелы и HTML;
- определяем валюту;
- приводим цену к числу;
- нормализуем URL;
- укорачиваем описание;
- определяем listing_type (apartment/room) и rent_period (long_term/short_term);
- удаляем потенциальные ПДн из описания (телефоны).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse

from bs4 import BeautifulSoup


logger = logging.getLogger("normalizer")


PHONE_RE = re.compile(
    r"(?:\+?\d[\d\-\s().]{7,}\d)|(?:8[\s\-]?0?\d{2}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2})"
)
TELEGRAM_HANDLE_RE = re.compile(r"@[\w_]{4,}")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
MULTI_SPACE_RE = re.compile(r"\s+")


SHORT_TERM_KEYWORDS = [
    "посуточно",
    "посуточная",
    "на сутки",
    "на сут",
    "на ночь",
    "почасово",
    "на часы",
    "час",  # будет проверяться в составе словосочетаний
    "краткосрочн",
    "short term",
    "short-term",
    "shortterm",
    "daily",
    "hourly",
    "day rent",
    "per day",
    "per night",
    "ночь",
]

LONG_TERM_KEYWORDS = [
    "долгосрочн",
    "на длительный",
    "длительно",
    "long term",
    "long-term",
    "longterm",
    "monthly",
    "помесячно",
    "на длит",
]

ROOM_KEYWORDS = ["комнат", "комната", "room", "комнаты в", "часть квартиры"]
APARTMENT_KEYWORDS = ["квартир", "apartment", "flat", "студи"]


def clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    # уберём html
    if "<" in value and ">" in value:
        value = BeautifulSoup(value, "lxml").get_text(" ")
    value = MULTI_SPACE_RE.sub(" ", value).strip()
    return value or None


def strip_personal_data(text: Optional[str]) -> Optional[str]:
    """Из описания убираем телефоны, e-mail и @username."""
    if not text:
        return text
    text = PHONE_RE.sub("[номер скрыт]", text)
    text = EMAIL_RE.sub("[email скрыт]", text)
    text = TELEGRAM_HANDLE_RE.sub("[контакт скрыт]", text)
    return text


def shorten(text: Optional[str], limit: int = 300) -> Optional[str]:
    if not text:
        return None
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0]
    return cut.rstrip(" .,;:!?-") + "…"


def normalize_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    try:
        parsed = urlparse(url)
        if not parsed.scheme:
            return None
        # убираем utm и подобные tracking-параметры
        query = parsed.query
        if query:
            keep = []
            for pair in query.split("&"):
                key = pair.split("=", 1)[0].lower()
                if key.startswith("utm_") or key in {"fbclid", "gclid", "yclid"}:
                    continue
                keep.append(pair)
            query = "&".join(keep)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, ""))
    except Exception:  # noqa: BLE001
        return url


def normalize_price(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"[^\d.,]", "", value).replace(",", ".")
    if not cleaned:
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def detect_currency(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    low = text.lower()
    if "$" in text or "usd" in low or "у.е" in low:
        return "USD"
    if "€" in text or "eur" in low:
        return "EUR"
    if "byn" in low or "руб" in low or "р." in low or "бел" in low:
        return "BYN"
    return None


def detect_listing_type(listing: Dict[str, Any]) -> str:
    """Возвращает 'apartment' или 'room'. По умолчанию apartment."""
    explicit = (listing.get("listing_type") or "").lower()
    if explicit in {"apartment", "room"}:
        return explicit

    haystack = " ".join(
        str(listing.get(k) or "")
        for k in ("title", "description", "url")
    ).lower()

    if any(kw in haystack for kw in ROOM_KEYWORDS):
        # "1-комнатная квартира" не считаем комнатой
        if re.search(r"\d\s*-?\s*комн", haystack) and "квартир" in haystack:
            return "apartment"
        return "room"
    if any(kw in haystack for kw in APARTMENT_KEYWORDS):
        return "apartment"
    return "apartment"


def detect_rent_period(listing: Dict[str, Any]) -> str:
    """
    Возвращает 'long_term' или 'short_term'.
    Если найдены явные признаки посуточной аренды — short_term.
    Если найдены явные признаки долгосрочной — long_term.
    Иначе — 'long_term' только если URL/категория источника подразумевает длительную аренду.
    """
    explicit = (listing.get("rent_period") or "").lower()
    haystack = " ".join(
        str(listing.get(k) or "")
        for k in ("title", "description", "url")
    ).lower()

    has_short = any(kw in haystack for kw in SHORT_TERM_KEYWORDS)
    has_long = any(kw in haystack for kw in LONG_TERM_KEYWORDS)

    if has_short and not has_long:
        return "short_term"
    if has_long:
        return "long_term"
    return explicit if explicit in {"long_term", "short_term"} else "long_term"


def normalize_listing(listing: Dict[str, Any]) -> Dict[str, Any]:
    """Приводит объявление к единому формату."""
    out = dict(listing)

    out["url"] = normalize_url(out.get("url"))
    out["title"] = clean_text(out.get("title"))
    raw_description = clean_text(out.get("description"))
    raw_description = strip_personal_data(raw_description)
    out["description"] = shorten(raw_description, limit=350)

    out["district"] = clean_text(out.get("district"))
    out["address"] = clean_text(out.get("address"))
    out["metro"] = clean_text(out.get("metro"))
    out["city"] = clean_text(out.get("city")) or "Минск"
    out["floor"] = clean_text(out.get("floor"))
    out["area"] = clean_text(out.get("area"))
    out["rooms"] = clean_text(out.get("rooms"))

    # цена / валюта
    price_value = out.get("price_value")
    if price_value is None:
        price_value = normalize_price(out.get("price"))
    out["price_value"] = price_value

    currency = out.get("currency") or detect_currency(out.get("price"))
    out["currency"] = currency.upper() if isinstance(currency, str) else None

    if price_value is not None and out["currency"]:
        if out["currency"] == "USD":
            out["price"] = f"{price_value} $"
        elif out["currency"] == "EUR":
            out["price"] = f"{price_value} €"
        elif out["currency"] == "BYN":
            out["price"] = f"{price_value} BYN"
        else:
            out["price"] = f"{price_value} {out['currency']}"
    elif price_value is not None:
        out["price"] = str(price_value)
    else:
        out["price"] = clean_text(out.get("price"))

    out["listing_type"] = detect_listing_type(out)
    out["rent_period"] = detect_rent_period(out)

    # У комнаты число комнат не нужно
    if out["listing_type"] == "room":
        out["rooms"] = None

    return out
