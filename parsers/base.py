"""
Базовый класс парсера.

Любой парсер обязан реализовать метод parse_list(), который возвращает
список словарей-объявлений в едином формате.

Единый формат:
{
  "source":         "Realt.by",
  "listing_type":   "apartment" | "room",
  "rent_period":    "long_term" | "short_term",
  "title":          str,
  "price":          str,
  "price_value":    int | None,
  "currency":       "USD" | "BYN" | "EUR" | None,
  "city":           str | None,
  "district":       str | None,
  "address":        str | None,
  "metro":          str | None,
  "rooms":          str | None,
  "area":           str | None,
  "floor":          str | None,
  "description":    str | None,
  "url":            str (обязательно),
  "image_url":      str | None,
  "published_at":   str | None,
  "external_id":    str | None,
}
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup


logger = logging.getLogger("parsers")


# Пытаемся использовать lxml (быстрый), а если он не установлен —
# работаем со встроенным html.parser. Это позволяет запускать парсеры
# и локально без обязательной установки lxml.
try:
    BeautifulSoup("", "lxml")
    _PARSER = "lxml"
except Exception:  # noqa: BLE001
    _PARSER = "html.parser"


def make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, _PARSER)


class BaseParser(ABC):
    name: str = "base"
    source: str = "Base"

    def __init__(
        self,
        *,
        list_url: str,
        category: str = "apartment",
        rent_period: str = "long_term",
        city: str = "Минск",
        base_url: str = "",
    ) -> None:
        self.list_url = list_url
        self.category = category
        self.rent_period = rent_period
        self.city = city
        self.base_url = base_url

    @abstractmethod
    def parse_list(self) -> List[Dict[str, Any]]:
        """Получить и распарсить страницу со списком объявлений."""
        raise NotImplementedError

    def empty_listing(self) -> Dict[str, Any]:
        """Шаблон объявления со всеми полями."""
        return {
            "source": self.source,
            "listing_type": self.category,
            "rent_period": self.rent_period,
            "title": None,
            "price": None,
            "price_value": None,
            "currency": None,
            "city": self.city,
            "district": None,
            "address": None,
            "metro": None,
            "rooms": None,
            "area": None,
            "floor": None,
            "description": None,
            "url": None,
            "image_url": None,
            "published_at": None,
            "external_id": None,
        }

    def safe_parse(self) -> List[Dict[str, Any]]:
        """Ловим любые исключения, чтобы один проблемный сайт не валил весь пайплайн."""
        try:
            items = self.parse_list() or []
        except Exception as exc:  # noqa: BLE001
            logger.exception("Парсер %s упал: %s", self.source, exc)
            return []

        valid: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if not item.get("url") or not item.get("title"):
                continue
            item.setdefault("source", self.source)
            item.setdefault("listing_type", self.category)
            item.setdefault("rent_period", self.rent_period)
            item.setdefault("city", self.city)
            valid.append(item)

        logger.info("[%s] получено %s объявлений", self.source, len(valid))
        return valid


def absolute_url(base_url: str, href: Optional[str]) -> Optional[str]:
    """Превращает относительный URL в абсолютный."""
    if not href:
        return None
    href = href.strip()
    if not href:
        return None
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return base_url.rstrip("/") + href
    return base_url.rstrip("/") + "/" + href
