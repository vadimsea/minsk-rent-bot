"""
Загрузка конфигурации из .env.

Все настройки приложения собраны здесь, чтобы остальной код не зависел
напрямую от переменных окружения.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value is not None else default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _list(name: str, default: List[str] | None = None) -> List[str]:
    raw = os.getenv(name)
    if not raw:
        return list(default or [])
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


@dataclass
class AppConfig:
    # ---- Telegram ----
    telegram_bot_token: str = field(default_factory=lambda: _str("TELEGRAM_BOT_TOKEN"))
    telegram_channel_id: str = field(default_factory=lambda: _str("TELEGRAM_CHANNEL_ID"))

    # ---- Режим ----
    dry_run: bool = field(default_factory=lambda: _bool("DRY_RUN", True))
    posts_per_run: int = field(default_factory=lambda: _int("POSTS_PER_RUN", 3))
    post_interval_seconds: int = field(default_factory=lambda: _int("POST_INTERVAL_SECONDS", 60))

    # Для режима SCHEDULE_MODE=random_daily
    posts_per_day: int = field(default_factory=lambda: _int("POSTS_PER_DAY", 12))
    post_window_start: str = field(default_factory=lambda: _str("POST_WINDOW_START", "10:00"))
    post_window_end: str = field(default_factory=lambda: _str("POST_WINDOW_END", "20:00"))

    # ---- Фильтры ----
    city: str = field(default_factory=lambda: _str("CITY", "Минск"))
    rent_type: str = field(default_factory=lambda: _str("RENT_TYPE", "long_term").lower())
    allowed_listing_types: List[str] = field(
        default_factory=lambda: _list("ALLOWED_LISTING_TYPES", ["apartment", "room"])
    )
    require_photo: bool = field(default_factory=lambda: _bool("REQUIRE_PHOTO", True))

    min_room_price_usd: int = field(default_factory=lambda: _int("MIN_ROOM_PRICE_USD", 80))
    min_apartment_price_usd: int = field(default_factory=lambda: _int("MIN_APARTMENT_PRICE_USD", 150))
    max_price_usd: int = field(default_factory=lambda: _int("MAX_PRICE_USD", 2000))

    # ---- Источники ----
    enable_realt: bool = field(default_factory=lambda: _bool("ENABLE_REALT", True))
    enable_kufar: bool = field(default_factory=lambda: _bool("ENABLE_KUFAR", True))
    enable_domovita: bool = field(default_factory=lambda: _bool("ENABLE_DOMOVITA", True))
    enable_hata: bool = field(default_factory=lambda: _bool("ENABLE_HATA", True))
    enable_onliner: bool = field(default_factory=lambda: _bool("ENABLE_ONLINER", False))

    # ---- HTTP ----
    request_timeout: int = field(default_factory=lambda: _int("REQUEST_TIMEOUT", 15))
    request_delay_seconds: float = field(default_factory=lambda: _float("REQUEST_DELAY_SECONDS", 3.0))
    request_max_retries: int = field(default_factory=lambda: _int("REQUEST_MAX_RETRIES", 3))
    user_agent: str = field(
        default_factory=lambda: _str(
            "USER_AGENT", "Mozilla/5.0 RentAggregatorBot/1.0"
        )
    )

    # ---- Планировщик ----
    schedule_mode: str = field(default_factory=lambda: _str("SCHEDULE_MODE", "interval").lower())
    run_every_hours: int = field(default_factory=lambda: _int("RUN_EVERY_HOURS", 6))
    post_time: str = field(default_factory=lambda: _str("POST_TIME", "10:00"))
    timezone: str = field(default_factory=lambda: _str("TIMEZONE", "Europe/Minsk"))

    # ---- Логирование ----
    log_level: str = field(default_factory=lambda: _str("LOG_LEVEL", "INFO").upper())
    log_file: str = field(default_factory=lambda: _str("LOG_FILE", "rent_bot.log"))

    # ---- Хранилище ----
    db_path: str = field(default_factory=lambda: _str("DB_PATH", "published.sqlite3"))

    def is_source_enabled(self, source_name: str) -> bool:
        name = (source_name or "").strip().lower()
        mapping = {
            "realt.by": self.enable_realt,
            "kufar": self.enable_kufar,
            "domovita.by": self.enable_domovita,
            "hata.by": self.enable_hata,
            "onliner": self.enable_onliner,
        }
        return bool(mapping.get(name, False))


CONFIG = AppConfig()


def setup_logging(cfg: AppConfig = CONFIG) -> None:
    """Инициализация логирования: и в файл, и в консоль."""
    level = getattr(logging, cfg.log_level, logging.INFO)

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if cfg.log_file:
        try:
            handlers.append(logging.FileHandler(cfg.log_file, encoding="utf-8"))
        except OSError:
            pass

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=handlers,
        force=True,
    )
