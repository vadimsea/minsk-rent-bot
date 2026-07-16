# Минск-Аренда — агрегатор объявлений для Telegram

MVP-проект: бот-агрегатор, который ищет **новые объявления о долгосрочной аренде квартир и комнат в Минске**, аккуратно форматирует их и публикует в Telegram-канал/группу. Источники: Realt.by, Kufar (re.kufar.by), Domovita.by, Hata.by, Onliner (зарезервировано).

**Это не парсер для копирования базы.** Из каждого объявления берётся только минимальная полезная информация: цена, район, площадь, этаж, краткое описание, одна фотография и **обязательно ссылка на оригинал**.

Реклама публикуется по расписанию Минска: **vadzim.by** каждый день в **10:05**, **ATEN** каждый день в **12:30** и **18:30**, канал удалённой работы в Беларуси по воскресеньям в **11:30**, канал про ИИ/маркетинг/дизайн по вторникам и пятницам в **16:30**, бот-помощник программиста раз в 2 дня в **14:30**.

---

## Что бот НЕ делает

- Не публикует посуточную аренду, продажу, коммерческую недвижимость, дома, коттеджи, гаражи, машиноместа, гостиницы, хостелы.
- Не публикует объявления без цены и без ссылки (а если `REQUIRE_PHOTO=true` — и без фото).
- Не копирует длинные описания полностью.
- Не публикует телефоны, имена владельцев, личные контакты и любые ПДн.
- Не обходит капчу, не ломает защиту сайтов, не использует прокси для обхода блокировок.
- Уважает `robots.txt` и держит паузы между запросами.
- Не публикует одну и ту же ссылку повторно (есть SQLite-дедуп).

Если сайт-источник запрещает парсинг — отключите его в `.env` (`ENABLE_*=false`).

---

## Структура проекта

```
.
├── main.py                  # точка входа
├── config.py                # настройки из .env
├── sources.py               # список источников
├── fetcher.py               # HTTP-клиент с retry/паузами/robots.txt
├── normalizer.py            # приведение к единому формату
├── filters.py               # фильтры (аренда / долгосрочная / квартира-комната / дубли / suspicious)
├── formatter.py             # шаблоны Telegram-постов
├── telegram_publisher.py    # отправка в Telegram (фото / текст / dry_run)
├── storage.py               # SQLite-хранилище опубликованных
├── scheduler.py             # запуск по расписанию (APScheduler)
├── parsers/
│   ├── __init__.py
│   ├── base.py
│   ├── realt.py
│   ├── kufar.py
│   ├── domovita.py
│   ├── hata.py
│   └── onliner.py
├── requirements.txt
├── .env.example
└── README.md
```

---

## Требования

- Python **3.11+**
- Доступ в интернет
- Telegram-бот (создаётся за минуту)

---

## Шаг 1. Создать Telegram-бота через BotFather

1. Откройте Telegram, найдите [@BotFather](https://t.me/BotFather).
2. Отправьте `/newbot`.
3. Придумайте имя (например, `Minsk Rent Aggregator`) и логин (должен заканчиваться на `bot`, например `minsk_rent_aggregator_bot`).
4. BotFather пришлёт **токен** вида `123456789:ABC-XYZ...`. Сохраните его.

---

## Шаг 2. Добавить бота админом в канал/группу

1. Создайте Telegram-канал или группу (или используйте существующую).
2. Откройте канал → **Управление каналом** → **Администраторы** → **Добавить администратора** → найдите вашего бота → дайте право **публиковать сообщения** (Post Messages).
3. Узнайте ID канала:
   - Самый простой способ — переслать любое сообщение из канала в [@userinfobot](https://t.me/userinfobot), он покажет ID.
   - Для **публичного** канала можно использовать username: `@my_channel`.
   - Для **приватного** канала нужен числовой ID вида `-1001234567890`.

---

## Шаг 3. Поставить зависимости

```bash
python -m venv .venv

# Linux/macOS
source .venv/bin/activate

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

---

## Шаг 4. Заполнить `.env`

Скопируйте пример и отредактируйте:

```bash
# Linux/macOS
cp .env.example .env

# Windows
copy .env.example .env
```

Минимально нужно заполнить:

```
TELEGRAM_BOT_TOKEN=123456789:ABC-XYZ...
TELEGRAM_CHANNEL_ID=@my_channel
DRY_RUN=true
```

Все остальные параметры опциональны и имеют разумные значения по умолчанию.

---

## Шаг 5. Запуск в DRY_RUN

Первый запуск ВСЕГДА делайте с `DRY_RUN=true` — это позволит увидеть, что бот собирается опубликовать, **без отправки в Telegram**:

```bash
python main.py --once
```

В консоли вы увидите:

- сколько объявлений найдено по каждому источнику,
- сколько отброшено и по какой причине (`not_long_term`, `not_apartment_or_room`, `no_photo`, `duplicate`, `suspicious`, ...),
- готовый текст поста и `image_url` каждого объявления, которое прошло фильтры.

Параллельно лог пишется в `rent_bot.log`.

---

## Шаг 6. Включить реальную публикацию

Когда убедились, что результат корректный — поставьте в `.env`:

```
DRY_RUN=false
```

И запустите снова:

```bash
python main.py --once
```

Бот опубликует в канал до `POSTS_PER_RUN` объявлений с паузой `POST_INTERVAL_SECONDS` между ними и запомнит ссылки, чтобы не публиковать повторно.

---

## Шаг 7. Запуск по расписанию

Встроенный планировщик (`SCHEDULE_MODE` в `.env`):

```bash
python main.py --scheduled
```

Режимы:

- `once` — один прогон и выход.
- `interval` — раз в `RUN_EVERY_HOURS` часов (по умолчанию каждые 6 часов).
- `daily` — каждый день в `POST_TIME` по `TIMEZONE`.

Процесс будет висеть в консоли. Останавливать через Ctrl+C.

### Cron (Linux)

Запуск каждые 6 часов:

```cron
0 */6 * * * /path/to/project/.venv/bin/python /path/to/project/main.py --once >> /path/to/project/rent_bot.log 2>&1
```

### systemd (Linux)

`/etc/systemd/system/rent-bot.service`:

```ini
[Unit]
Description=Minsk Rent Aggregator Bot
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/project
ExecStart=/path/to/project/.venv/bin/python /path/to/project/main.py --scheduled
Restart=on-failure
RestartSec=30
User=youruser

[Install]
WantedBy=multi-user.target
```

Активация:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rent-bot.service
sudo systemctl status rent-bot.service
```

---

## Как добавить новый источник

1. Создайте файл `parsers/новый_источник.py` с классом, унаследованным от `BaseParser`. Реализуйте метод `parse_list()`, возвращающий объявления в едином формате (см. `parsers/base.py`).
2. Зарегистрируйте парсер в `parsers/__init__.py` в словаре `PARSERS`.
3. Добавьте источник в `sources.py` (`Source(...)`) с правильными `parser_name`, `category`, `rent_period`.
4. При необходимости добавьте флаг `ENABLE_NEWSOURCE` в `.env` и в `config.py`/`sources.py`.

## Как отключить источник

Самый быстрый способ — в `.env`:

```
ENABLE_REALT=false
ENABLE_KUFAR=false
ENABLE_DOMOVITA=false
ENABLE_HATA=false
ENABLE_ONLINER=false
```

Можно отключить любой по отдельности.

## Как включить/отключить квартиры или комнаты

В `.env`:

```
# только квартиры
ALLOWED_LISTING_TYPES=apartment

# только комнаты
ALLOWED_LISTING_TYPES=room

# и то и то (по умолчанию)
ALLOWED_LISTING_TYPES=apartment,room
```

---

## Параметры фильтрации

```
CITY=Минск
RENT_TYPE=long_term
ALLOWED_LISTING_TYPES=apartment,room
REQUIRE_PHOTO=true
MIN_ROOM_PRICE_USD=80
MIN_APARTMENT_PRICE_USD=150
MAX_PRICE_USD=2000
```

Объявления с подозрительными ключевыми словами («предоплата на карту», «без просмотра», «бронь по предоплате» и т.п.) отсеиваются как `suspicious`.

---

## Логи и БД

- Лог: `rent_bot.log` (уровень — `LOG_LEVEL`, по умолчанию `INFO`).
- БД опубликованных: `published.sqlite3` (путь — `DB_PATH`).
  В ней лежит таблица `published(url, source, listing_type, title, price, published_at)`. Чтобы «забыть» историю — удалите файл, и бот снова сможет публиковать те же объявления.

---

## Этика и юридические моменты

- Бот всегда оставляет ссылку на оригинал, не копирует базу и публикует минимум информации (без ПДн).
- Уважайте условия каждого сайта-источника. Если сайт запрещает автоматический сбор — **отключите его** в `.env`.
- Не повышайте частоту запросов (`REQUEST_DELAY_SECONDS`) ниже разумной. По умолчанию пауза 3 секунды.
- Никаких прокси для обхода блокировок и никакого обхода капчи.

---

## Частые проблемы

- **«HTTP 403/429 от источника»** — сайт временно блокирует. Подождите, увеличьте `REQUEST_DELAY_SECONDS`, или отключите источник.
- **«fetch_text вернул None для всех источников»** — проверьте интернет, прокси, антивирус.
- **«Telegram отвечает 400: chat not found»** — проверьте `TELEGRAM_CHANNEL_ID`. Для приватного канала используйте числовой ID `-100…`.
- **«Telegram отвечает 403: bot was kicked» / «not enough rights»** — бот должен быть админом канала с правом «Publish Messages».
- **«Парсер ничего не возвращает»** — сайт мог поменять разметку. Запустите с `LOG_LEVEL=DEBUG`, посмотрите ответ. Если сломалось — отключите источник до починки.

---

Удачного запуска!
