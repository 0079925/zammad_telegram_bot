# Zammad ↔ Telegram Bot

Двусторонняя интеграция: пользователи Telegram пишут в поддержку, ответы агентов из Zammad приходят обратно в Telegram.

---

## Этап 1 — Архитектурное проектирование

### Выбор стека и обоснование

| Компонент | Выбор | Почему |
|---|---|---|
| Язык | Python 3.11 | Богатая экосистема для Telegram/HTTP; async из коробки |
| Telegram SDK | aiogram 3.x | Современный async, FSM, middleware, активная поддержка |
| HTTP-сервер | FastAPI + uvicorn | Async, Pydantic-валидация, OpenAPI docs, лёгкий |
| База данных | PostgreSQL | ACID, JSON-поля, надёжность, знакомость ops-команды |
| FSM-хранилище | Redis | Быстрый, TTL из коробки, стандарт для aiogram FSM |
| ORM + миграции | SQLAlchemy 2 async + Alembic | Типобезопасность, ленивые запросы, авто-миграции |
| HTTP-клиент | httpx (async) | Первоклассный async, retry через tenacity |
| Логирование | structlog | JSON в prod, цвета в dev, авто-маскирование секретов |
| Контейнеризация | Docker + Docker Compose | Portainer-совместимость, reproducible builds |

### Схема компонентов

```
┌─────────────────────────────────────────────────────────────┐
│                        TELEGRAM USERS                        │
└────────────────────────────┬────────────────────────────────┘
                             │ HTTPS (Telegram API)
                    ┌────────▼────────┐
                    │   aiogram bot   │  (long-polling)
                    │  (Dispatcher)   │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
      ┌───────▼──────┐  ┌────▼─────┐  ┌────▼──────┐
      │ UserService  │  │TicketSvc │  │NotifSvc   │
      └───────┬──────┘  └────┬─────┘  └────┬──────┘
              │              │              │
              └──────────────┼──────────────┘
                             │
                    ┌────────▼────────┐
                    │  ZammadClient   │  httpx + tenacity
                    └────────┬────────┘
                             │ HTTPS (Zammad REST API)
                    ┌────────▼────────┐
                    │    ZAMMAD       │
                    └────────┬────────┘
                             │ Trigger → Webhook POST
                    ┌────────▼────────┐
                    │  FastAPI        │  /webhook/zammad
                    │  Webhook Server │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │ NotifService    │  →  aiogram Bot.send_message()
                    └─────────────────┘

      ┌─────────────────────┐     ┌──────────┐
      │     PostgreSQL      │     │  Redis   │
      │  users/tickets/logs │     │ FSM/dedup│
      └─────────────────────┘     └──────────┘
```

### Потоки данных

**Пользователь → Zammad:**
1. Telegram update → aiogram Dispatcher
2. DeduplicationMiddleware (проверяет update_id в PostgreSQL)
3. CorrelationMiddleware (генерирует correlation_id)
4. Handler (start / contact / queue_select / message / media)
5. TicketService → ZammadClient.add_article()
6. BotArticle записывается в PostgreSQL (anti-loop)

**Zammad → Telegram:**
1. Zammad Trigger срабатывает на новый article
2. POST на `/webhook/zammad` (FastAPI)
3. Валидация Authorization Bearer секрета
4. NotificationService:
   - `article.internal == true` → **skip**
   - `article.created_by_id == integration_user_id` → **skip**
   - `article_id` в таблице `bot_article` → **skip**
5. Telegram `send_message()` / `send_document()` / `send_photo()`

### Модель данных

```
telegram_user                    ticket
─────────────────────────        ─────────────────────────────────────
telegram_id   PK bigint          id               PK uuid
username         varchar         telegram_id      FK → telegram_user
first_name       varchar         zammad_ticket_id    int
last_name        varchar         zammad_ticket_number varchar
phone            varchar         queue_type          enum(support,manager)
zammad_user_id   int             status              enum(new,open,...)
created_at       timestamptz     is_active           bool
updated_at       timestamptz     created_at          timestamptz
                                 closed_at           timestamptz

bot_article                      processed_update
──────────────────────────────   ──────────────────────────────
article_id   PK int              update_id   PK bigint
ticket_id    FK → ticket         processed_at   timestamptz
created_at   timestamptz

integration_log
──────────────────────────────────────────
id             PK uuid
event_type     varchar
telegram_id    bigint
zammad_ticket_id int
correlation_id varchar
payload        jsonb
created_at     timestamptz
```

### Риски и их закрытие

| Риск | Решение |
|---|---|
| Дублирование сообщений при рестарте бота | `processed_update` — идемпотентность по update_id |
| Петля Telegram→Zammad→Telegram | `bot_article` таблица + проверка `created_by_id` |
| Утечка внутренних заметок | `article.internal == True` → skip в NotificationService |
| Потеря сообщений при недоступности Zammad | tenacity retry (3 попытки, экспоненциальный back-off) |
| Инъекция через имена файлов | `_sanitize_filename()` — удаление path traversal символов |
| Утечка секретов в логах | `_mask_sensitive()` processor в structlog |
| Падение webhook не должно ломать Zammad | Всегда возвращаем HTTP 200, ошибки логируем |
| Рассинхронизация статусов тикетов | При каждом открытии тикета — GET /tickets/{id} из Zammad |

---

## Этап 2 — Структура репозитория

```
zammad-telegram-bot/
│
├── src/                          # Весь исходный код
│   ├── config.py                 # Конфигурация через pydantic-settings
│   ├── logging_config.py         # structlog, маскирование секретов
│   ├── main.py                   # Точка входа: запуск бота + webhook сервера
│   │
│   ├── bot/                      # Telegram-слой
│   │   ├── app.py                # Фабрика Bot + Dispatcher, DI middleware
│   │   ├── states.py             # FSM-состояния (UserFlow)
│   │   ├── keyboards.py          # Инлайн/реплай клавиатуры
│   │   ├── handlers/
│   │   │   ├── start.py          # /start — первый вход
│   │   │   ├── contact.py        # Получение номера телефона
│   │   │   ├── queue_select.py   # Выбор направления (support/manager)
│   │   │   ├── message.py        # Текстовые сообщения в тикет
│   │   │   └── media.py          # Вложения (фото, документы, аудио, видео)
│   │   └── middleware/
│   │       ├── correlation.py    # Генерация correlation_id
│   │       └── dedup.py          # Дедупликация update_id
│   │
│   ├── zammad/                   # Интеграция с Zammad API
│   │   ├── client.py             # Async REST клиент (httpx + tenacity)
│   │   └── schemas.py            # Pydantic-модели Zammad API и webhook
│   │
│   ├── webhook/                  # FastAPI для входящих webhook от Zammad
│   │   ├── app.py                # Фабрика FastAPI приложения + /healthz
│   │   └── router.py             # POST /webhook/zammad — верификация + dispatch
│   │
│   ├── db/                       # Слой данных
│   │   ├── base.py               # DeclarativeBase
│   │   ├── models.py             # ORM-модели (TelegramUser, Ticket, ...)
│   │   ├── session.py            # Async engine + session factory
│   │   └── repositories/
│   │       ├── user_repository.py
│   │       ├── ticket_repository.py
│   │       └── idempotency_repository.py
│   │
│   └── services/                 # Бизнес-логика
│       ├── user_service.py       # Создание/поиск пользователя в Zammad
│       ├── ticket_service.py     # Управление тикетами
│       └── notification_service.py  # Zammad → Telegram пересылка
│
├── migrations/                   # Alembic
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial.py       # Начальная схема
│
├── tests/
│   ├── conftest.py               # Общие фикстуры
│   ├── unit/
│   │   ├── test_user_service.py
│   │   ├── test_ticket_service.py
│   │   └── test_zammad_client.py
│   └── integration/
│       ├── test_webhook_flow.py        # FastAPI TestClient
│       └── test_notification_service.py  # Anti-loop сценарии
│
├── Dockerfile                    # Multi-stage build
├── docker-compose.yml            # Production stack
├── docker-compose.override.yml   # Dev overrides (auto-loaded)
├── .env.example                  # Шаблон переменных окружения
├── alembic.ini
├── pyproject.toml
└── README.md
```

---

## Этап 4 — Docker / Portainer

### Переменные окружения (обязательные)

| Переменная | Описание |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен бота от @BotFather |
| `ZAMMAD_URL` | URL Zammad без trailing slash |
| `ZAMMAD_HTTP_TOKEN` | API-токен сервисного аккаунта |
| `ZAMMAD_WEBHOOK_SECRET` | Секрет для авторизации входящих webhook |
| `ZAMMAD_INTEGRATION_USER_ID` | ID сервисного пользователя Zammad |
| `POSTGRES_PASSWORD` | Пароль PostgreSQL |
| `DATABASE_URL` | *(Подставляется docker-compose автоматически)* |

### Необязательные переменные

| Переменная | Значение по умолчанию | Описание |
|---|---|---|
| `ZAMMAD_GROUP_SUPPORT` | `Support L1` | Группа для поддержки |
| `ZAMMAD_GROUP_MANAGER` | `manager` | Группа для менеджера |
| `LOG_LEVEL` | `INFO` | Уровень логов |
| `ENVIRONMENT` | `production` | `development` включает DEBUG и консольный вывод |
| `MAX_ATTACHMENT_SIZE_BYTES` | `20971520` | Лимит размера файла (20 МБ) |
| `ZAMMAD_MAX_RETRIES` | `3` | Попыток при временных ошибках Zammad |
| `APP_PORT` | `8080` | Порт webhook-сервера |
| `BOT_PORT` | `8080` | Внешний порт для docker-compose |

---

## Этап 5 — Эксплуатация

### Быстрый старт

```bash
# 1. Клонировать репозиторий
git clone https://github.com/yourorg/zammad-telegram-bot
cd zammad-telegram-bot

# 2. Создать .env из шаблона
cp .env.example .env
# Открыть .env и заполнить ВСЕ обязательные переменные

# 3. Запустить
docker compose up -d

# 4. Проверить здоровье
docker compose ps
curl http://localhost:8080/healthz
```

### Локальный запуск (без Docker)

```bash
# Требуется: Python 3.11+, PostgreSQL, Redis

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Применить миграции
alembic upgrade head

# Запустить
python -m src.main
```

### Запуск тестов

```bash
# Все тесты
pytest

# С отчётом о покрытии
pytest --cov=src --cov-report=term-missing

# Только unit
pytest tests/unit/

# Только integration
pytest tests/integration/
```

### Настройка Zammad Webhook

1. В Zammad: **Settings → Trigger → New Trigger**
2. Условия: `Article → created` + `Article → visibility = public`
3. Действие: `HTTP Request`
   - URL: `https://your-bot-host:8080/webhook/zammad`
   - Method: `POST`
   - Payload: `#{ticket}` + `#{article}` (JSON)
   - **Authorization**: `Bearer YOUR_ZAMMAD_WEBHOOK_SECRET`

**Важно:** В настройках Trigger нельзя задать произвольный заголовок Authorization напрямую — используйте поле `Token` если Zammad поддерживает, или настройте Nginx reverse proxy с добавлением заголовка.

### Обновление приложения

```bash
git pull origin main

# Пересобрать образ
docker compose build bot

# Применить новые миграции (если есть)
docker compose run --rm migrate

# Перезапустить бота
docker compose up -d bot
```

### Диагностика

**Посмотреть логи:**
```bash
docker compose logs -f bot
docker compose logs -f bot --since 1h
```

**Проверить здоровье сервисов:**
```bash
docker compose ps
curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
```

**Подключиться к PostgreSQL:**
```bash
docker compose exec postgres psql -U botuser -d zammadbot

# Посмотреть последние события
SELECT event_type, telegram_id, zammad_ticket_id, created_at
FROM integration_log
ORDER BY created_at DESC LIMIT 20;

# Найти пользователя
SELECT * FROM telegram_user WHERE telegram_id = 123456789;

# Посмотреть активные тикеты
SELECT t.*, tu.first_name
FROM ticket t
JOIN telegram_user tu ON tu.telegram_id = t.telegram_id
WHERE t.is_active = true
ORDER BY t.created_at DESC;
```

**Проверить Redis (FSM состояния):**
```bash
docker compose exec redis redis-cli
127.0.0.1:6379> KEYS *
127.0.0.1:6379> GET fsm:<telegram_id>:*
```

**Проверить дедупликацию:**
```bash
# В PostgreSQL
SELECT count(*) FROM processed_update;
SELECT * FROM processed_update ORDER BY processed_at DESC LIMIT 10;
```

**Проверить webhook вручную:**
```bash
curl -X POST http://localhost:8080/webhook/zammad \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_ZAMMAD_WEBHOOK_SECRET" \
  -d '{
    "ticket": {"id": 1, "number": "1001", "title": "Test", "state": {"name": "open"}},
    "article": {
      "id": 999,
      "ticket_id": 1,
      "body": "Ответ агента",
      "internal": false,
      "created_by_id": 99,
      "content_type": "text/plain",
      "attachments": []
    }
  }'
```

**Посмотреть correlation_id в логах:**
```bash
docker compose logs bot | grep '"correlation_id"' | jq .
```

### Что делать при типичных проблемах

| Симптом | Причина | Решение |
|---|---|---|
| Бот не отвечает | Неверный TELEGRAM_BOT_TOKEN | Проверить токен в @BotFather |
| Webhook возвращает 401 | Неверный ZAMMAD_WEBHOOK_SECRET | Сверить значение с настройками Trigger в Zammad |
| Тикет не создаётся | Неверные права Zammad API или название группы | Проверить ZAMMAD_GROUP_SUPPORT/MANAGER, права токена |
| Ответы агента не приходят | Trigger не настроен или Zammad не может достучаться до бота | Проверить сетевую доступность, логи запросов в Zammad |
| Дублирующиеся сообщения | Zammad повторяет webhook при timeout | Bot всегда отдаёт 200; проверить таблицу bot_article |
| Внутренние заметки утекают | Ошибка в поле internal | Проверить схему Trigger в Zammad (отправлять только visibility=public) |

---

## Сценарии работы

### Новый пользователь
```
/start
→ Приветствие + запрос телефона
→ Пользователь нажимает «Поделиться номером»
→ Создаётся запись в telegram_user + Zammad user
→ Показывается главное меню (2 кнопки)
```

### Пользователь не дал телефон
```
Нажимает кнопку поддержки без номера
→ Видит сообщение с кнопкой «Поделиться номером»
→ Пока телефон не передан — тикет не создаётся
```

### Первое сообщение вложением
```
Файл приходит в state in_ticket
→ Если нет активного тикета → предлагается вернуться в меню
→ При активном тикете → файл загружается в Zammad как attachment article
```

### Пользователь долго молчал, тикет закрыли
```
Пользователь пишет → TicketService проверяет Zammad статус
→ Статус closed → деактивирует тикет в БД
→ Создаётся новый тикет
→ Пользователь видит подтверждение нового тикета
```

### Несколько агентов ответили
```
Каждый новый public article → отдельный webhook → отдельное сообщение в Telegram
Заголовок «💬 Ответ от поддержки» + номер тикета в каждом
```

### Системный комментарий / internal note
```
article.internal == true → NotificationService → немедленный return без send_message
```

---

## Ограничения и следующий этап

### Текущие ограничения

1. **Один активный тикет на очередь** — при втором обращении в ту же очередь используется существующий тикет (если открыт). Можно расширить до явного создания нового через команду.
2. **Polling-режим** — в production предпочтительнее webhook-режим для Telegram (меньше задержка). Код поддерживает webhook через `TELEGRAM_WEBHOOK_URL`.
3. **Нет rich-text в Telegram** — HTML из Zammad стрипается. Можно добавить конвертацию через `html2text`.
4. **Нет поддержки закрытия тикета из Telegram** — пользователь не может сам закрыть тикет.
5. **Размер файла** — ограничен Telegram API (~50 МБ для документов через Bot API).

### Следующий этап

- [ ] Webhook-режим для Telegram (через reverse proxy)
- [ ] Команда `/status` — статус текущего тикета
- [ ] Команда `/new` — принудительно новый тикет
- [ ] HTML → Markdown конвертация ответов агентов
- [ ] Поддержка SLA-уведомлений (напомнить если нет ответа N часов)
- [ ] Метрики (Prometheus endpoint)
- [ ] Поддержка нескольких языков (i18n)
- [ ] Раздельные потоки для файлов >10 МБ через Telegram file_id
- [ ] Автоматическая оценка качества поддержки (CSAT) после закрытия тикета
- [ ] Admin-панель: список пользователей, статистика обращений
