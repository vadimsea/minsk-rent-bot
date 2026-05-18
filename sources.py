"""
Список источников объявлений.

Каждый источник описывается метаданными, на основе которых main.py
выбирает парсер и решает, обрабатывать ли его. Чтобы быстро отключить
любой сайт — поставить enabled=False или соответствующий ENABLE_* в .env.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from config import CONFIG


@dataclass(frozen=True)
class Source:
    name: str
    base_url: str
    list_url: str
    enabled: bool
    parser_name: str
    city: str
    category: str         # "apartment" | "room"
    rent_period: str      # "long_term"
    source_priority: int  # чем меньше — тем выше приоритет

    @property
    def safe_id(self) -> str:
        """Идентификатор источника для логов и БД."""
        return f"{self.parser_name}:{self.category}"


def _enabled(flag_name: str) -> bool:
    return getattr(CONFIG, flag_name, False)


def get_sources() -> List[Source]:
    """Возвращает все источники, отсортированные по приоритету."""
    sources: List[Source] = [
        # ---------------- Realt.by ----------------
        Source(
            name="Realt.by",
            base_url="https://realt.by",
            list_url="https://realt.by/rent/flat-for-long/",
            enabled=_enabled("enable_realt"),
            parser_name="realt",
            city="Минск",
            category="apartment",
            rent_period="long_term",
            source_priority=1,
        ),
        Source(
            name="Realt.by",
            base_url="https://realt.by",
            list_url="https://realt.by/rent/room-for-long/",
            enabled=_enabled("enable_realt"),
            parser_name="realt",
            city="Минск",
            category="room",
            rent_period="long_term",
            source_priority=2,
        ),

        # ---------------- Kufar ----------------
        Source(
            name="Kufar",
            base_url="https://re.kufar.by",
            list_url="https://re.kufar.by/l/minsk/snyat/kvartiru-dolgosrochno",
            enabled=_enabled("enable_kufar"),
            parser_name="kufar",
            city="Минск",
            category="apartment",
            rent_period="long_term",
            source_priority=3,
        ),
        Source(
            name="Kufar",
            base_url="https://re.kufar.by",
            list_url="https://re.kufar.by/l/minsk/snyat/komnatu-dolgosrochno",
            enabled=_enabled("enable_kufar"),
            parser_name="kufar",
            city="Минск",
            category="room",
            rent_period="long_term",
            source_priority=4,
        ),

        # ---------------- Domovita.by ----------------
        Source(
            name="Domovita.by",
            base_url="https://domovita.by",
            list_url="https://domovita.by/minsk/flats/rent",
            enabled=_enabled("enable_domovita"),
            parser_name="domovita",
            city="Минск",
            category="apartment",
            rent_period="long_term",
            source_priority=5,
        ),
        Source(
            name="Domovita.by",
            base_url="https://domovita.by",
            list_url="https://domovita.by/minsk/room/rent",
            enabled=_enabled("enable_domovita"),
            parser_name="domovita",
            city="Минск",
            category="room",
            rent_period="long_term",
            source_priority=6,
        ),

        # ---------------- Hata.by ----------------
        Source(
            name="Hata.by",
            base_url="https://hata.by",
            list_url="https://hata.by/rent-flat/minsk/",
            enabled=_enabled("enable_hata"),
            parser_name="hata",
            city="Минск",
            category="apartment",
            rent_period="long_term",
            source_priority=7,
        ),
        Source(
            name="Hata.by",
            base_url="https://hata.by",
            list_url="https://hata.by/rent-room/minsk/",
            enabled=_enabled("enable_hata"),
            parser_name="hata",
            city="Минск",
            category="room",
            rent_period="long_term",
            source_priority=8,
        ),

        # ---------------- Onliner (зарезервировано) ----------------
        Source(
            name="Onliner",
            base_url="https://r.onliner.by",
            list_url="https://r.onliner.by/ak/?rent_type%5B%5D=1_room&rent_type%5B%5D=2_rooms&rent_type%5B%5D=3_rooms&rent_type%5B%5D=4_rooms%2B&only_owner=false",
            enabled=_enabled("enable_onliner"),
            parser_name="onliner",
            city="Минск",
            category="apartment",
            rent_period="long_term",
            source_priority=9,
        ),
    ]

    return sorted(sources, key=lambda s: s.source_priority)


def get_enabled_sources() -> List[Source]:
    return [s for s in get_sources() if s.enabled]
