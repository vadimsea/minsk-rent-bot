"""
Парсер Domovita.by — обычный server-side HTML.

Domovita периодически меняет классы. Поэтому ищем карточки по нескольким
селекторам и текстовым шаблонам. Если разметка изменилась — парсер
вернёт пустой список, и main.py просто пропустит источник.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from bs4 import Tag

from fetcher import fetch_text
from parsers.base import BaseParser, absolute_url, make_soup


logger = logging.getLogger("parsers.domovita")


PRICE_RE = re.compile(r"(\d[\d\s]*)\s*(\$|USD|р\.|руб|BYN|€|EUR)", re.IGNORECASE)
AREA_RE = re.compile(r"(\d+[.,]?\d*)\s*м²")
ROOMS_RE = re.compile(r"(\d)\s*-?\s*комн", re.IGNORECASE)
FLOOR_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s*эт", re.IGNORECASE)


class DomovitaParser(BaseParser):
    name = "domovita"
    source = "Domovita.by"

    def parse_list(self) -> List[Dict[str, Any]]:
        html = fetch_text(self.list_url)
        if not html:
            return []

        soup = make_soup(html)
        cards = self._find_cards(soup)

        results: List[Dict[str, Any]] = []
        for card in cards:
            listing = self._parse_card(card)
            if listing:
                results.append(listing)

        # Дедуп по URL
        seen: set[str] = set()
        deduped: List[Dict[str, Any]] = []
        for r in results:
            if r["url"] in seen:
                continue
            seen.add(r["url"])
            deduped.append(r)
        return deduped[:50]

    def _find_cards(self, soup: BeautifulSoup) -> List[Tag]:
        # Несколько возможных селекторов карточки
        selectors = [
            "div.listing-item",
            "div.list-item",
            "div.b-listing-item",
            "article",
            "div[itemtype*='Product']",
        ]
        for sel in selectors:
            cards = soup.select(sel)
            if len(cards) >= 3:
                return cards

        # Fallback: ищем по ссылкам на детальную страницу
        links = soup.select("a[href*='/minsk/flats/rent/']") + soup.select("a[href*='/minsk/rooms/rent/']")
        seen = set()
        cards = []
        for link in links:
            parent = link.find_parent(["article", "div", "li"])
            if parent and id(parent) not in seen:
                seen.add(id(parent))
                cards.append(parent)
        return cards

    def _parse_card(self, card: Tag) -> Optional[Dict[str, Any]]:
        link = card.find("a", href=True)
        if not link:
            return None
        url = absolute_url(self.base_url or "https://domovita.by", link.get("href"))
        if not url:
            return None

        # title
        title = None
        title_tag = card.find(["h2", "h3", "h4"])
        if title_tag:
            title = title_tag.get_text(" ", strip=True)
        if not title:
            title = link.get_text(" ", strip=True)
        if not title:
            return None

        text = card.get_text(" ", strip=True)

        price_value, currency, price_str = self._parse_price(text)
        rooms = self._parse_rooms(title + " " + text)
        area = self._parse_area(text)
        floor = self._parse_floor(text)
        district = self._parse_district(card)
        image_url = self._parse_image(card)
        description = self._parse_description(card)

        listing = self.empty_listing()
        listing.update({
            "title": title[:200],
            "price": price_str,
            "price_value": price_value,
            "currency": currency,
            "district": district,
            "rooms": rooms,
            "area": area,
            "floor": floor,
            "description": description,
            "url": url,
            "image_url": image_url,
        })
        return listing

    def _parse_price(self, text: str) -> tuple[int | None, str | None, str | None]:
        match = PRICE_RE.search(text)
        if not match:
            return None, None, None
        raw_num = match.group(1).replace(" ", "").replace("\xa0", "")
        cur_raw = match.group(2).lower()
        try:
            value = int(raw_num)
        except ValueError:
            return None, None, None
        if cur_raw in {"$", "usd"}:
            currency = "USD"
        elif cur_raw in {"€", "eur"}:
            currency = "EUR"
        else:
            currency = "BYN"
        return value, currency, f"{value} {currency}"

    def _parse_rooms(self, text: str) -> str | None:
        m = ROOMS_RE.search(text)
        return m.group(1) if m else None

    def _parse_area(self, text: str) -> str | None:
        m = AREA_RE.search(text)
        if not m:
            return None
        return f"{m.group(1).replace(',', '.')} м²"

    def _parse_floor(self, text: str) -> str | None:
        m = FLOOR_RE.search(text)
        if not m:
            return None
        return f"{m.group(1)} из {m.group(2)}"

    def _parse_district(self, card: Tag) -> str | None:
        for sel in [".district", ".region", "[class*=district]", "[class*=address]"]:
            tag = card.select_one(sel)
            if tag:
                txt = tag.get_text(" ", strip=True)
                if txt:
                    return txt[:80]
        return None

    def _parse_image(self, card: Tag) -> str | None:
        img = card.find("img")
        if not img:
            return None
        for attr in ("data-src", "data-original", "src"):
            value = img.get(attr)
            if value and not value.startswith("data:"):
                if value.startswith("//"):
                    return "https:" + value
                if value.startswith("/"):
                    return absolute_url(self.base_url or "https://domovita.by", value)
                if value.startswith("http"):
                    return value
        return None

    def _parse_description(self, card: Tag) -> str | None:
        for sel in [".description", ".preview", "[class*=description]"]:
            tag = card.select_one(sel)
            if tag:
                txt = tag.get_text(" ", strip=True)
                if txt:
                    return txt[:400]
        return None
