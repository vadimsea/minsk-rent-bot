"""
Парсер Onliner.

У Onliner есть стабильное JSON API долгосрочной аренды:
https://r.onliner.by/sdk/search/apartments?...

В MVP источник выключен через ENABLE_ONLINER=false. Когда понадобится —
включить в .env и проверить актуальность API. Здесь даём готовый клиент,
который возвращает данные в едином формате.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fetcher import fetch
from parsers.base import BaseParser


logger = logging.getLogger("parsers.onliner")


API_URL = "https://r.onliner.by/sdk/search/apartments"


class OnlinerParser(BaseParser):
    name = "onliner"
    source = "Onliner"

    def parse_list(self) -> List[Dict[str, Any]]:
        params = {
            "rent_type[]": "1_room",
            "page": "1",
            "v": "0.838",
        }
        response = fetch(
            API_URL,
            params=params,
            headers={"Accept": "application/json"},
        )
        if response is None:
            return []

        try:
            data = response.json()
        except ValueError:
            logger.warning("Onliner вернул не-JSON ответ")
            return []

        apartments = data.get("apartments") if isinstance(data, dict) else None
        if not isinstance(apartments, list):
            return []

        results: List[Dict[str, Any]] = []
        for item in apartments:
            if not isinstance(item, dict):
                continue

            url = item.get("url")
            if not url:
                continue

            price_value = None
            currency = None
            price_obj = item.get("price") or {}
            converted = price_obj.get("converted") if isinstance(price_obj, dict) else None
            if isinstance(converted, dict):
                usd = converted.get("USD") or {}
                if isinstance(usd, dict) and usd.get("amount"):
                    try:
                        price_value = int(float(usd["amount"]))
                        currency = "USD"
                    except (TypeError, ValueError):
                        pass

            rooms = item.get("rent_type", "")
            rooms_num = "".join(ch for ch in str(rooms) if ch.isdigit()) or None

            location = item.get("location") or {}
            address = location.get("user_address") if isinstance(location, dict) else None

            photo = item.get("photo")

            listing = self.empty_listing()
            listing.update({
                "title": (
                    f"{rooms_num}-комнатная квартира, Минск"
                    if rooms_num else "Квартира в Минске"
                ),
                "price_value": price_value,
                "price": f"{price_value} {currency}" if price_value and currency else None,
                "currency": currency,
                "address": address,
                "rooms": rooms_num,
                "url": url,
                "image_url": photo if isinstance(photo, str) and photo.startswith("http") else None,
                "external_id": str(item.get("id") or ""),
            })
            results.append(listing)

        return results
