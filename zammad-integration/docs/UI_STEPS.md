# UI_STEPS.md — Шаги, которые нельзя сделать через API

Некоторые настройки в Zammad доступны **только через веб-интерфейс**.

---

## 1. Назначение ролей агентам

Автоматическое назначение ролей при создании пользователей невозможно через API
без знания конкретных user_id. Это делается вручную:

**Путь:** Admin → Users → [выбрать агента] → Roles

| Агент | Роль |
|---|---|
| Агент первой линии | `agent_l1` |
| Агент второй линии | `agent_l2` |
| Агент третьей линии | `agent_l3` |
| Руководитель | `manager` |

---

## 2. Два варианта доступа менеджера к тикетам

Роль `manager` настроена с **read-доступом ко всем Support группам**.
Если нужен вариант "только тикеты, где менеджер — watcher/owner":

**Путь:** Admin → Roles → manager → Groups

- Убери `Support L1/L2/L3` из group_ids роли  
- Оставь только `Managers: full`
- Агент в группе Managers будет видеть тикеты только своей группы + те, где является owner/watcher

**Разница:**
- **Вариант полного доступа (текущий):** менеджер видит весь Support queue — удобно для контроля
- **Вариант restricted:** менеджер видит только Managers-тикеты + свои — меньше шума

---

## 3. Проверить миграцию поля customer_type

После запуска `setup.py` скрипт автоматически вызывает `/execute_migrations`.
Если что-то пошло не так:

**Путь:** Admin → System → Object Manager → Ticket → customer_type

Убедись, что:
- Поле отображается в списке атрибутов
- Статус: Active
- Кнопка **"Update database"** нажата (если есть pending migration)

---

## 4. SMTP / Email Channel

Для отправки email-уведомлений при SLA breach:

**Путь:** Admin → Channels → Email → Add Account

Заполни:
- From: `Support <noreply@example.com>`
- SMTP Host: из `SMTP_HOST` в `.env`
- Port: из `SMTP_PORT`
- User / Password: из `SMTP_USER` / `SMTP_PASS`
- TLS: включить если `SMTP_TLS=true`

После добавления канала тригеры `[SLA] Breach → email customer` начнут отправлять письма.

---

## 5. HTTP Notifications (для Zammad → Gateway вебхуков)

Убедись что включено:

**Путь:** Admin → System → API → HTTP Notifications

Поставь галочку **"Enable HTTP Notifications"** и сохрани.

Без этого триггеры с `notification.http` действием не работают.

---

## 6. Telegram Webhook Secret в Triggers

Если хочешь убедиться, что хедер `X-Zammad-Webhook-Secret` прописан в триггерах:

**Путь:** Admin → Triggers → [каждый trigger с "notification.http"]

Проверь, что в поле Headers есть:
```
X-Zammad-Webhook-Secret: <значение из ZAMMAD_WEBHOOK_SECRET>
```

---

## 7. SLA — выбор правила назначения (условие)

Если Zammad не применяет SLA автоматически через поле `customer_type`:

**Путь:** Admin → SLAs → [SLA VIP/Standard] → Conditions

Убедись что условие выглядит так:

**SLA VIP:**
- Ticket → Customer Type → is → VIP, Enterprise

**SLA Standard:**
- Ticket → Customer Type → is → Standard  
  ИЛИ Customer Type → has no value

---

## 8. VIP L1 группа (Вариант А маршрутизации)

Если `VIP_ROUTING_MODE=A` — нужно назначить агентов в группу **VIP L1**:

**Путь:** Admin → Users → [каждый VIP-агент] → Groups

Добавь: `VIP L1: full`

---

## 9. Watcher для Managers-тикетов (альтернатива webhook)

Если не хочешь использовать webhook `/webhook/zammad/new_ticket` для добавления watcher,
можно настроить вручную:

**Путь:** Admin → Triggers → [Route] Billing/Equipment → Managers

К действиям добавить: **Subscribe agents** → [выбрать менеджеров]

*Примечание: эта опция появляется в Actions если включена подписка на уведомления агента.*
