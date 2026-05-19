"""
Парсер Hata.by.

Hata.by на странице /rent-flat/minsk/ (и /rent-room/minsk/) отдаёт
обычный HTML без SPA-обёрток. Карточки объявлений рендерятся в виде
блоков с классом ``b-catalog-table__item``. На страницу обычно
помещается до 50 объявлений; пагинация нам не нужна.

Важно: на странице есть тысячи ссылок вида ``/rent-flat/<street>__st/``
— это боковой каталог улиц («ул. Авакяна (0)»). Брать их за объявления
нельзя; реальные объявления имеют URL ``/object/<id>/``.

Из карточки забираем title, район, цену, площадь, этаж, год постройки,
описание и URL первой картинки. Картинки на Hata лежат за прокси
``pic.hata.by/imagecache/...``; внутри query-параметра ``?image=`` идёт
оригинальный URL — его и используем, чтобы качество в Telegram было
лучше, чем у миниатюры 200×150.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup, Tag

from fetcher import fetch_text
from parsers.base import BaseParser, absolute_url, make_soup


logger = logging.getLogger("parsers.hata")


_PRICE_RE = re.compile(r"(\d[\d\s\u00a0]*)\s*(\$|USD|р\.|руб|BYN|€|EUR)", re.IGNORECASE)
_AREA_RE = re.compile(r"(\d+[.,]?\d*)\s*/\s*(\d+[.,]?\d*)?\s*/?\s*(\d+[.,]?\d*)?", re.IGNORECASE)
_FLOOR_RE = re.compile(r"(\d+)\s*эта\w+\s*\(\s*(\d+)\s*эта", re.IGNORECASE)
_ROOMS_RE = re.compile(r"(\d)\s*-?\s*комн", re.IGNORECASE)


class HataParser(BaseParser):
    name = "hata"
    source = "Hata.by"

    def parse_list(self) -> List[Dict[str, Any]]:
        html = fetch_text(self.list_url)
        if not html:
            return []

        soup = make_soup(html)
        cards = soup.select("div.b-catalog-table__item")
        if not cards:
            logger.warning("[Hata.by] не нашёл ни одной карточки .b-catalog-table__item")
            return []

        results: List[Dict[str, Any]] = []
        for card in cards:
            listing = self._parse_card(card)
            if listing:
                results.append(listing)

        seen: set[str] = set()
        deduped: List[Dict[str, Any]] = []
        for r in results:
            if r["url"] in seen:
                continue
            seen.add(r["url"])
            deduped.append(r)
        return deduped[:50]

    # ---------- одна карточка ----------

    def _parse_card(self, card: Tag) -> Optional[Dict[str, Any]]:
        # URL объявления — берём первую ссылку на /object/<id>/
        object_link = card.select_one("a[href*='/object/']")
        if not object_link:
            return None
        url = absolute_url(self.base_url or "https://hata.by", object_link.get("href") or "")
        if not url:
            return None

        title_tag = card.select_one(".title a") or object_link
        title = title_tag.get_text(" ", strip=True)
        if not title:
            return None
        title = re.sub(r"\s+", " ", title)[:200]

        price_value, currency, price_str = self._parse_price(card)
        district = self._parse_district(card)
        image_url = self._parse_image(card)
        description = self._parse_description(card)
        area_total = self._parse_area_total(card)
        floor = self._parse_floor(card)
        rooms = self._parse_rooms(title)
        external_id = self._parse_external_id(card, url)

        listing = self.empty_listing()
        listing.update({
            "title": title,
            "price": price_str,
            "price_value": price_value,
            "currency": currency,
            "district": district,
            "rooms": rooms,
            "area": area_total,
            "floor": floor,
            "description": description,
            "url": url,
            "image_url": image_url,
            "external_id": external_id,
        })
        return listing

    # ---------- кусочки ----------

    def _parse_price(self, card: Tag) -> tuple[int | None, str | None, str | None]:
        value_tag = card.select_one(".price .value")
        text = value_tag.get_text(" ", strip=True) if value_tag else card.get_text(" ", strip=True)
        m = _PRICE_RE.search(text)
        if not m:
            return None, None, None
        raw_num = re.sub(r"\D", "", m.group(1))
        if not raw_num:
            return None, None, None
        try:
            value = int(raw_num)
        except ValueError:
            return None, None, None
        cur_raw = m.group(2).lower()
        if cur_raw in {"$", "usd"}:
            currency = "USD"
        elif cur_raw in {"€", "eur"}:
            currency = "EUR"
        else:
            currency = "BYN"
        return value, currency, f"{value} {currency}"

    def _parse_area_total(self, card: Tag) -> str | None:
        # .price .num содержит «45 /43/12 м²» (общая/жилая/кухня).
        # Для квартиры берём общую (первое число). Для комнаты — её жилую
        # площадь (второе число), но мы это решим позже по category.
        num_tag = card.select_one(".price .num")
        if not num_tag:
            return None
        text = num_tag.get_text(" ", strip=True)
        if self.category == "room":
            # второй компонент — жилая площадь комнаты
            m = _AREA_RE.search(text)
            if m and m.group(2):
                return f"{m.group(2).replace(',', '.')} м²"
            # fallback на общую, если жилой нет
            if m and m.group(1):
                return f"{m.group(1).replace(',', '.')} м²"
            return None

        m = _AREA_RE.search(text)
        if m and m.group(1):
            return f"{m.group(1).replace(',', '.')} м²"
        return None

    def _parse_floor(self, card: Tag) -> str | None:
        info = card.select_one(".building-info")
        text = info.get_text(" ", strip=True) if info else card.get_text(" ", strip=True)
        m = _FLOOR_RE.search(text)
        if not m:
            return None
        try:
            floor = int(m.group(1))
            total = int(m.group(2))
        except (TypeError, ValueError):
            return f"{m.group(1)} из {m.group(2)}"
        if total <= 0:
            return str(floor) if floor > 0 else None
        if floor <= 0:
            return f"? из {total}"
        return f"{floor} из {total}"

    def _parse_rooms(self, title: str) -> str | None:
        m = _ROOMS_RE.search(title)
        return m.group(1) if m else None

    def _parse_district(self, card: Tag) -> str | None:
        # Район лежит в .info первый <span> рядом с иконкой карты.
        for span in card.select(".info span"):
            txt = span.get_text(" ", strip=True)
            if not txt:
                continue
            low = txt.lower()
            if "просмотр" in low or "отзыв" in low:
                continue
            if "район" in low or "минск" in low:
                return txt[:80]
        return None

    def _parse_image(self, card: Tag) -> str | None:
        # Hata прокси: src вида
        #   https://pic.hata.by/imagecache/<id>/200x150/?image=<orig>&s=34
        # достаём оригинал из query-параметра image, если он есть.
        for img in card.select("img"):
            for attr in ("data-lazy", "data-src", "data-original", "src"):
                raw = img.get(attr)
                if not raw or raw.startswith("data:"):
                    continue
                value = raw.strip()
                if value.startswith("//"):
                    value = "https:" + value
                if not value.startswith("http"):
                    value = absolute_url(self.base_url or "https://hata.by", value)
                if not value:
                    continue

                # Пытаемся вытащить оригинальную ссылку из query ?image=…
                try:
                    parsed = urlparse(value)
                    qs = parse_qs(parsed.query)
                    inner = qs.get("image", [None])[0]
                    if inner:
                        return unquote(inner)
                except ValueError:
                    pass
                return value
        return None

    def _parse_description(self, card: Tag) -> str | None:
        tag = card.select_one(".description")
        if not tag:
            return None
        txt = tag.get_text(" ", strip=True)
        return txt[:400] if txt else None

    def _parse_external_id(self, card: Tag, url: str) -> str | None:
        # 1) data-id у кнопки «избранное»
        fav = card.select_one("[data-id]")
        if fav and fav.get("data-id"):
            return str(fav["data-id"]).strip()
        # 2) /object/<id>/
        m = re.search(r"/object/(\d+)/?", url)
        return m.group(1) if m else None
