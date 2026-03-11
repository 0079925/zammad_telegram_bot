"""
Text message handler — routes user messages to the active Zammad ticket.

Only active in UserFlow.in_ticket state.
All other text messages (menu state, fallback) are handled here too.
"""
from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.bot.keyboards import main_menu_keyboard
from src.bot.states import UserFlow
from src.db.models import QueueType
from src.services.ticket_service import TicketService

router = Router(name="message")
logger = structlog.get_logger(__name__)

_NO_TICKET = (
    "⚠️ У вас нет активного обращения.\n"
    "Выберите направление, чтобы начать:"
)


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
        await message.answer(_NO_TICKET, reply_markup=main_menu_keyboard())
        return

    queue = QueueType(queue_raw)
    sent = await ticket_service.add_text_article(
        telegram_id=user.id,
        queue=queue,
        text=message.text,
        correlation_id=correlation_id,
    )

    if not sent:
        # Active ticket disappeared (closed/merged externally)
        await state.set_state(UserFlow.main_menu)
        await message.answer(
            "ℹ️ Ваше предыдущее обращение было закрыто.\n"
            "Выберите направление для нового обращения:",
            reply_markup=main_menu_keyboard(),
        )
        return

    await message.react([])  # clear any previous reactions


@router.message(UserFlow.main_menu, F.text)
async def handle_menu_text(message: Message) -> None:
    """Catch stray text in menu state."""
    await message.answer(
        "Пожалуйста, выберите направление с помощью кнопок.",
        reply_markup=main_menu_keyboard(),
    )


@router.message(F.text)
async def handle_fallback_text(message: Message, state: FSMContext) -> None:
    """
    Catch-all for any unhandled text message.
    Happens when, e.g., user texts before pressing /start.
    """
    current_state = await state.get_state()
    if current_state is None:
        # Completely fresh user — act like /start
        from src.bot.handlers.start import cmd_start

        # Re-dispatch is not trivial; send a friendly nudge instead
        await message.answer(
            "👋 Добро пожаловать! Для начала работы введите /start"
        )
    else:
        await message.answer(
            "Воспользуйтесь кнопками меню или введите /start для перезапуска."
        )
