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
from collections import Counter, OrderedDict
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


def pick_round_robin(items: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    """
    Берём первые n объявлений по схеме round-robin между источниками.

    Зачем: `collect_all()` собирает источники в порядке приоритета и просто
    конкатенирует списки. У Realt объявлений в разы больше, чем у других,
    поэтому первые N штук в `passed` — всегда с Realt, и канал получался
    «однопотоковый». Здесь мы по очереди берём по одному с Kufar, Hata,
    Domovita, Realt и т.д., чтобы лента была разноисточниковой.

    Внутри каждой группы порядок сохраняется (он определяется выдачей сайта,
    как правило — по свежести), поэтому свежие листинги по-прежнему
    идут первыми.
    """
    if n <= 0 or not items:
        return []

    groups: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for it in items:
        src = it.get("source") or "unknown"
        groups.setdefault(src, []).append(it)

    picked: List[Dict[str, Any]] = []
    while len(picked) < n:
        took_any = False
        for src in list(groups.keys()):
            bucket = groups[src]
            if not bucket:
                continue
            picked.append(bucket.pop(0))
            took_any = True
            if len(picked) >= n:
                break
        if not took_any:
            break
    return picked


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

    sources_in_passed = Counter((l.get("source") or "?") for l in passed)
    logger.info(
        "Распределение прошедших по источникам: %s",
        ", ".join(f"{s}={c}" for s, c in sources_in_passed.most_common()),
    )

    to_publish = pick_round_robin(passed, max(0, target_count))
    sources_in_pick = Counter((l.get("source") or "?") for l in to_publish)
    logger.info(
        "К публикации: %s из %s (по источникам: %s)",
        len(to_publish), len(passed),
        ", ".join(f"{s}={c}" for s, c in sources_in_pick.most_common()),
    )

    published_count = 0
    for idx, listing in enumerate(to_publish, start=1):
        logger.info("→ %s/%s: %s | %s", idx, len(to_publish),
                    listing.get("source"), listing.get("url"))
        logger.info("   причина прохождения фильтров: %s", explain_pass_reason(listing))

        ok = publisher.publish(listing)
        if ok:
            published_count += 1
            # В DRY_RUN ничего реально не публиковалось — БД не трогаем,
            # иначе при первом боевом прогоне всё будет пропущено как «дубль».
            if not CONFIG.dry_run:
                storage.mark_as_published(listing)
        else:
            logger.warning("Не удалось опубликовать: %s", listing.get("url"))

        # Пауза между постами (кроме DRY_RUN)
        if not CONFIG.dry_run and idx < len(to_publish):
            time.sleep(max(0, CONFIG.post_interval_seconds))

    logger.info("Опубликовано: %s. Всего в БД: %s", published_count, storage.count())
    return published_count


def run_cron_tick(slot_grace_minutes: int = 30, max_catch_up: int = 5) -> int:
    """
    Один «тик» для запуска по cron (например, GitHub Actions каждые 5 минут).

    Логика «догоняем пропущенные слоты»:
    - детерминированно генерируем POSTS_PER_DAY слотов на сегодня (seed=date);
    - собираем все ещё не отработанные слоты, время которых уже наступило
      (due-слоты), отсортированные от самого раннего;
    - публикуем до `max_catch_up` объявлений за один тик — по одному в счёт
      каждого due-слота, начиная с самого раннего;
    - сколько удалось опубликовать — столько же самых ранних due-слотов
      помечаем как отработанные. Остальные оставляем — попробуем на
      следующем тике.

    Зачем «догонять» сразу несколько слотов в одном тике:
    GitHub Actions free-tier на «*/5 * * * *» сильно лагает и иногда не
    запускает тики часами. Если за полдня накопилось 4 просроченных слота,
    а cron наконец-то стрельнул — нам нужно за этот тик добить все 4, а не
    один. Иначе следующего тика можно ждать ещё несколько часов.

    Чтобы не вылететь по 10-мин таймауту GH Actions и не спамить телегу
    залпом — ограничиваем количество catch-up публикаций за один тик
    параметром `max_catch_up` (по умолчанию 5).

    Параметр slot_grace_minutes оставлен для обратной совместимости CLI,
    но больше не используется для отбраковки слотов.
    """
    _ = slot_grace_minutes  # legacy

    max_catch_up = max(1, int(max_catch_up))

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

    executed_today = storage.count_executed_slots_for_day(today)
    logger.info(
        "cron-tick: %s | отработано слотов сегодня: %s / %s",
        now.strftime("%Y-%m-%d %H:%M"),
        executed_today,
        len(slots),
    )

    # Собираем все due-неотработанные слоты (отсортированы по возрастанию).
    # slots уже отсортированы — поэтому идём по порядку.
    due_slots: List[tuple[datetime, str]] = []
    next_future_key: str | None = None
    for slot in slots:
        slot_dt = slot.replace(tzinfo=tz) if tz else slot
        if slot_dt > now:
            next_future_key = slot.strftime("%H:%M")
            break
        slot_key = slot.strftime("%H:%M")
        if storage.is_slot_executed(today, slot_key):
            continue
        due_slots.append((slot_dt, slot_key))

    if not due_slots:
        if next_future_key:
            logger.info(
                "Все наступившие слоты уже отработаны. Следующий слот: %s",
                next_future_key,
            )
        else:
            logger.info(
                "Все слоты сегодня отработаны: %s",
                ", ".join(s.strftime("%H:%M") for s in slots),
            )
        return 0

    to_publish_n = min(len(due_slots), max_catch_up)

    if to_publish_n == 1:
        s_dt, s_key = due_slots[0]
        delta_min = int((now - s_dt).total_seconds() // 60)
        if delta_min <= 1:
            logger.info("Слот %s наступил — публикуем 1 объявление", s_key)
        else:
            logger.info(
                "Догоняем слот %s (опоздание %s мин) — публикуем 1 объявление",
                s_key, delta_min,
            )
    else:
        oldest_delta = int((now - due_slots[0][0]).total_seconds() // 60)
        logger.info(
            "Догоняем %s слот(а/ов) одним тиком (самый старый опоздал на %s мин): %s",
            to_publish_n, oldest_delta,
            ", ".join(s[1] for s in due_slots[:to_publish_n]),
        )
        if len(due_slots) > to_publish_n:
            logger.info(
                "Ещё %s слот(а/ов) останется на следующий тик: %s",
                len(due_slots) - to_publish_n,
                ", ".join(s[1] for s in due_slots[to_publish_n:]),
            )

    published = run_once(posts_count=to_publish_n)

    # Помечаем первые `published` due-слот(а/ов) — самые ранние.
    for i in range(min(published, to_publish_n)):
        slot_key = due_slots[i][1]
        storage.mark_slot_executed(today, slot_key)
        logger.info("Слот %s отмечен как отработанный", slot_key)

    if published < to_publish_n:
        logger.warning(
            "Запросили %s публикаций, реально опубликовано %s — "
            "оставшиеся слоты попробуем на следующем тике",
            to_publish_n, published,
        )

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
             "догоняет самые ранние ещё не отработанные слоты из POSTS_PER_DAY "
             "случайных слотов в окне. До --max-catch-up публикаций за тик.",
    )
    parser.add_argument(
        "--grace-minutes", type=int, default=30,
        help="Legacy-параметр, оставлен для обратной совместимости. "
             "Больше не используется: --cron-tick не выбрасывает «просроченные» слоты.",
    )
    parser.add_argument(
        "--max-catch-up", type=int, default=5, dest="max_catch_up",
        help="Сколько просроченных слотов догонять за один --cron-tick. "
             "По умолчанию 5. Защищает от спама в телегу и от 10-мин таймаута CI, "
             "и одновременно позволяет одному редкому тику закрыть несколько слотов сразу "
             "(полезно при ненадёжном GitHub Actions cron).",
    )
    return parser.parse_args()


def main() -> int:
    setup_logging()
    args = parse_cli_args()

    if args.cron_tick:
        run_cron_tick(
            slot_grace_minutes=args.grace_minutes,
            max_catch_up=args.max_catch_up,
        )
    elif args.scheduled and not args.once:
        run_scheduled(run_once)
    else:
        run_once()
    return 0


if __name__ == "__main__":
    sys.exit(main())
