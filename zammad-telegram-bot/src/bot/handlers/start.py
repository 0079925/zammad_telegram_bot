"""
/start handler — entry point for every user.

Flow:
    1. Upsert TelegramUser in DB
    2. If phone already collected → show main menu
    3. If phone missing → ask for it (request_contact keyboard)
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.bot.keyboards import main_menu_keyboard, request_phone_keyboard
from src.bot.states import UserFlow
from src.services.user_service import UserService

router = Router(name="start")

_WELCOME = (
    "👋 <b>Добро пожаловать!</b>\n\n"
    "Здесь вы можете обратиться в службу поддержки или к менеджеру.\n"
    "Ваши сообщения будут переданы специалистам, а ответы придут сюда."
)

_ASK_PHONE = (
    "📱 Для того чтобы мы могли связаться с вами, пожалуйста, "
    "<b>поделитесь номером телефона</b>.\n\n"
    "Нажмите кнопку ниже:"
)


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    user_service: UserService,
    correlation_id: str,
) -> None:
    user = message.from_user
    if user is None:
        return

    await user_service.ensure_user(
        telegram_id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        correlation_id=correlation_id,
    )

    has_phone = await user_service.has_phone(user.id)

    if has_phone:
        await state.set_state(UserFlow.main_menu)
        await message.answer(
            _WELCOME,
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
    else:
        await state.set_state(UserFlow.awaiting_phone)
        await message.answer(_WELCOME, parse_mode="HTML")
        await message.answer(_ASK_PHONE, parse_mode="HTML", reply_markup=request_phone_keyboard())
