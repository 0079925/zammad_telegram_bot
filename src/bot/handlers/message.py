"""
Text message handler + simple command UX.
"""
from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.bot.keyboards import active_ticket_keyboard, main_menu_keyboard
from src.bot.states import UserFlow
from src.db.models import QueueType
from src.services.ticket_service import TicketService

router = Router(name="message")
logger = structlog.get_logger(__name__)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "🧭 <b>Как пользоваться</b>\n\n"
        "Самое простое — кнопками:\n"
        "• 🆕 Новое обращение\n"
        "• 📂 Мои обращения\n"
        "• ✅ Закрыть текущее\n\n"
        "Команды (если нужно):\n"
        "<code>/menu</code> — главное меню\n"
        "<code>/new</code> — новое обращение\n"
        "<code>/close</code> — закрыть текущее\n"
        "<code>/help</code> — эта подсказка",
        parse_mode="HTML",
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.set_state(UserFlow.main_menu)
    await message.answer(
        "Выберите направление или откройте список обращений:",
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("new"))
async def cmd_new(message: Message, state: FSMContext) -> None:
    await state.set_state(UserFlow.main_menu)
    await message.answer(
        "🆕 Новое обращение. Выберите направление:",
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("close"))
async def cmd_close(
    message: Message,
    state: FSMContext,
    ticket_service: TicketService,
    correlation_id: str,
) -> None:
    user = message.from_user
    if user is None:
        return

    data = await state.get_data()
    queue_raw = data.get("active_queue")
    if not queue_raw:
        await message.answer("Нет активного обращения. Нажмите «🆕 Новое обращение».", reply_markup=main_menu_keyboard())
        return

    number = await ticket_service.close_active_ticket(
        telegram_id=user.id,
        queue=QueueType(queue_raw),
        correlation_id=correlation_id,
    )
    if number is None:
        await message.answer("Не нашёл активного обращения.", reply_markup=main_menu_keyboard())
        return

    await state.set_state(UserFlow.main_menu)
    await message.answer(
        f"✅ Тикет #{number} закрыт.\nНужно новое обращение — выберите направление:",
        reply_markup=main_menu_keyboard(),
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
        await state.set_state(UserFlow.main_menu)
        await message.answer(
            "⚠️ Не удалось определить активное направление. Выберите снова:",
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
        await state.set_state(UserFlow.main_menu)
        await message.answer(
            "ℹ️ Ваше предыдущее обращение было закрыто.\n"
            "Выберите направление — создадим новое:",
            reply_markup=main_menu_keyboard(),
        )
        return

    await message.answer(
        "✅ Сообщение отправлено в обращение.",
        reply_markup=active_ticket_keyboard(),
    )


@router.message(UserFlow.main_menu, F.text)
async def handle_menu_stray_text(message: Message) -> None:
    await message.answer(
        "Пожалуйста, выберите направление кнопкой ниже:",
        reply_markup=main_menu_keyboard(),
    )


@router.message(F.text)
async def handle_fallback_text(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is None:
        await message.answer("👋 Для начала введите /start")
    else:
        logger.warning(
            "unhandled_text_in_state",
            state=current,
            telegram_id=message.from_user.id if message.from_user else None,
        )
        await message.answer("Используйте кнопки меню или введите /menu")
