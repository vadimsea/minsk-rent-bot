"""
Хранилище опубликованных объявлений (SQLite).

Назначение:
- понять, публиковали ли мы уже эту ссылку (is_published);
- зафиксировать факт публикации (mark_as_published);
- посмотреть, что публиковалось недавно (get_recent_published) — для отчётов.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional

from config import CONFIG


logger = logging.getLogger("storage")


SCHEMA = """
CREATE TABLE IF NOT EXISTS published (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    source TEXT,
    listing_type TEXT,
    title TEXT,
    price TEXT,
    published_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_published_url ON published(url);
CREATE INDEX IF NOT EXISTS idx_published_at ON published(published_at);

-- Слоты «12 раз в день в случайное время» — отметка о выполнении.
-- Используется в режиме --cron-tick (GitHub Actions / cron),
-- чтобы один и тот же случайный слот не сработал дважды.
CREATE TABLE IF NOT EXISTS daily_slots (
    day TEXT NOT NULL,
    slot TEXT NOT NULL,
    executed_at TEXT NOT NULL,
    PRIMARY KEY (day, slot)
);

CREATE TABLE IF NOT EXISTS promo_posts (
    day TEXT NOT NULL,
    promo_key TEXT NOT NULL,
    message_id INTEGER,
    posted_at TEXT NOT NULL,
    PRIMARY KEY (day, promo_key)
);
"""


class Storage:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or CONFIG.db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    @contextmanager
    def _connect(self) -> Iterable[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def is_published(self, url: str) -> bool:
        if not url:
            return False
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT 1 FROM published WHERE url = ? LIMIT 1", (url,))
            return cur.fetchone() is not None

    def mark_as_published(self, listing: Dict[str, Any]) -> None:
        url = listing.get("url")
        if not url:
            logger.warning("mark_as_published: пустой url, пропуск")
            return
        with self._lock, self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO published
                    (url, source, listing_type, title, price, published_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        url,
                        listing.get("source"),
                        listing.get("listing_type"),
                        listing.get("title"),
                        listing.get("price"),
                        datetime.utcnow().isoformat(timespec="seconds"),
                    ),
                )
                conn.commit()
            except sqlite3.Error as exc:
                logger.error("Ошибка записи в БД: %s", exc)

    def get_recent_published(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                SELECT url, source, listing_type, title, price, published_at
                FROM published
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]

    def count(self) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM published")
            row = cur.fetchone()
            return int(row[0]) if row else 0

    # -------- daily_slots --------

    @staticmethod
    def _day_key(day: date | str) -> str:
        if isinstance(day, date):
            return day.isoformat()
        return str(day)

    def is_slot_executed(self, day: date | str, slot: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "SELECT 1 FROM daily_slots WHERE day = ? AND slot = ? LIMIT 1",
                (self._day_key(day), slot),
            )
            return cur.fetchone() is not None

    def mark_slot_executed(self, day: date | str, slot: str) -> None:
        with self._lock, self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO daily_slots (day, slot, executed_at)
                    VALUES (?, ?, ?)
                    """,
                    (self._day_key(day), slot, datetime.utcnow().isoformat(timespec="seconds")),
                )
                conn.commit()
            except sqlite3.Error as exc:
                logger.error("Ошибка записи слота в БД: %s", exc)

    def count_executed_slots_for_day(self, day: date | str) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM daily_slots WHERE day = ?",
                (self._day_key(day),),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0

    # -------- promo_posts --------

    def promo_keys_posted_for_day(self, day: date | str) -> set[str]:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "SELECT promo_key FROM promo_posts WHERE day = ?",
                (self._day_key(day),),
            )
            return {str(row[0]) for row in cur.fetchall()}

    def mark_promo_posted(
        self,
        day: date | str,
        promo_key: str,
        message_id: int | None = None,
    ) -> None:
        with self._lock, self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO promo_posts
                    (day, promo_key, message_id, posted_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        self._day_key(day),
                        promo_key,
                        message_id,
                        datetime.utcnow().isoformat(timespec="seconds"),
                    ),
                )
                conn.commit()
            except sqlite3.Error as exc:
                logger.error("Ошибка записи промо в БД: %s", exc)
