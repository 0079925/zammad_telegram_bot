# SETUP.md — Пошаговая инструкция запуска

## Требования

- Docker + Docker Compose v2
- Python 3.10+ (для запуска `zammad-setup/setup.py`)
- Доменное имя с HTTPS (Telegram требует TLS для webhooks)
- Zammad ≥ 5.x (рекомендуется 6.x) с включёнными HTTP-notifications

---

## 1. Клонируем и настраиваем `.env`

```bash
git clone <your-repo>
cd zammad-integration
cp .env.example .env
nano .env   # заполни все TODO_*
```

Минимально необходимые переменные:

| Переменная | Пример |
|---|---|
| `ZAMMAD_URL` | `https://zammad.example.com` |
| `ZAMMAD_ADMIN_TOKEN` | токен из Admin > Profile > Token Access |
| `TELEGRAM_BOT_TOKEN` | токен из @BotFather |
| `TELEGRAM_WEBHOOK_URL` | `https://yourdomain.com/webhook/telegram` |
| `MANAGER_TELEGRAM_CHAT_ID` | числовой chat id менеджера |
| `ZAMMAD_WEBHOOK_SECRET` | случайная строка 32+ символа |

### Как получить ZAMMAD_ADMIN_TOKEN

1. Войди в Zammad как admin
2. Кликни аватар (верхний правый угол) → **Profile**
3. Раздел **Token Access** → **Create**
4. Включи разрешения: `ticket.agent`, `admin.group`, `admin.role`, `admin.object`, `admin.sla`, `admin.trigger`, `admin.calendar`
5. Скопируй токен → вставь в `.env`

---

## 2. TLS сертификаты

```bash
mkdir -p infra/nginx/certs
# Let's Encrypt (certbot)
certbot certonly --standalone -d yourdomain.com
cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem infra/nginx/certs/
cp /etc/letsencrypt/live/yourdomain.com/privkey.pem   infra/nginx/certs/
```

---

## 3. Запуск Telegram Gateway

```bash
cd infra
docker compose up -d --build

# Проверить статус
docker compose ps
docker compose logs telegram-gateway -f
```

---

## 4. Запуск Zammad Setup Script

```bash
cd zammad-setup
pip install -r requirements.txt

# Скрипт читает ../.env автоматически (dotenv)
python setup.py
```

Скрипт создаёт (идемпотентно):
- Groups: Support L1, Support L2, Support L3, Managers (+ VIP L1 если режим A)
- Roles: agent_l1, agent_l2, agent_l3, manager
- Custom field: `customer_type` (Standard/VIP/Enterprise)
- Calendar: Business 5/2 09:00–18:00
- SLA: SLA Standard, SLA VIP
- Triggers: маршрутизация, Telegram-уведомления, SLA-алерты

После выполнения скрипт напомнит о **ручных шагах** — смотри `docs/UI_STEPS.md`.

---

## 5. Проверить webhook Telegram

```bash
# Проверить регистрацию webhook
curl https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo | jq .

# Ожидаемый ответ:
# "url": "https://yourdomain.com/webhook/telegram",
# "pending_update_count": 0,
# "last_error_message": null
```

---

## 6. Проверить весь стек

```bash
# Health endpoint
curl https://yourdomain.com/health
# → {"status": "ok"}

# Telegram → Zammad:
# 1. Напиши что-нибудь боту в Telegram
# 2. Проверь в Zammad: Admin > Tickets — должен появиться новый тикет

# Zammad → Telegram:
# 1. Ответь публичной заметкой в тикете (не internal)
# 2. Telegram клиент должен получить сообщение

# SLA alert:
# 1. Создай тикет с priority=high
# 2. Установи SLA, дождись или вручную измени escalation_at
# 3. MANAGER_TELEGRAM_CHAT_ID должен получить уведомление
```

Подробные curl-тесты — в `docs/HEALTHCHECKS.md`.

---

## Переменные SLA

Все SLA-времена в `.env` — в **минутах**:

```env
SLA_STANDARD_P1_FIRST_RESPONSE=60    # 1 час
SLA_VIP_P1_FIRST_RESPONSE=15         # 15 минут
```

Чтобы изменить SLA:
1. Обнови `.env`
2. Перезапусти `python setup.py` — скрипт обновит существующие SLA через API (idempotent PUT)

---

## Обновление и перезапуск

```bash
# Перебилдить gateway (после изменений кода)
cd infra
docker compose up -d --build telegram-gateway

# Пересоздать Zammad-настройки (безопасно, идемпотентно)
cd ../zammad-setup
python setup.py
```
