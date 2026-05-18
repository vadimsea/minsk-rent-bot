"""
Парсер Kufar (re.kufar.by).

Kufar — Next.js. Все данные ленты лежат в <script id="__NEXT_DATA__">.
Парсим JSON, оттуда берём только нужные поля. Если Kufar блокирует —
отключаем через ENABLE_KUFAR=false в .env.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List


# Реальные image_id у Kufar выглядят как длинный hex-хеш (40+ символов).
# Заглушки/пустые значения вроде "0", "0000", "none" — фильтруем,
# чтобы не строить URL вида yams.kufar.by/.../00/0000.jpg
_HEX_HASH_RE = re.compile(r"^[0-9a-fA-F]{16,}$")

from fetcher import fetch_text
from parsers.base import BaseParser, absolute_url, make_soup


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
        soup = make_soup(html)
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
            title = str(title).strip()
            # subject у Kufar часто склеен с адресом через "/", "," или " - ".
            # Берём первую логичную часть, чтобы заголовок выглядел опрятно.
            for sep in (" / ", "/", " — ", " - ", ","):
                if sep in title:
                    candidate = title.split(sep, 1)[0].strip()
                    if len(candidate) >= 8:
                        title = candidate
                        break

            price_value, currency = self._extract_price(ad)
            image_url = self._extract_image(ad)
            params = self._params_to_dict(ad.get("ad_parameters") or ad.get("adParameters") or [])

            rooms = params.get("rooms") or params.get("rooms_count")
            area = (
                params.get("size")
                or params.get("square")
                or params.get("size_m2")
            )
            floor = params.get("floor") or params.get("re_number_floor")
            floors_total = (
                params.get("floors")
                or params.get("floors_count")
                or params.get("floor_total")
                or params.get("re_number_floors")
            )
            address = ad.get("address") or params.get("address")
            # На Kufar районы Минска лежат в параметре "area" (например, "Московский").
            # "district" — это, наоборот, муниципальный/областной район; в Минске
            # он обычно совпадает с region и нам не нужен. "re_district" — это
            # микрорайон ("Юго-Запад").
            district = params.get("area") or params.get("re_district")
            metro = (
                params.get("metro")
                or params.get("rent_metro")
                or params.get("subway")
                or params.get("metro_station")
                or params.get("nearest_metro")
            )

            listing = self.empty_listing()
            listing.update({
                "title": title,
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
        # 1) Если есть готовый абсолютный URL — используем его
        for key in ("image_url", "imageUrl", "main_image_url", "photo"):
            value = ad.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value

        images = ad.get("images")
        if not isinstance(images, list) or not images:
            return None

        # Перебираем первые несколько картинок (некоторые могут быть заглушками)
        for img in images[:5]:
            if isinstance(img, str):
                if img.startswith("http"):
                    return img
                continue

            if not isinstance(img, dict):
                continue

            # 2) Прямая ссылка
            for key in ("url", "src", "image_url", "imageUrl"):
                direct = img.get(key)
                if isinstance(direct, str) and direct.startswith("http"):
                    return direct

            # 3) ID картинки → строим URL шаблона Kufar
            image_id = img.get("id") or img.get("path") or img.get("media_storage")
            if not isinstance(image_id, str):
                continue

            image_id = image_id.strip()
            if not image_id or not _HEX_HASH_RE.match(image_id):
                # Похоже на заглушку ("0000", "none", и т.п.) — пропускаем
                continue

            return (
                "https://yams.kufar.by/api/v1/kufar-ads/images/"
                f"{image_id[:2]}/{image_id}.jpg?rule=gallery"
            )

        return None

    def _params_to_dict(self, params: Any) -> Dict[str, Any]:
        """
        Разворачивает Kufar ad_parameters в плоский словарь.
        ВАЖНО: для каждого параметра берём именно `vl` (value label) —
        человекочитаемое значение. Поле `v` содержит ID/коды Kufar,
        которые в посте показывать нельзя (получится «Метро: [3, 17]»).
        """
        result: Dict[str, Any] = {}
        if not isinstance(params, list):
            return result

        for p in params:
            if not isinstance(p, dict):
                continue
            key = (p.get("p") or p.get("name") or "").lower()
            if not key:
                continue

            raw = p.get("vl")
            if raw is None or raw == "" or raw == []:
                raw = p.get("value_label")
            if raw is None or raw == "" or raw == []:
                fallback = p.get("v") if "v" in p else p.get("value")
                if isinstance(fallback, (list, tuple)):
                    # Если массив из одного числа/строки (часто бывает у floor,
                    # rooms, area) — это не ID, можно развернуть.
                    if len(fallback) == 1 and isinstance(fallback[0], (int, float, str)):
                        raw = fallback[0]
                    else:
                        # Несколько ID — без vl не расшифровать.
                        raw = None
                else:
                    raw = fallback

            result[key] = self._stringify_param(raw)
        return result

    @staticmethod
    def _stringify_param(value: Any) -> Any:
        """Делает из листов читаемую строку, остальные значения отдаёт как есть."""
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            # фильтруем None / пустые
            items = [str(v).strip() for v in value if v not in (None, "", [])]
            if not items:
                return None
            if len(items) == 1:
                return items[0]
            return ", ".join(items)
        return value

    def _parse_html(self, html: str) -> List[Dict[str, Any]]:
        soup = make_soup(html)
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
