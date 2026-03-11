"""
Queue selection handler.

User presses «Написать в поддержку» or «Написать менеджеру».
We create or find the Zammad ticket and confirm to the user.

Callback data format:  "queue:support"  |  "queue:manager"
"""
from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from src.bot.keyboards import active_ticket_keyboard, main_menu_keyboard
from src.bot.states import UserFlow
from src.db.models import QueueType
from src.services.ticket_service import TicketService
from src.services.user_service import UserService

router = Router(name="queue_select")
logger = structlog.get_logger(__name__)

_QUEUE_LABELS = {
    QueueType.support: "Поддержка (Support L1)",
    QueueType.manager: "Менеджер",
}

_CHOOSE_DIRECTION = "Пожалуйста, выберите направление:"


@router.callback_query(F.data.in_({"queue:support", "queue:manager"}))
async def handle_queue_select(
    call: CallbackQuery,
    state: FSMContext,
    user_service: UserService,
    ticket_service: TicketService,
    correlation_id: str,
) -> None:
    if call.message is None or call.from_user is None:
        return

    await call.answer()

    queue = QueueType.support if call.data == "queue:support" else QueueType.manager

    # Ensure we have a Zammad user
    zammad_user_id = await user_service.get_zammad_user_id(call.from_user.id)
    if zammad_user_id is None:
        # Edge case: user somehow lost their phone record
        await call.message.answer(
            "⚠️ Не удалось найти ваш профиль. Пожалуйста, введите /start и поделитесь номером.",
        )
        return

    await call.message.answer("⏳ Создаю обращение…")

    try:
        zammad_ticket, created = await ticket_service.get_or_create_ticket(
            telegram_id=call.from_user.id,
            zammad_user_id=zammad_user_id,
            queue=queue,
            initial_message="Новое обращение через Telegram.",
            correlation_id=correlation_id,
        )
    except Exception as exc:
        logger.error("ticket_creation_failed", error=str(exc), correlation_id=correlation_id)
        await call.message.answer(
            "😔 К сожалению, не удалось создать обращение. Попробуйте чуть позже.",
        )
        return

    # Save queue to FSM data so message handler knows where to route
    await state.update_data(active_queue=queue.value)
    await state.set_state(UserFlow.in_ticket)

    state_name = (zammad_ticket.state or {}).get("name", "open")
    from src.services.ticket_service import _zammad_state_to_status, _status_display

    status_label = _status_display(_zammad_state_to_status(state_name))
    queue_label = _QUEUE_LABELS[queue]
    verb = "создано" if created else "найдено"

    text = (
        f"✅ Обращение {verb}!\n\n"
        f"📁 <b>Направление:</b> {queue_label}\n"
        f"🎫 <b>Номер тикета:</b> #{zammad_ticket.number}\n"
        f"📊 <b>Статус:</b> {status_label}\n\n"
        "Напишите ваш вопрос — и мы передадим его специалисту."
    )
    await call.message.answer(text, parse_mode="HTML", reply_markup=active_ticket_keyboard())


@router.callback_query(F.data == "menu:main")
async def handle_back_to_menu(call: CallbackQuery, state: FSMContext) -> None:
    if call.message is None:
        return
    await call.answer()
    await state.set_state(UserFlow.main_menu)
    await call.message.answer(
        "Выберите направление:",
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(F.data == "ticket:status")
async def handle_ticket_status(
    call: CallbackQuery,
    state: FSMContext,
    ticket_service: TicketService,
) -> None:
    if call.message is None or call.from_user is None:
        return
    await call.answer()

    data = await state.get_data()
    queue_raw = data.get("active_queue")
    if not queue_raw:
        await call.message.answer("Нет активного обращения. Выберите направление.")
        return

    queue = QueueType(queue_raw)
    info = await ticket_service.get_active_ticket_info(call.from_user.id, queue)
    if info is None:
        await call.message.answer("Активное обращение не найдено.")
        return

    number, status_label = info
    await call.message.answer(
        f"🎫 <b>Тикет #{number}</b>\n📊 <b>Статус:</b> {status_label}",
        parse_mode="HTML",
    )
