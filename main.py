"""
Точка входа агрегатора объявлений.

Цикл работы:
1) Загружаем настройки и источники.
2) Для каждого включённого источника запускаем парсер.
3) Нормализуем объявления.
4) Прогоняем через фильтры (включая проверку дублей).
5) Берём первые POSTS_PER_RUN новых объявлений.
6) Формируем красивый пост и публикуем в Telegram (или dry-run).
7) Сохраняем ссылку в БД.
8) Логируем подробную статистику.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List

from config import CONFIG, setup_logging
from filters import filter_listings
from formatter import explain_pass_reason
from normalizer import normalize_listing
from parsers import get_parser
from scheduler import _parse_hhmm, _tz, generate_daily_slots, run_scheduled
from sources import Source, get_enabled_sources
from storage import Storage
from telegram_publisher import TelegramPublisher


logger = logging.getLogger("main")


def collect_from_source(source: Source) -> List[Dict[str, Any]]:
    parser_cls = get_parser(source.parser_name)
    if parser_cls is None:
        logger.warning("Парсер %s не зарегистрирован", source.parser_name)
        return []

    parser = parser_cls(
        list_url=source.list_url,
        category=source.category,
        rent_period=source.rent_period,
        city=source.city,
        base_url=source.base_url,
    )

    logger.info("Источник: %s | %s | %s", source.name, source.category, source.list_url)
    raw = parser.safe_parse()

    normalized: List[Dict[str, Any]] = []
    for item in raw:
        # Перебиваем категорию из источника — она надёжнее, чем эвристики
        item.setdefault("listing_type", source.category)
        item.setdefault("rent_period", source.rent_period)
        item.setdefault("source", source.name)
        try:
            normalized.append(normalize_listing(item))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Нормализация упала: %s", exc)
    return normalized


def collect_all() -> List[Dict[str, Any]]:
    enabled = get_enabled_sources()
    if not enabled:
        logger.warning("Нет включённых источников")
        return []

    all_items: List[Dict[str, Any]] = []
    for source in enabled:
        try:
            items = collect_from_source(source)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Источник %s упал: %s", source.name, exc)
            items = []
        all_items.extend(items)

    logger.info("Всего собрано объявлений со всех источников: %s", len(all_items))
    return all_items


def deduplicate_in_batch(listings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Уберём дубли по URL внутри одного запуска (бывает, что один и тот же объект на двух источниках)."""
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for l in listings:
        url = l.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(l)
    return out


def run_once(posts_count: int | None = None) -> int:
    """
    Один прогон пайплайна.
    posts_count — сколько объявлений опубликовать максимум за этот прогон.
    Если None — берём CONFIG.posts_per_run.
    Возвращает число фактически опубликованных постов.
    """
    storage = Storage()
    publisher = TelegramPublisher()

    target_count = posts_count if posts_count is not None else CONFIG.posts_per_run

    logger.info("=" * 60)
    logger.info("Старт прогона. DRY_RUN=%s, цель=%s пост(ов)", CONFIG.dry_run, target_count)
    logger.info("Включённые источники: %s", [s.name + "/" + s.category for s in get_enabled_sources()])
    logger.info("=" * 60)

    raw_listings = collect_all()
    raw_listings = deduplicate_in_batch(raw_listings)

    passed, stats = filter_listings(raw_listings, storage=storage)

    logger.info(
        "Статистика фильтров: total=%s, passed=%s, "
        "missing_fields=%s, not_rent=%s, not_long_term=%s, "
        "not_allowed_type=%s, not_apartment_or_room=%s, "
        "not_minsk=%s, no_price=%s, no_photo=%s, "
        "duplicate=%s, suspicious=%s",
        stats.get("total", 0), stats.get("passed", 0),
        stats.get("missing_fields", 0), stats.get("not_rent", 0), stats.get("not_long_term", 0),
        stats.get("not_allowed_type", 0), stats.get("not_apartment_or_room", 0),
        stats.get("not_minsk", 0), stats.get("no_price", 0), stats.get("no_photo", 0),
        stats.get("duplicate", 0), stats.get("suspicious", 0),
    )

    if not passed:
        logger.info("Новых подходящих объявлений нет. Завершаем.")
        return 0

    to_publish = passed[: max(0, target_count)]
    logger.info("К публикации: %s из %s", len(to_publish), len(passed))

    published_count = 0
    for idx, listing in enumerate(to_publish, start=1):
        logger.info("→ %s/%s: %s | %s", idx, len(to_publish),
                    listing.get("source"), listing.get("url"))
        logger.info("   причина прохождения фильтров: %s", explain_pass_reason(listing))

        ok = publisher.publish(listing)
        if ok:
            storage.mark_as_published(listing)
            published_count += 1
        else:
            logger.warning("Не удалось опубликовать: %s", listing.get("url"))

        # Пауза между постами (кроме DRY_RUN)
        if not CONFIG.dry_run and idx < len(to_publish):
            time.sleep(max(0, CONFIG.post_interval_seconds))

    logger.info("Опубликовано: %s. Всего в БД: %s", published_count, storage.count())
    return published_count


def run_cron_tick(slot_grace_minutes: int = 30) -> int:
    """
    Один «тик» для запуска по cron (например, GitHub Actions каждые 5 минут).

    Логика:
    - детерминированно генерируем POSTS_PER_DAY слотов на сегодня (seed=date);
    - смотрим, есть ли слот, на который сейчас «попадаем»:
        — слот должен быть в прошлом не более чем slot_grace_minutes минут назад
          (это покрывает задержки GitHub Actions);
        — слот не должен быть уже отмечен как выполненный сегодня;
    - если такой слот есть — публикуем ровно одно объявление и отмечаем слот.

    Один тик публикует не более одного поста.
    """
    storage = Storage()

    tz = _tz()
    now = datetime.now(tz=tz) if tz else datetime.now()
    today = now.date()

    start = _parse_hhmm(CONFIG.post_window_start, (10, 0))
    end = _parse_hhmm(CONFIG.post_window_end, (20, 0))
    slots = generate_daily_slots(today, CONFIG.posts_per_day, start, end)

    if not slots:
        logger.info("Нет слотов на сегодня (%s). Выход.", today.isoformat())
        return 0

    slots_today_executed = storage.count_executed_slots_for_day(today)
    logger.info(
        "cron-tick: %s | сегодня уже отработано слотов: %s / %s",
        now.strftime("%Y-%m-%d %H:%M"),
        slots_today_executed,
        len(slots),
    )

    matched_slot = None
    for slot in slots:
        slot_dt = slot.replace(tzinfo=tz) if tz else slot
        delta = (now - slot_dt).total_seconds()
        if delta < 0:
            # этот слот ещё в будущем — пропускаем, проверим в следующий тик
            continue
        if delta > slot_grace_minutes * 60:
            # слот был слишком давно, считаем «потерянным» (запишем как сделанный,
            # чтобы не зацикливаться)
            slot_key = slot.strftime("%H:%M")
            if not storage.is_slot_executed(today, slot_key):
                logger.warning(
                    "Слот %s пропущен (был %d мин назад). Помечаем как отработанный.",
                    slot_key, int(delta / 60),
                )
                storage.mark_slot_executed(today, slot_key)
            continue

        slot_key = slot.strftime("%H:%M")
        if storage.is_slot_executed(today, slot_key):
            continue

        matched_slot = (slot, slot_key)
        break

    if not matched_slot:
        logger.info(
            "Ни один слот не подходит. Слоты сегодня: %s",
            ", ".join(s.strftime("%H:%M") for s in slots),
        )
        return 0

    slot, slot_key = matched_slot
    logger.info("Слот %s попал — пытаемся опубликовать 1 объявление", slot_key)

    published = run_once(posts_count=1)
    if published > 0:
        storage.mark_slot_executed(today, slot_key)
        logger.info("Слот %s отмечен как отработанный", slot_key)
    else:
        # Не удалось опубликовать (нечего публиковать / телеграм отказал и т.п.).
        # Слот НЕ помечаем выполненным — попробуем в следующий тик в рамках grace.
        logger.warning("Слот %s: ничего не опубликовано, попробуем в следующий тик", slot_key)

    return published


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Агрегатор объявлений аренды в Минске")
    parser.add_argument(
        "--once", action="store_true",
        help="Игнорировать SCHEDULE_MODE и сделать один прогон (опубликовать до POSTS_PER_RUN постов)",
    )
    parser.add_argument(
        "--scheduled", action="store_true",
        help="Запустить долгоживущий процесс по расписанию из .env (SCHEDULE_MODE)",
    )
    parser.add_argument(
        "--cron-tick", action="store_true", dest="cron_tick",
        help="Один тик для запуска по внешнему cron / GitHub Actions: "
             "сам решит, попадаем ли в один из POSTS_PER_DAY случайных слотов в окне",
    )
    parser.add_argument(
        "--grace-minutes", type=int, default=30,
        help="Допуск опоздания слота для --cron-tick (мин). По умолчанию 30.",
    )
    return parser.parse_args()


def main() -> int:
    setup_logging()
    args = parse_cli_args()

    if args.cron_tick:
        run_cron_tick(slot_grace_minutes=args.grace_minutes)
    elif args.scheduled and not args.once:
        run_scheduled(run_once)
    else:
        run_once()
    return 0


if __name__ == "__main__":
    sys.exit(main())
