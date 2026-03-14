# Архитектура решения

## Источники истины

| Данные | Источник истины | Где хранится |
|---|---|---|
| Telegram user ↔ local user | `telegram_user.telegram_id` (PK) | PostgreSQL |
| Local user ↔ Zammad user | `telegram_user.zammad_user_id` | PostgreSQL |
| Telegram dialog ↔ Zammad ticket | `ticket` (telegram_id + queue_type + is_active) | PostgreSQL |
| FSM состояние диалога | aiogram FSM (active_queue, state) | Redis |
| Уже обработанные update_id | `processed_update` | PostgreSQL |
| Статьи, созданные ботом | `bot_article.article_id` | PostgreSQL |

## Механизм получения ответов от Zammad

**Выбранный вариант: Webhook (push)**

Zammad → Trigger → HTTP POST → наш FastAPI сервер → NotificationService → bot.send_message()

**Почему не polling:**
- Polling требовал бы периодического `GET /api/v1/tickets?...` по всем активным тикетам
- Нет события "новый article" в Zammad API через polling — только timestamp-based фильтры
- Каждый новый пользователь увеличивал бы нагрузку на polling
- Webhook: O(1) при любом количестве пользователей, реальный real-time

**Как настроить Trigger в Zammad — нужно ДВА триггера:**

```
# Триггер 1: новый публичный ответ агента
Conditions:  Article → created
             Article → visibility = public  (только публичные)
Action:      HTTP Request POST https://<bot-host>:<port>/webhook/zammad
             Authorization: Bearer <ZAMMAD_WEBHOOK_SECRET>
             Payload: all ticket + article fields

# Триггер 2: смена статуса тикета (закрыт / переоткрыт) БЕЗ статьи
Conditions:  Ticket → state → changed
Action:      HTTP Request POST https://<bot-host>:<port>/webhook/zammad
             Authorization: Bearer <ZAMMAD_WEBHOOK_SECRET>
             Payload: all ticket fields  (article — пусто)
```

Без Триггера 2 бот не узнает о закрытии тикета, если агент закрыл его без публичного ответа.

## Механизм отправки ответов в Telegram

`NotificationService.handle_webhook()` вызывает `bot.send_message(chat_id=ticket.telegram_id)`.

`telegram_id` берётся из таблицы `ticket` → `telegram_user.telegram_id`.
Одному `zammad_ticket_id` соответствует один `telegram_id`.

Заголовок уведомления формируется по `db_ticket.queue_type`:
- `support` → `💬 Поддержка — ответ агента`
- `manager` → `👔 Менеджер — ответ агента`

Это позволяет пользователю различать ответы, даже если у него одновременно
открыты оба тикета (в поддержку и к менеджеру).

## Стратегия идемпотентности

| Контекст | Механизм |
|---|---|
| Telegram update | `processed_update` (PK = update_id); деdup-middleware проверяет перед обработкой |
| Zammad webhook | Возвращаем HTTP 200 всегда; повторная доставка безопасна — article_id в `bot_article` |
| Создание Zammad user | `search_user_by_login(tg_{id})` перед `create_user` |
| Создание Zammad ticket | Проверка `get_active(telegram_id, queue)` перед `create_ticket` |

## Стратегия восстановления после рестарта

1. **PostgreSQL**: все связи пользователей и тикетов персистентны — не теряются
2. **Redis**: FSM-состояния (`in_ticket`, `active_queue`) сохраняются (`appendonly yes`)
3. **При потере Redis**: пользователь получает friendly-сообщение при следующем тексте,
   FSM сбрасывается в `main_menu` — данные в PostgreSQL не затронуты
4. **processed_update**: хранится в PostgreSQL — дубли не пройдут и после рестарта

## Стратегия обработки ошибок

| Слой | Стратегия |
|---|---|
| ZammadClient | tenacity: 3 попытки, exponential backoff на 429/5xx и сетевые ошибки |
| Webhook handler | Всегда HTTP 200 — ошибки логируются, Zammad не повторяет бесконечно |
| Bot handlers | try/except с user-friendly сообщением, без технических деталей |
| DB session | context manager с `rollback()` при исключении |
| Вложения | Fallback: если скачивание из Zammad не удалось — текстовое уведомление пользователю |

## Анти-петля Telegram → Zammad → Telegram

```
Webhook получен
      │
      ├─► article is None?                   → State-only path
      │       ├─► update ticket status in DB
      │       ├─► notify if closed/reopened
      │       └─► done ✓
      │
      ├─► article.internal == True?          → DROP (внутренняя заметка)
      │
      ├─► article.created_by_id
      │   == ZAMMAD_INTEGRATION_USER_ID?     → DROP (создан ботом)
      │
      ├─► article.id ∈ bot_article table?    → DROP (belt-and-suspenders)
      │
      └─► Forwarded to Telegram ✓
           + заголовок с лейблом очереди (💬 Поддержка / 👔 Менеджер)
           + кнопка «↩️ Ответить в этот тикет»
```

## Границы слоёв

```
┌─────────────────────────────────────────────────┐
│  Transport layer                                 │
│  src/bot/handlers/     src/webhook/router.py     │
│  (aiogram handlers)    (FastAPI endpoint)        │
│  Отвечает за: FSM,     Отвечает за: auth,        │
│  keyboard, UX text     request parsing           │
└──────────────────┬──────────────────┬────────────┘
                   │                  │
┌──────────────────▼──────────────────▼────────────┐
│  Business logic layer                             │
│  src/services/                                    │
│  UserService   TicketService   NotificationSvc    │
│  Отвечает за: бизнес-правила, маршрутизацию,     │
│  lifecycle-логику, никакого HTTP/DB кода          │
└──────────────────┬──────────────────┬────────────┘
                   │                  │
      ┌────────────┘                  └──────────────┐
      │                                              │
┌─────▼─────────────────┐      ┌─────────────────────▼──┐
│  Integration layer     │      │  Storage layer          │
│  src/zammad/client.py  │      │  src/db/repositories/   │
│  Отвечает за:          │      │  Отвечает за: SQL-       │
│  HTTP, retry, auth,    │      │  запросы, маппинг ORM,  │
│  base64, sanitize      │      │  транзакции             │
└────────────────────────┘      └─────────────────────────┘
```

## Диаграмма полного цикла

```
USER (Telegram)                  BOT                    ZAMMAD
     │                            │                        │
     │─── /start ──────────────►  │                        │
     │                            │── upsert TelegramUser  │
     │◄── "Поделитесь номером" ── │                        │
     │                            │                        │
     │─── Contact (phone) ──────► │                        │
     │                            │── search_user_by_login │
     │                            │◄── None ───────────────│
     │                            │── create_user ────────►│
     │                            │◄── {id:77} ────────────│
     │                            │── link_zammad_user(77) │
     │◄── "Выберите направление" ─│                        │
     │                            │                        │
     │─── [Написать в поддержку]► │                        │
     │                            │── get_active → None    │
     │                            │── create_ticket ──────►│
     │                            │◄── {id:200, #200001} ──│
     │                            │── record DB ticket     │
     │◄── "✅ Тикет #200001" ─────│                        │
     │                            │                        │
     │─── "Есть вопрос..." ──────►│                        │
     │                            │── add_article ────────►│
     │                            │◄── {article_id:300} ───│
     │                            │── record bot_article   │
     │◄── "✅ Передано" ──────────│                        │
     │                            │                        │
     │                            │           AGENT REPLIES IN ZAMMAD
     │                            │                        │
     │                            │◄── POST /webhook/zammad│
     │                            │    {ticket, article}   │
     │                            │                        │
     │                            │── is_bot_article(300)? NO
     │                            │── is_internal? NO      │
     │                            │── created_by_id==5? NO │
     │                            │── get ticket by zammad_id
     │                            │── telegram_id = 111222333
     │◄── "💬 Ответ: ..." ────────│                        │
     │                            │                        │
     │─── повторное сообщение ───►│                        │
     │                            │── get_active → ticket #200001 (OPEN)
     │                            │── add_article ────────►│
     │                            │                        │
     │         (тикет закрыт в Zammad)                     │
     │                            │◄── POST /webhook       │
     │                            │    state=closed        │
     │◄── "✅ Тикет #200001 закрыт"│                       │
     │                            │                        │
     │─── новое сообщение ───────►│                        │
     │                            │── get_active → CLOSED  │
     │                            │── update_status        │
     │                            │── create_ticket NEW ──►│
     │                            │◄── {id:201, #200002} ──│
     │◄── "✅ Новый тикет #200002"─│                       │
```
