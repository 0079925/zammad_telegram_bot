"""
Contact / phone number handler.

Handles two cases:
    1. User sends their contact via the 'Share contact' button (message.contact)
    2. User types a phone number manually as text (best-effort fallback)
"""
from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.bot.keyboards import main_menu_keyboard, remove_keyboard, request_phone_keyboard
from src.bot.states import UserFlow
from src.services.user_service import UserService

router = Router(name="contact")

_PHONE_RE = re.compile(r"^\+?[\d\s\-\(\)]{7,20}$")

_THANKS = (
    "✅ <b>Номер телефона принят!</b>\n\n"
    "Теперь выберите, куда хотите обратиться:"
)

_INVALID_PHONE = (
    "❌ Не удалось распознать номер телефона.\n"
    "Пожалуйста, воспользуйтесь кнопкой <b>«Поделиться номером телефона»</b>."
)


@router.message(UserFlow.awaiting_phone, F.contact)
async def handle_contact(
    message: Message,
    state: FSMContext,
    user_service: UserService,
    correlation_id: str,
) -> None:
    contact = message.contact
    if contact is None or not contact.phone_number:
        await message.answer(_INVALID_PHONE, parse_mode="HTML", reply_markup=request_phone_keyboard())
        return

    user = message.from_user
    if user is None:
        return

    await user_service.register_phone(
        telegram_id=user.id,
        phone=contact.phone_number,
        first_name=user.first_name,
        last_name=user.last_name,
        correlation_id=correlation_id,
    )

    await state.set_state(UserFlow.main_menu)
    await message.answer(
        _THANKS,
        parse_mode="HTML",
        reply_markup=remove_keyboard(),
    )
    await message.answer(
        "Выберите направление:",
        reply_markup=main_menu_keyboard(),
    )


@router.message(UserFlow.awaiting_phone, F.text)
async def handle_phone_text(
    message: Message,
    state: FSMContext,
    user_service: UserService,
    correlation_id: str,
) -> None:
    """Accept manually typed phone as a fallback (less reliable)."""
    text = (message.text or "").strip()
    if not _PHONE_RE.match(text):
        await message.answer(_INVALID_PHONE, parse_mode="HTML", reply_markup=request_phone_keyboard())
        return

    user = message.from_user
    if user is None:
        return

    await user_service.register_phone(
        telegram_id=user.id,
        phone=text,
        first_name=user.first_name,
        last_name=user.last_name,
        correlation_id=correlation_id,
    )

    await state.set_state(UserFlow.main_menu)
    await message.answer(
        _THANKS,
        parse_mode="HTML",
        reply_markup=remove_keyboard(),
    )
    await message.answer("Выберите направление:", reply_markup=main_menu_keyboard())
