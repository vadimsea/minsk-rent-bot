"""
Публикация в Telegram через Bot API.

Стратегия отправки:
1) Если есть image_url — отправляем sendPhoto по URL.
2) Если Telegram отказался скачивать картинку — скачиваем сами и шлём файлом.
3) Если и это не получилось — отправляем обычным sendMessage с текстом.

Если caption длиннее 1024 символов — formatter сам подсказал короткую версию.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
from typing import Any, Dict, Optional, Tuple

import requests

from config import CONFIG
from fetcher import fetch_bytes
from formatter import (
    TELEGRAM_CAPTION_LIMIT,
    TELEGRAM_MESSAGE_LIMIT,
    format_for_telegram,
)
from promo import PromoPost


logger = logging.getLogger("telegram")


TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramPublisher:
    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        dry_run: Optional[bool] = None,
    ) -> None:
        self.bot_token = bot_token or CONFIG.telegram_bot_token
        self.chat_id = chat_id or CONFIG.telegram_channel_id
        self.dry_run = CONFIG.dry_run if dry_run is None else dry_run

    # ---------- DRY RUN ----------

    def _dry_run_publish(self, listing: Dict[str, Any]) -> bool:
        text = format_for_telegram(listing, has_photo=bool(listing.get("image_url")))
        logger.info("=" * 60)
        logger.info("[DRY_RUN] %s", listing.get("source"))
        logger.info("URL: %s", listing.get("url"))
        logger.info("Image: %s", listing.get("image_url"))
        logger.info("Текст поста:\n%s", text)
        logger.info("=" * 60)
        return True

    # ---------- реальная публикация ----------

    def publish(self, listing: Dict[str, Any]) -> bool:
        if self.dry_run:
            return self._dry_run_publish(listing)

        if not self.bot_token or not self.chat_id:
            logger.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL_ID не заданы — публикация невозможна")
            return False

        image_url = listing.get("image_url")
        has_photo = isinstance(image_url, str) and image_url.startswith("http")

        text = format_for_telegram(listing, has_photo=has_photo)

        if has_photo:
            ok = self._send_photo_by_url(image_url, text)
            if ok:
                logger.info("Опубликовано (photo by url): %s", listing.get("url"))
                return True

            ok = self._send_photo_uploaded(image_url, text)
            if ok:
                logger.info("Опубликовано (photo uploaded): %s", listing.get("url"))
                return True

            # фото не отправилось — шлём текст
            logger.warning("Фото не отправилось, шлём текстом: %s", listing.get("url"))
            text_only = format_for_telegram(listing, has_photo=False)
            return self._send_message(text_only)

        return self._send_message(text)

    def publish_promo(self, promo: PromoPost) -> Optional[int]:
        if self.dry_run:
            logger.info("=" * 60)
            logger.info("[DRY_RUN] promo %s", promo.key)
            logger.info("Текст промо:\n%s", promo.text)
            logger.info("Кнопки: %s", [(button.text, button.url) for button in promo.buttons])
            logger.info("=" * 60)
            return 0

        if not self.bot_token or not self.chat_id:
            logger.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL_ID не заданы — публикация невозможна")
            return None

        keyboard = {
            "inline_keyboard": [
                [{"text": button.text, "url": button.url}]
                for button in promo.buttons
            ]
        }
        return self._send_message_with_keyboard(promo.text, keyboard)

    # ---------- низкоуровневые запросы ----------

    def _api(self, method: str) -> str:
        return TELEGRAM_API.format(token=self.bot_token, method=method)

    def _send_photo_by_url(self, photo_url: str, caption: str) -> bool:
        try:
            response = requests.post(
                self._api("sendPhoto"),
                data={
                    "chat_id": self.chat_id,
                    "photo": photo_url,
                    "caption": caption[:TELEGRAM_CAPTION_LIMIT],
                    "parse_mode": "HTML",
                },
                timeout=CONFIG.request_timeout,
            )
        except requests.RequestException as exc:
            logger.warning("sendPhoto(url) сетевая ошибка: %s", exc)
            return False

        if response.status_code == 200:
            return True
        logger.warning("sendPhoto(url) %s: %s", response.status_code, response.text[:300])
        return False

    def _send_photo_uploaded(self, photo_url: str, caption: str) -> bool:
        data = fetch_bytes(photo_url, check_robots=False)
        if not data:
            return False

        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".jpg", prefix="rent_photo_")
            with os.fdopen(fd, "wb") as f:
                f.write(data)

            with open(tmp_path, "rb") as f:
                response = requests.post(
                    self._api("sendPhoto"),
                    data={
                        "chat_id": self.chat_id,
                        "caption": caption[:TELEGRAM_CAPTION_LIMIT],
                        "parse_mode": "HTML",
                    },
                    files={"photo": ("photo.jpg", f, "image/jpeg")},
                    timeout=CONFIG.request_timeout * 2,
                )

            if response.status_code == 200:
                return True
            logger.warning("sendPhoto(upload) %s: %s", response.status_code, response.text[:300])
            return False
        except (OSError, requests.RequestException) as exc:
            logger.warning("sendPhoto(upload) ошибка: %s", exc)
            return False
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def _send_message(self, text: str) -> bool:
        try:
            response = requests.post(
                self._api("sendMessage"),
                data={
                    "chat_id": self.chat_id,
                    "text": text[:TELEGRAM_MESSAGE_LIMIT],
                    "parse_mode": "HTML",
                    "disable_web_page_preview": "false",
                },
                timeout=CONFIG.request_timeout,
            )
        except requests.RequestException as exc:
            logger.warning("sendMessage сетевая ошибка: %s", exc)
            return False

        if response.status_code == 200:
            return True
        logger.warning("sendMessage %s: %s", response.status_code, response.text[:300])
        return False

    def _send_message_with_keyboard(self, text: str, reply_markup: dict) -> Optional[int]:
        try:
            response = requests.post(
                self._api("sendMessage"),
                json={
                    "chat_id": self.chat_id,
                    "text": text[:TELEGRAM_MESSAGE_LIMIT],
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "reply_markup": reply_markup,
                },
                timeout=CONFIG.request_timeout,
            )
        except requests.RequestException as exc:
            logger.warning("sendMessage promo сетевая ошибка: %s", exc)
            return None

        if response.status_code == 200:
            data = response.json()
            return int((data.get("result") or {}).get("message_id") or 0)
        logger.warning("sendMessage promo %s: %s", response.status_code, response.text[:300])
        return None
