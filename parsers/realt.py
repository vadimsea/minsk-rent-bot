"""
Парсер Realt.by.

Realt.by рендерит данные через Next.js и кладёт большой JSON в
<script id="__NEXT_DATA__">. Это удобнее, чем разбирать HTML карточек,
поэтому пытаемся вытащить данные оттуда, а HTML используем как fallback.

Берём только минимальную информацию: цена, тип, район, площадь, этаж,
краткое описание (обрезаем), одна фотография и ссылка на оригинал.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from fetcher import fetch_text
from parsers.base import BaseParser, absolute_url, make_soup


logger = logging.getLogger("parsers.realt")


# Realt отдаёт код валюты по ISO 4217 (число)
ISO_CURRENCY = {
    840: "USD",
    933: "BYN",
    978: "EUR",
    643: "RUB",
}


class RealtParser(BaseParser):
    name = "realt"
    source = "Realt.by"

    def parse_list(self) -> List[Dict[str, Any]]:
        html = fetch_text(self.list_url)
        if not html:
            return []

        items = self._parse_next_data(html)
        if items:
            return items

        # fallback: разбираем HTML
        return self._parse_html(html)

    # ---------- основной путь: __NEXT_DATA__ ----------
    def _parse_next_data(self, html: str) -> List[Dict[str, Any]]:
        soup = make_soup(html)
        tag = soup.find("script", id="__NEXT_DATA__")
        if not tag or not tag.string:
            return []

        try:
            data = json.loads(tag.string)
        except json.JSONDecodeError:
            return []

        objects = self._extract_objects(data)
        if not objects:
            return []

        results: List[Dict[str, Any]] = []
        for obj in objects:
            if not isinstance(obj, dict):
                continue

            url = self._build_object_url(obj)
            if not url:
                continue

            title = (
                obj.get("title")
                or obj.get("seoTitle")
                or self._build_title(obj)
            )
            if not title:
                continue

            price_value, currency = self._extract_price(obj)
            image_url = self._extract_image(obj)
            district = self._extract_district(obj)
            metro = self._extract_metro(obj)
            rooms = obj.get("rooms") or obj.get("roomsCount")
            area = obj.get("areaTotal") or obj.get("area")
            floor = self._extract_floor(obj)
            description = obj.get("description") or obj.get("shortDescription") or obj.get("headline")
            address = obj.get("address")
            city = obj.get("townName") or obj.get("town") or obj.get("city")

            listing = self.empty_listing()
            listing.update({
                "title": str(title).strip(),
                "price_value": price_value,
                "price": f"{price_value} {currency}" if price_value and currency else None,
                "currency": currency,
                "city": str(city).strip() if isinstance(city, str) and city.strip() else None,
                "district": district,
                "address": address,
                "metro": metro,
                "rooms": str(rooms) if rooms else None,
                "area": f"{area} м²" if area else None,
                "floor": floor,
                "description": description,
                "url": url,
                "image_url": image_url,
                "external_id": str(obj.get("uuid") or obj.get("code") or obj.get("id") or ""),
            })
            results.append(listing)

        return results

    def _build_object_url(self, obj: Dict[str, Any]) -> str | None:
        # Самый стабильный путь: /rent-flat-for-long/object/<code>/
        existing = obj.get("url") or obj.get("link")
        if existing:
            return absolute_url(self.base_url or "https://realt.by", existing)

        code = obj.get("code") or obj.get("uuid") or obj.get("id")
        if not code:
            return None

        prefix = "rent-room-for-long" if self.category == "room" else "rent-flat-for-long"
        return f"{(self.base_url or 'https://realt.by').rstrip('/')}/{prefix}/object/{code}/"

    def _extract_objects(self, data: Any) -> List[Any]:
        """Ищем в __NEXT_DATA__ массив объектов недвижимости."""
        candidates: List[List[Any]] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in {"objects", "items", "results", "data"} and isinstance(value, list):
                        if value and isinstance(value[0], dict):
                            candidates.append(value)
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(data)
        if not candidates:
            return []

        # Берём самый длинный массив словарей, который похож на список объявлений
        candidates.sort(key=len, reverse=True)
        for cand in candidates:
            sample = cand[0]
            if isinstance(sample, dict) and any(
                k in sample for k in ("uuid", "id", "url", "title", "priceMin", "price")
            ):
                return cand
        return []

    def _extract_price(self, obj: Dict[str, Any]) -> tuple[int | None, str | None]:
        # Текущий формат Realt: price + priceCurrency (ISO 4217 numeric)
        price = obj.get("price")
        price_currency = obj.get("priceCurrency")
        if price and price_currency:
            try:
                value = int(float(price))
                currency = ISO_CURRENCY.get(int(price_currency))
                if value > 0 and currency:
                    return value, currency
            except (TypeError, ValueError):
                pass

        # Историческое: priceUsd / priceByn / priceEur
        for key, currency in (
            ("priceUsd", "USD"),
            ("price_usd", "USD"),
            ("priceByn", "BYN"),
            ("price_byn", "BYN"),
            ("priceEur", "EUR"),
        ):
            value = obj.get(key)
            if value:
                try:
                    return int(float(value)), currency
                except (TypeError, ValueError):
                    pass

        prices = obj.get("prices")
        if isinstance(prices, list):
            for p in prices:
                if isinstance(p, dict):
                    val = p.get("value") or p.get("amount")
                    cur = (p.get("currency") or "").upper()
                    if val and cur in {"USD", "BYN", "EUR"}:
                        try:
                            return int(float(val)), cur
                        except (TypeError, ValueError):
                            continue
        return None, None

    def _extract_image(self, obj: Dict[str, Any]) -> str | None:
        for key in ("mainPhoto", "photo", "image"):
            value = obj.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
            if isinstance(value, dict):
                url = value.get("url") or value.get("src") or value.get("path")
                if url:
                    return url if url.startswith("http") else "https:" + url

        photos = obj.get("photos") or obj.get("images")
        if isinstance(photos, list) and photos:
            first = photos[0]
            if isinstance(first, str):
                return first if first.startswith("http") else "https:" + first
            if isinstance(first, dict):
                return first.get("url") or first.get("src") or first.get("big") or first.get("preview")
        return None

    def _extract_district(self, obj: Dict[str, Any]) -> str | None:
        """
        Берём ТОЛЬКО городской район (Советский / Фрунзенский / ...).
        stateDistrictName специально игнорируем — у Realt он для квартир
        внутри Минска всегда равен «Минский» (это район Минской ОБЛАСТИ
        вокруг города), и выводить такое в посте бессмысленно и
        вводит в заблуждение, будто квартира в пригороде.
        """
        for key in ("townDistrictName", "townDistrict", "districtName", "district"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                name = value.get("name")
                if name:
                    return str(name).strip()
        return None

    def _extract_metro(self, obj: Dict[str, Any]) -> str | None:
        for key in ("metroStationName", "metro", "metroStation", "subway"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                name = value.get("name")
                if name:
                    return str(name).strip()
            if isinstance(value, list) and value:
                first = value[0]
                if isinstance(first, dict):
                    name = first.get("name")
                    if name:
                        return str(name).strip()
                if isinstance(first, str):
                    return first.strip()
        return None

    def _extract_floor(self, obj: Dict[str, Any]) -> str | None:
        floor = obj.get("storey") or obj.get("floor")
        floors = (
            obj.get("storeys")
            or obj.get("floors")
            or obj.get("floorsTotal")
            or obj.get("floorTotal")
        )
        if floor and floors:
            return f"{floor} из {floors}"
        if floor:
            return str(floor)
        return None

    def _build_title(self, obj: Dict[str, Any]) -> str | None:
        rooms = obj.get("rooms") or obj.get("roomsCount")
        if self.category == "room":
            return "Комната в Минске"
        if rooms:
            return f"{rooms}-комнатная квартира, Минск"
        return "Квартира в Минске"

    # ---------- fallback: HTML ----------
    def _parse_html(self, html: str) -> List[Dict[str, Any]]:
        soup = make_soup(html)
        results: List[Dict[str, Any]] = []

        # Realt меняет вёрстку, поэтому ищем все ссылки на /object/
        for link in soup.select("a[href*='/object/']"):
            href = link.get("href") or ""
            url = absolute_url(self.base_url or "https://realt.by", href)
            if not url:
                continue
            text = link.get_text(" ", strip=True)
            if not text:
                continue

            listing = self.empty_listing()
            listing.update({
                "title": text[:120],
                "url": url,
            })
            results.append(listing)

        # Дедуп по URL внутри страницы
        seen = set()
        deduped: List[Dict[str, Any]] = []
        for r in results:
            if r["url"] in seen:
                continue
            seen.add(r["url"])
            deduped.append(r)
        return deduped[:50]
