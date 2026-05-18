"""
Фильтры объявлений.

Правила: если что-то непонятно — лучше отклонить объявление, чем
случайно опубликовать посуточную аренду, продажу или коммерческую
недвижимость.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Tuple

from config import CONFIG
from storage import Storage


logger = logging.getLogger("filters")


# Слова, по которым отсекаем «не наш» тип жилья / тип сделки
SALE_KEYWORDS = [
    "продажа", "продаётся", "продается", "продам", "купить", "for sale",
]
COMMERCIAL_KEYWORDS = [
    "офис", "склад", "помещение", "торгов", "коммерческ", "ресторан",
    "магазин", "цех", "ангар", "производств",
]
HOUSE_KEYWORDS = [
    "коттедж", "дом ", " дома", "усадьба", "таунхаус", "townhouse", "дача",
]
GARAGE_KEYWORDS = ["гараж", "паркинг", "машиноместо"]
HOTEL_KEYWORDS = ["гостиниц", "отель", "хостел", "hostel", "hotel"]

SHORT_TERM_HARD = [
    "посуточно", "посуточная", "на сутки", "на сут", "на ночь",
    "почасово", "на часы", "краткосрочн",
    "short term", "short-term", "shortterm", "daily", "hourly",
    "day rent", "per day", "per night",
]

SUSPICIOUS_KEYWORDS = [
    "срочно предоплата",
    "предоплата на карту",
    "переведите задаток",
    "переведите предоплату",
    "без просмотра",
    "бронь по предоплате",
    "пишите только в мессенджер",
    "пишите только в whatsapp",
    "пишите только в вайбер",
    "пишите только в viber",
    "только переводом",
]


# --------- атомарные предикаты ---------

def has_required_fields(listing: Dict[str, Any]) -> bool:
    return bool(
        listing.get("url")
        and listing.get("source")
        and listing.get("title")
        and listing.get("listing_type")
        and listing.get("rent_period")
    )


def _haystack(listing: Dict[str, Any]) -> str:
    return " ".join(
        str(listing.get(k) or "")
        for k in ("title", "description", "url", "address")
    ).lower()


def is_rent_listing(listing: Dict[str, Any]) -> bool:
    text = _haystack(listing)
    if any(kw in text for kw in SALE_KEYWORDS):
        return False
    return True


def is_long_term_rent(listing: Dict[str, Any]) -> bool:
    period = (listing.get("rent_period") or "").lower()
    if period == "short_term":
        return False

    text = _haystack(listing)
    if any(kw in text for kw in SHORT_TERM_HARD):
        return False

    # «неделя» — только если явно как срок аренды
    if re.search(r"\bна\s+(?:одну\s+)?неделю\b", text):
        return False
    if re.search(r"\bна\s+\d+\s+(?:дн[ея]|сут|ноч)", text):
        return False

    return period == "long_term"


def is_allowed_listing_type(listing: Dict[str, Any]) -> bool:
    allowed = set(CONFIG.allowed_listing_types or ["apartment", "room"])
    ltype = (listing.get("listing_type") or "").lower()
    return ltype in allowed


def is_apartment_or_room(listing: Dict[str, Any]) -> bool:
    ltype = (listing.get("listing_type") or "").lower()
    if ltype not in {"apartment", "room"}:
        return False
    text = _haystack(listing)
    if any(kw in text for kw in COMMERCIAL_KEYWORDS):
        return False
    if any(kw in text for kw in HOUSE_KEYWORDS):
        return False
    if any(kw in text for kw in GARAGE_KEYWORDS):
        return False
    if any(kw in text for kw in HOTEL_KEYWORDS):
        return False
    return True


def is_minsk_listing(listing: Dict[str, Any]) -> bool:
    target = (CONFIG.city or "Минск").lower()
    city = (listing.get("city") or "").lower()
    if target and target in city:
        return True
    # запас: ищем «минск» в title/address/url
    text = _haystack(listing)
    return "минск" in text or "minsk" in text


def has_price(listing: Dict[str, Any]) -> bool:
    return listing.get("price_value") is not None and listing["price_value"] > 0


def has_photo(listing: Dict[str, Any]) -> bool:
    image = listing.get("image_url")
    return isinstance(image, str) and image.startswith("http")


def is_not_duplicate(listing: Dict[str, Any], storage: Storage | None = None) -> bool:
    if storage is None:
        return True
    return not storage.is_published(listing.get("url") or "")


def _price_in_usd(listing: Dict[str, Any]) -> int | None:
    """
    Очень упрощённая конвертация: для MVP считаем BYN ≈ /3.2, EUR ≈ *1.08.
    Если курс важен — лучше брать через API; для фильтра «слишком дёшево»
    этого достаточно.
    """
    price = listing.get("price_value")
    if price is None:
        return None
    currency = (listing.get("currency") or "").upper()
    if currency == "USD":
        return int(price)
    if currency == "EUR":
        return int(price * 1.08)
    if currency == "BYN":
        return int(price / 3.2)
    return int(price)


def is_not_suspicious(listing: Dict[str, Any]) -> bool:
    text = _haystack(listing)
    if any(kw in text for kw in SUSPICIOUS_KEYWORDS):
        return False

    usd = _price_in_usd(listing)
    if usd is None:
        return True

    ltype = (listing.get("listing_type") or "").lower()
    if ltype == "room" and usd < CONFIG.min_room_price_usd:
        return False
    if ltype == "apartment" and usd < CONFIG.min_apartment_price_usd:
        return False
    if usd > CONFIG.max_price_usd:
        return False
    return True


# --------- пайплайн ---------

FilterFn = Callable[[Dict[str, Any]], bool]


def _build_filters(storage: Storage | None) -> List[Tuple[str, FilterFn]]:
    return [
        ("missing_fields", has_required_fields),
        ("not_rent", is_rent_listing),
        ("not_long_term", is_long_term_rent),
        ("not_allowed_type", is_allowed_listing_type),
        ("not_apartment_or_room", is_apartment_or_room),
        ("not_minsk", is_minsk_listing),
        ("no_price", has_price),
        ("no_photo", (lambda l: has_photo(l)) if CONFIG.require_photo else (lambda l: True)),
        ("duplicate", lambda l: is_not_duplicate(l, storage)),
        ("suspicious", is_not_suspicious),
    ]


def filter_listings(
    listings: List[Dict[str, Any]],
    storage: Storage | None = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    Возвращает (прошедшие_фильтры, статистика_отсечений).
    Каждое объявление падает в первую же категорию, по которой оно отсеяно.
    """
    stats: Dict[str, int] = {name: 0 for name, _ in _build_filters(storage)}
    stats["total"] = len(listings)
    stats["passed"] = 0

    filters = _build_filters(storage)
    passed: List[Dict[str, Any]] = []

    for listing in listings:
        rejected = False
        for name, fn in filters:
            try:
                ok = fn(listing)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Фильтр %s упал на объявлении %s: %s", name, listing.get("url"), exc)
                ok = False
            if not ok:
                stats[name] += 1
                rejected = True
                break
        if not rejected:
            passed.append(listing)
            stats["passed"] += 1

    return passed, stats
