"""
HTTP-клиент с базовой защитой:
- User-Agent;
- таймауты;
- ретраи с экспоненциальной задержкой;
- паузы между запросами (вежливый бот);
- уважение robots.txt (проверка перед запросом).

Если источник недоступен — возвращаем None, остальной пайплайн продолжает работу.
"""

from __future__ import annotations

import logging
import time
import urllib.robotparser
from typing import Dict, Optional
from urllib.parse import urlparse

import requests

from config import CONFIG


logger = logging.getLogger("fetcher")

# Кэш парсеров robots.txt по хостам
_robots_cache: Dict[str, urllib.robotparser.RobotFileParser] = {}

# Время последнего запроса к каждому хосту — чтобы выдерживать паузу
_last_request_at: Dict[str, float] = {}


def _get_robot_parser(url: str) -> Optional[urllib.robotparser.RobotFileParser]:
    parsed = urlparse(url)
    host = f"{parsed.scheme}://{parsed.netloc}"
    if host in _robots_cache:
        return _robots_cache[host]

    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(f"{host}/robots.txt")
    try:
        rp.read()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось прочитать robots.txt для %s: %s", host, exc)
        return None
    _robots_cache[host] = rp
    return rp


def is_allowed_by_robots(url: str, user_agent: str | None = None) -> bool:
    """Если robots.txt прямо запрещает — не ходим. Если непонятно — разрешаем."""
    ua = user_agent or CONFIG.user_agent
    rp = _get_robot_parser(url)
    if rp is None:
        return True
    try:
        return rp.can_fetch(ua, url)
    except Exception:  # noqa: BLE001
        return True


def _respect_rate_limit(url: str) -> None:
    """Гарантирует паузу между запросами к одному и тому же хосту."""
    host = urlparse(url).netloc
    delay = max(CONFIG.request_delay_seconds, 0.0)
    last = _last_request_at.get(host)
    now = time.monotonic()
    if last is not None:
        wait = (last + delay) - now
        if wait > 0:
            time.sleep(wait)
    _last_request_at[host] = time.monotonic()


def fetch(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
    allow_redirects: bool = True,
    check_robots: bool = True,
) -> Optional[requests.Response]:
    """
    Делает HTTP-запрос с ретраями. Если все попытки провалились,
    возвращает None — мы не падаем, просто пропускаем источник.
    """
    if check_robots and not is_allowed_by_robots(url):
        logger.warning("robots.txt запрещает обход: %s", url)
        return None

    request_headers = {
        "User-Agent": CONFIG.user_agent,
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    if headers:
        request_headers.update(headers)

    timeout_val = timeout or CONFIG.request_timeout
    max_retries = max(1, CONFIG.request_max_retries)

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        _respect_rate_limit(url)
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=request_headers,
                params=params,
                timeout=timeout_val,
                allow_redirects=allow_redirects,
            )

            # 429/5xx — повторяем
            if response.status_code == 429 or 500 <= response.status_code < 600:
                logger.warning(
                    "HTTP %s от %s (попытка %s/%s)",
                    response.status_code, url, attempt, max_retries,
                )
                time.sleep(min(2 ** attempt, 30))
                continue

            if response.status_code >= 400:
                logger.warning("HTTP %s от %s, выходим", response.status_code, url)
                return None

            return response

        except requests.RequestException as exc:
            last_exc = exc
            logger.warning(
                "Ошибка запроса %s (попытка %s/%s): %s",
                url, attempt, max_retries, exc,
            )
            time.sleep(min(2 ** attempt, 30))

    if last_exc:
        logger.error("Не удалось получить %s после %s попыток: %s", url, max_retries, last_exc)
    return None


def fetch_text(url: str, **kwargs) -> Optional[str]:
    response = fetch(url, **kwargs)
    if response is None:
        return None
    return response.text


def fetch_bytes(url: str, **kwargs) -> Optional[bytes]:
    """Используется для скачивания одной фотографии перед отправкой в Telegram."""
    response = fetch(url, **kwargs)
    if response is None:
        return None
    return response.content
