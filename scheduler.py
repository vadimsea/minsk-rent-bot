"""
Запуск по расписанию через APScheduler.

Поддерживаемые режимы (SCHEDULE_MODE в .env):
- once         — один прогон и выход;
- interval     — раз в RUN_EVERY_HOURS;
- daily        — каждый день в POST_TIME (HH:MM) по TIMEZONE;
- random_daily — публиковать POSTS_PER_DAY постов в день в случайные моменты
                 внутри окна [POST_WINDOW_START, POST_WINDOW_END]. Расписание
                 на каждый день детерминированно от даты (seed = date),
                 поэтому при перезапуске процесса бот возьмёт ровно то же
                 расписание и не задвоит и не потеряет посты.
"""

from __future__ import annotations

import logging
import random
import signal
from datetime import date, datetime, time, timedelta
from typing import Callable, List

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

from config import CONFIG


logger = logging.getLogger("scheduler")


def _tz():
    name = CONFIG.timezone or "Europe/Minsk"
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось создать таймзону %s, использую системную", name)
        return None


def _parse_hhmm(value: str, default: tuple[int, int]) -> tuple[int, int]:
    try:
        h, m = value.split(":")
        return int(h), int(m)
    except (ValueError, AttributeError):
        logger.warning("Не смог распарсить время %r, использую %02d:%02d", value, *default)
        return default


def generate_daily_slots(
    day: date,
    count: int,
    window_start: tuple[int, int],
    window_end: tuple[int, int],
) -> List[datetime]:
    """
    Возвращает count случайных datetime в окне [window_start, window_end] на дату day.
    Случайность детерминированная: одно и то же расписание для одной и той же даты.
    Минуты не повторяются, разнесены минимум на 1 минуту друг от друга.
    """
    start_minutes = window_start[0] * 60 + window_start[1]
    end_minutes = window_end[0] * 60 + window_end[1]
    if end_minutes <= start_minutes:
        logger.warning("POST_WINDOW_END <= POST_WINDOW_START, использую дефолт 10:00-20:00")
        start_minutes, end_minutes = 10 * 60, 20 * 60

    available = end_minutes - start_minutes
    n = min(max(0, count), available)

    seed_str = f"{day.isoformat()}|{n}|{start_minutes}|{end_minutes}"
    rng = random.Random(seed_str)

    chosen = sorted(rng.sample(range(start_minutes, end_minutes), n))

    return [
        datetime.combine(day, time(hour=m // 60, minute=m % 60))
        for m in chosen
    ]


def _slot_job_wrapper(job: Callable[[int | None], int]) -> Callable[[], None]:
    """APScheduler требует callable без аргументов; передаём 1 пост на слот."""
    def _run() -> None:
        try:
            job(1)  # просим run_once опубликовать ровно 1 объявление
        except Exception as exc:  # noqa: BLE001
            logger.exception("Слот рухнул: %s", exc)
    return _run


def _schedule_random_day(
    scheduler: BlockingScheduler,
    job: Callable[[int | None], int],
    target_day: date,
    tz,
) -> int:
    """
    Регистрирует jobs на дату target_day. Возвращает число запланированных слотов
    (только тех, что в будущем относительно «сейчас»).
    """
    start = _parse_hhmm(CONFIG.post_window_start, (10, 0))
    end = _parse_hhmm(CONFIG.post_window_end, (20, 0))
    slots = generate_daily_slots(target_day, CONFIG.posts_per_day, start, end)

    now = datetime.now(tz=tz) if tz else datetime.now()

    scheduled = 0
    skipped = 0
    runner = _slot_job_wrapper(job)
    for slot in slots:
        slot_dt = slot.replace(tzinfo=tz) if tz else slot
        if slot_dt <= now:
            skipped += 1
            continue
        scheduler.add_job(
            runner,
            trigger=DateTrigger(run_date=slot_dt, timezone=tz),
            id=f"slot_{slot_dt.isoformat()}",
            replace_existing=True,
            misfire_grace_time=600,  # если процесс был занят, нагнать в течение 10 мин
        )
        scheduled += 1

    if scheduled or skipped:
        logger.info(
            "День %s: запланировано %s, пропущено (уже в прошлом) %s, всего слотов %s",
            target_day.isoformat(), scheduled, skipped, len(slots),
        )
    if slots:
        logger.info(
            "Слоты на %s: %s",
            target_day.isoformat(),
            ", ".join(s.strftime("%H:%M") for s in slots),
        )
    return scheduled


def run_scheduled(job: Callable[..., int | None]) -> None:
    """
    Запускает job согласно настройкам.
    job вызывается так:
        job() либо job(posts_count=1) — в зависимости от режима.
    """
    mode = (CONFIG.schedule_mode or "interval").lower()

    if mode == "once":
        logger.info("SCHEDULE_MODE=once — один прогон")
        job()
        return

    tz = _tz()
    scheduler = BlockingScheduler(timezone=CONFIG.timezone or "Europe/Minsk")

    if mode == "interval":
        hours = max(1, CONFIG.run_every_hours)
        logger.info("SCHEDULE_MODE=interval — запуск каждые %s ч.", hours)
        scheduler.add_job(
            job,
            trigger=IntervalTrigger(hours=hours),
            id="rent_job_interval",
        )
        logger.info("Стартовый прогон…")
        try:
            job()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Стартовый прогон упал: %s", exc)

    elif mode == "daily":
        hour, minute = _parse_hhmm(CONFIG.post_time, (10, 0))
        logger.info("SCHEDULE_MODE=daily — каждый день в %02d:%02d (%s)", hour, minute, CONFIG.timezone)
        scheduler.add_job(
            job,
            trigger=CronTrigger(hour=hour, minute=minute),
            id="rent_job_daily",
        )

    elif mode == "random_daily":
        logger.info(
            "SCHEDULE_MODE=random_daily — %s пост(ов) в день в окне %s..%s (%s)",
            CONFIG.posts_per_day, CONFIG.post_window_start, CONFIG.post_window_end, CONFIG.timezone,
        )

        # Расписание на сегодня — сразу
        today = datetime.now(tz=tz).date() if tz else datetime.now().date()
        _schedule_random_day(scheduler, job, today, tz)

        # И заранее планируем на завтра, чтобы между «полночь» и «00:01»
        # не было дыр
        tomorrow = today + timedelta(days=1)
        _schedule_random_day(scheduler, job, tomorrow, tz)

        # Каждый день в 00:01 — генерируем расписание для следующего дня
        def _replan_tomorrow() -> None:
            try:
                next_day = (
                    datetime.now(tz=tz).date() if tz else datetime.now().date()
                ) + timedelta(days=1)
                _schedule_random_day(scheduler, job, next_day, tz)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Перепланирование на следующий день упало: %s", exc)

        scheduler.add_job(
            _replan_tomorrow,
            trigger=CronTrigger(hour=0, minute=1),
            id="replan_tomorrow",
            replace_existing=True,
        )

    else:
        logger.error("Неизвестный SCHEDULE_MODE=%s, выполняем один раз", mode)
        job()
        return

    def _graceful_shutdown(signum, frame):  # noqa: ARG001
        logger.info("Получен сигнал %s, останавливаемся…", signum)
        scheduler.shutdown(wait=False)

    try:
        signal.signal(signal.SIGINT, _graceful_shutdown)
        signal.signal(signal.SIGTERM, _graceful_shutdown)
    except (ValueError, AttributeError):
        # На Windows / в подпотоках регистрация может упасть — это нормально
        pass

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановка по запросу пользователя")
