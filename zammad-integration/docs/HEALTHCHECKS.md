# HEALTHCHECKS.md — Проверка работоспособности

## Требования

```bash
# Установи переменные для удобства
export ZAMMAD_URL=https://zammad.example.com
export ZAMMAD_TOKEN=your_admin_token
export TG_TOKEN=your_bot_token
export GW_URL=https://yourdomain.com
```

---

## 1. Gateway — базовая проверка

```bash
# Health endpoint
curl -s $GW_URL/health | jq .
# Ожидаем: {"status": "ok"}

# Или через Docker
docker exec telegram-gateway curl -s http://localhost:8080/health
```

---

## 2. Telegram Webhook статус

```bash
curl -s "https://api.telegram.org/bot${TG_TOKEN}/getWebhookInfo" | jq '{
  url: .result.url,
  pending: .result.pending_update_count,
  last_error: .result.last_error_message
}'
```

Ожидаем `url` = твой webhook URL, `last_error` = null.

---

## 3. Zammad — API доступность

```bash
curl -s -H "Authorization: Token token=$ZAMMAD_TOKEN" \
  $ZAMMAD_URL/api/v1/users/me | jq '{login, email}'
```

---

## 4. Проверка Groups

```bash
curl -s -H "Authorization: Token token=$ZAMMAD_TOKEN" \
  $ZAMMAD_URL/api/v1/groups | jq '[.[] | {id, name}]'
# Должны быть: Support L1, L2, L3, Managers
```

---

## 5. Проверка SLA

```bash
curl -s -H "Authorization: Token token=$ZAMMAD_TOKEN" \
  $ZAMMAD_URL/api/v1/slas | jq '[.[] | {id, name, first_response_time}]'
```

---

## 6. Проверка поля customer_type

```bash
curl -s -H "Authorization: Token token=$ZAMMAD_TOKEN" \
  "$ZAMMAD_URL/api/v1/object_manager_attributes?object=Ticket" \
  | jq '.[] | select(.name=="customer_type") | {name, data_option}'
```

---

## 7. E2E: Telegram → Zammad тикет

```bash
# Отправь сообщение боту вручную (или simulate update)
# Затем проверь тикет в Zammad:

curl -s -H "Authorization: Token token=$ZAMMAD_TOKEN" \
  "$ZAMMAD_URL/api/v1/tickets?state=new&limit=1" | jq '.[0] | {id, number, title, group_id}'
```

---

## 8. E2E: Zammad → Telegram reply

```bash
# Создай тестовый тикет через API
TICKET=$(curl -s -X POST \
  -H "Authorization: Token token=$ZAMMAD_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Test","group":"Support L1","customer":"admin@example.com",
       "article":{"subject":"test","body":"test body","type":"note","internal":false}}' \
  $ZAMMAD_URL/api/v1/tickets)

TICKET_ID=$(echo $TICKET | jq -r '.id')
echo "Created ticket $TICKET_ID"

# Добавь публичный ответ агента
curl -s -X POST \
  -H "Authorization: Token token=$ZAMMAD_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"ticket_id\": $TICKET_ID, \"subject\": \"Reply\",
       \"body\": \"Hello from agent!\", \"type\": \"note\",
       \"internal\": false, \"sender\": \"Agent\"}" \
  $ZAMMAD_URL/api/v1/ticket_articles
```

Telegram клиент (если есть маппинг) получит сообщение "📩 Ответ по заявке".

---

## 9. Проверка webhook Zammad → Gateway (вручную)

```bash
# Симулируй вызов от Zammad
curl -s -X POST \
  -H "Content-Type: application/json" \
  -H "X-Zammad-Webhook-Secret: $ZAMMAD_WEBHOOK_SECRET" \
  -d '{"ticket_id": "123", "article_body": "Test reply", "ticket_number": "12345"}' \
  $GW_URL/webhook/zammad/article | jq .
# Ожидаем: {"ok": true}
```

---

## 10. SLA Alert тест

```bash
# Симулируй SLA breach webhook
curl -s -X POST \
  -H "Content-Type: application/json" \
  -H "X-Zammad-Webhook-Secret: $ZAMMAD_WEBHOOK_SECRET" \
  -d '{"ticket_id": "99", "priority": "1 high", "sla": "SLA VIP"}' \
  $GW_URL/webhook/zammad/escalation | jq .
# Менеджер должен получить Telegram-уведомление
```

---

## 11. Docker logs

```bash
# Gateway logs
docker compose -f infra/docker-compose.yml logs telegram-gateway --tail=50 -f

# Nginx access log
docker compose -f infra/docker-compose.yml logs nginx --tail=20
```

---

## Checklist быстрой проверки ✅

| Проверка | Команда | Ожидание |
|---|---|---|
| Gateway health | `curl $GW_URL/health` | `{"status":"ok"}` |
| TG webhook | `getWebhookInfo` | url установлен, no errors |
| Zammad API | `GET /api/v1/users/me` | 200 + login |
| Groups созданы | `GET /api/v1/groups` | 4+ групп |
| SLA созданы | `GET /api/v1/slas` | SLA Standard + SLA VIP |
| Поле customer_type | object_manager_attributes | name=customer_type |
| Triggers | `GET /api/v1/triggers` | 5+ триггеров |
| Telegram → Zammad | отправить боту | тикет в Support L1 |
| Zammad → Telegram | ответить публично | сообщение в TG |
| SLA Alert | POST /webhook/zammad/escalation | TG у менеджера |
