"""
Парсер Kufar (re.kufar.by).

Kufar — Next.js. Все данные ленты лежат в <script id="__NEXT_DATA__">.
Парсим JSON, оттуда берём только нужные поля. Если Kufar блокирует —
отключаем через ENABLE_KUFAR=false в .env.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from bs4 import BeautifulSoup

from fetcher import fetch_text
from parsers.base import BaseParser, absolute_url


logger = logging.getLogger("parsers.kufar")


class KufarParser(BaseParser):
    name = "kufar"
    source = "Kufar"

    def parse_list(self) -> List[Dict[str, Any]]:
        html = fetch_text(self.list_url)
        if not html:
            return []

        items = self._parse_next_data(html)
        if items:
            return items

        return self._parse_html(html)

    def _parse_next_data(self, html: str) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
        tag = soup.find("script", id="__NEXT_DATA__")
        if not tag or not tag.string:
            return []

        try:
            data = json.loads(tag.string)
        except json.JSONDecodeError:
            return []

        ads = self._find_ads(data)
        results: List[Dict[str, Any]] = []
        for ad in ads:
            if not isinstance(ad, dict):
                continue

            url = ad.get("ad_link") or ad.get("url")
            url = absolute_url(self.base_url or "https://re.kufar.by", url)
            if not url:
                continue

            title = ad.get("subject") or ad.get("title")
            if not title:
                continue

            price_value, currency = self._extract_price(ad)
            image_url = self._extract_image(ad)
            params = self._params_to_dict(ad.get("ad_parameters") or ad.get("adParameters") or [])

            rooms = params.get("rooms") or params.get("rooms_count")
            area = params.get("size") or params.get("square")
            floor = params.get("floor")
            floors_total = params.get("floors") or params.get("floor_total")
            address = ad.get("address") or params.get("address")
            district = params.get("district") or params.get("region")
            metro = params.get("metro") or params.get("subway")

            listing = self.empty_listing()
            listing.update({
                "title": str(title).strip(),
                "price_value": price_value,
                "price": f"{price_value} {currency}" if price_value and currency else None,
                "currency": currency,
                "district": district,
                "address": address,
                "metro": metro,
                "rooms": str(rooms) if rooms else None,
                "area": f"{area} м²" if area else None,
                "floor": (f"{floor} из {floors_total}" if floor and floors_total else (str(floor) if floor else None)),
                "description": ad.get("body") or ad.get("description"),
                "url": url,
                "image_url": image_url,
                "external_id": str(ad.get("ad_id") or ad.get("id") or ""),
            })
            results.append(listing)

        return results

    def _find_ads(self, data: Any) -> List[Any]:
        """Ищем массив объявлений."""
        candidates: List[List[Any]] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in {"ads", "items", "results"} and isinstance(value, list):
                        if value and isinstance(value[0], dict):
                            candidates.append(value)
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(data)
        if not candidates:
            return []
        candidates.sort(key=len, reverse=True)
        for cand in candidates:
            first = cand[0]
            if isinstance(first, dict) and any(
                k in first for k in ("ad_id", "ad_link", "subject", "price_byn", "price_usd")
            ):
                return cand
        return candidates[0]

    def _extract_price(self, ad: Dict[str, Any]) -> tuple[int | None, str | None]:
        # Kufar отдаёт цену умноженной на 100 (в копейках)
        for key, currency, divisor in (
            ("price_usd", "USD", 100),
            ("priceUsd", "USD", 100),
            ("price_byn", "BYN", 100),
            ("priceByn", "BYN", 100),
        ):
            value = ad.get(key)
            if value:
                try:
                    return int(float(value) / divisor), currency
                except (TypeError, ValueError):
                    pass
        return None, None

    def _extract_image(self, ad: Dict[str, Any]) -> str | None:
        images = ad.get("images")
        if isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, dict):
                # Kufar шаблон URL картинки: yams.kufar.by/api/v1/kufar-ads/images/<id>/<size>.jpg
                image_id = first.get("id") or first.get("path")
                if image_id and isinstance(image_id, str):
                    if image_id.startswith("http"):
                        return image_id
                    return f"https://yams.kufar.by/api/v1/kufar-ads/images/{image_id[:2]}/{image_id}.jpg?rule=gallery"
                url = first.get("url") or first.get("src")
                if url:
                    return url
            elif isinstance(first, str):
                if first.startswith("http"):
                    return first
        return None

    def _params_to_dict(self, params: Any) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        if not isinstance(params, list):
            return result
        for p in params:
            if not isinstance(p, dict):
                continue
            key = (p.get("p") or p.get("name") or p.get("pl") or "").lower()
            value = p.get("v") or p.get("value") or p.get("vl")
            if key:
                result[key] = value
        return result

    def _parse_html(self, html: str) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
        results: List[Dict[str, Any]] = []
        for link in soup.select("a[href*='/vi/']"):
            href = link.get("href") or ""
            url = absolute_url(self.base_url or "https://re.kufar.by", href)
            if not url:
                continue
            title = link.get_text(" ", strip=True)
            if not title:
                continue
            listing = self.empty_listing()
            listing.update({"title": title[:120], "url": url})
            results.append(listing)

        seen = set()
        deduped: List[Dict[str, Any]] = []
        for r in results:
            if r["url"] in seen:
                continue
            seen.add(r["url"])
            deduped.append(r)
        return deduped[:50]
