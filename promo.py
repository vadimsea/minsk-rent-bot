from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional, Sequence

SITE_URL = "https://vadzim.by"
ATEN_URL = "https://vadzim.by/aten/"
REMOTE_JOBS_URL = "https://t.me/remote_belarus"
CHANNEL_URL = "https://t.me/vadzimby_live"
PROGRAMMER_BOT_URL = "https://t.me/vadzim_by_programmer_bot"

PROMO_WINDOW = timedelta(minutes=35)
PROMO_INTERVAL_START = date(2026, 7, 1)


@dataclass(frozen=True)
class PromoButton:
    text: str
    url: str


@dataclass(frozen=True)
class PromoPost:
    key: str
    text: str
    buttons: tuple[PromoButton, ...]


@dataclass(frozen=True)
class PromoCampaign:
    key: str
    text: str
    button_text: str
    url: str
    at: time
    weekdays: Optional[set[int]] = None
    interval_days: Optional[int] = None

    def is_due(self, current: datetime) -> bool:
        if self.weekdays is not None and current.weekday() not in self.weekdays:
            return False
        if self.interval_days is not None:
            days_since_start = (current.date() - PROMO_INTERVAL_START).days
            if days_since_start < 0 or days_since_start % self.interval_days != 0:
                return False
        start = datetime.combine(current.date(), self.at, tzinfo=current.tzinfo)
        return start <= current <= start + PROMO_WINDOW

    def build_post(self) -> PromoPost:
        return PromoPost(
            key=self.key,
            text=self.text,
            buttons=(PromoButton(self.button_text, self.url),),
        )


def build_campaigns(
    *,
    site_url: str = SITE_URL,
    channel_url: str = CHANNEL_URL,
) -> tuple[PromoCampaign, ...]:
    site_url = site_url.rstrip("/")
    channel_url = channel_url.strip() or CHANNEL_URL

    return (
        PromoCampaign(
            key="services_daily",
            at=time(10, 5),
            url=site_url,
            button_text="Услуги vadzim.by",
            text="""<b>Сайт и digital для бизнеса</b>

Бизнесу нужен не просто сайт, а инструмент, который приводит заявки.
На vadzim.by можно заказать лендинг, корпоративный сайт, интернет-магазин, SEO, CRM-интеграции и Telegram-бота с ИИ.""",
        ),
        PromoCampaign(
            key="aten_daily_1",
            at=time(12, 30),
            url=ATEN_URL,
            button_text="Открыть ATEN",
            text="""<b>ATEN / Атон</b>

ATEN создан для общения со своим кругом: друзья, рабочие группы, сообщества и каналы.
Простой интерфейс, фокус на переписке и веб-вход без установки.""",
        ),
        PromoCampaign(
            key="aten_daily_2",
            at=time(18, 30),
            url=ATEN_URL,
            button_text="Попробовать ATEN",
            text="""<b>ATEN / Атон</b>

Нужен мессенджер без бесконечной ленты и отвлечений?
ATEN помогает общаться по делу: личные чаты, группы, каналы и понятный веб-вход без установки.""",
        ),
        PromoCampaign(
            key="remote_belarus_weekly",
            at=time(11, 30),
            weekdays={6},
            url=REMOTE_JOBS_URL,
            button_text="Удалённая работа в Беларуси",
            text="""<b>Удалённая работа в Беларуси</b>

Хотите следить за удалёнными вакансиями в Беларуси?
Загляните в канал с подборками работы для специалистов:""",
        ),
        PromoCampaign(
            key="vadzimby_live_tue",
            at=time(16, 30),
            weekdays={1},
            url=channel_url,
            button_text="ИИ, дизайн и маркетинг",
            text="""<b>ИИ, маркетинг и дизайн</b>

Хотите следить за ИИ, дизайном, маркетингом и полезными digital-находками?
Заходите в группу vadzim.by live:""",
        ),
        PromoCampaign(
            key="vadzimby_live_fri",
            at=time(16, 30),
            weekdays={4},
            url=channel_url,
            button_text="Перейти в vadzim.by live",
            text="""<b>ИИ, маркетинг и дизайн</b>

Хотите следить за ИИ, дизайном, маркетингом и полезными digital-находками?
Заходите в группу vadzim.by live:""",
        ),
        PromoCampaign(
            key="programmer_bot_every_2_days",
            at=time(14, 30),
            interval_days=2,
            url=PROGRAMMER_BOT_URL,
            button_text="Открыть бота",
            text="""<b>Помощник программиста</b>

Бот для тех, кто изучает программирование. Помогает писать код, объясняет термины простыми словами и помогает разобраться в HTML, CSS, JavaScript, Python и других темах.""",
        ),
    )


def due_promo_posts(
    *,
    current: datetime,
    posted_keys: set[str],
    site_url: str = SITE_URL,
    channel_url: str = CHANNEL_URL,
    force: bool = False,
) -> list[PromoPost]:
    due: list[PromoPost] = []
    campaigns = sorted(
        build_campaigns(site_url=site_url, channel_url=channel_url),
        key=lambda campaign: (campaign.at.hour, campaign.at.minute),
        reverse=True,
    )
    for campaign in campaigns:
        if campaign.key in posted_keys and not force:
            continue
        if force or campaign.is_due(current):
            due.append(campaign.build_post())
    return due


def next_promo_hint(
    *,
    current: datetime,
    posted_keys: set[str],
    site_url: str = SITE_URL,
    channel_url: str = CHANNEL_URL,
) -> Optional[str]:
    candidates: list[datetime] = []
    campaigns: Sequence[PromoCampaign] = build_campaigns(
        site_url=site_url,
        channel_url=channel_url,
    )
    for days_ahead in range(8):
        day = current.date() + timedelta(days=days_ahead)
        for campaign in campaigns:
            if days_ahead == 0 and campaign.key in posted_keys:
                continue
            target = datetime.combine(day, campaign.at, tzinfo=current.tzinfo)
            if target <= current:
                continue
            if campaign.weekdays is not None and target.weekday() not in campaign.weekdays:
                continue
            if campaign.interval_days is not None:
                days_since_start = (target.date() - PROMO_INTERVAL_START).days
                if days_since_start < 0 or days_since_start % campaign.interval_days != 0:
                    continue
            candidates.append(target)

    if not candidates:
        return None
    return min(candidates).strftime("%d.%m %H:%M")
