"""
Text message handler.

State routing:
  UserFlow.in_ticket  → text goes into the active Zammad ticket
  UserFlow.main_menu  → nudge the user to press a queue button
  (no state)          → user hasn't pressed /start yet → friendly hint
"""
from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.bot.keyboards import active_ticket_keyboard, main_menu_keyboard
from src.bot.states import UserFlow
from src.db.models import QueueType
from src.services.ticket_service import TicketService

router = Router(name="message")
logger = structlog.get_logger(__name__)


@router.message(UserFlow.in_ticket, F.text)
async def handle_in_ticket_text(
    message: Message,
    state: FSMContext,
    ticket_service: TicketService,
    correlation_id: str,
) -> None:
    user = message.from_user
    if user is None or not message.text:
        return

    data = await state.get_data()
    queue_raw = data.get("active_queue")

    if not queue_raw:
        # FSM data lost (e.g. Redis was flushed) — gracefully recover
        await state.set_state(UserFlow.main_menu)
        await message.answer(
            "⚠️ Не удалось определить активное направление. Пожалуйста, выберите снова:",
            reply_markup=main_menu_keyboard(),
        )
        return

    queue = QueueType(queue_raw)

    sent = await ticket_service.add_text_article(
        telegram_id=user.id,
        queue=queue,
        text=message.text,
        correlation_id=correlation_id,
    )

    if not sent:
        # Ticket was closed/merged externally since the last interaction
        await state.set_state(UserFlow.main_menu)
        await message.answer(
            "ℹ️ Ваше предыдущее обращение было закрыто.\n"
            "Выберите направление — будет создано новое обращение:",
            reply_markup=main_menu_keyboard(),
        )
        return

    # Subtle confirmation: reply with the current ticket status keyboard
    # so the user always has a way to check status without cluttering the chat
    await message.answer(
        "✅ Сообщение передано специалисту.",
        reply_markup=active_ticket_keyboard(),
    )


@router.message(UserFlow.main_menu, F.text)
async def handle_menu_stray_text(message: Message) -> None:
    """User typed text while at main menu — remind them to use the buttons."""
    await message.answer(
        "Пожалуйста, выберите направление с помощью кнопок ниже:",
        reply_markup=main_menu_keyboard(),
    )


@router.message(F.text)
async def handle_fallback_text(message: Message, state: FSMContext) -> None:
    """
    Catch-all handler for text messages with no active state.
    Happens when a user writes before ever pressing /start, or after
    their FSM state expired/was cleared.
    """
    current = await state.get_state()
    if current is None:
        await message.answer(
            "👋 Добро пожаловать!\nДля начала работы введите /start"
        )
    else:
        logger.warning(
            "unhandled_text_in_state",
            state=current,
            telegram_id=message.from_user.id if message.from_user else None,
        )
        await message.answer(
            "Воспользуйтесь кнопками меню или введите /start для перезапуска."
        )
