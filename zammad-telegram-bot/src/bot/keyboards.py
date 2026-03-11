"""Reusable keyboard factories."""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Написать в поддержку", callback_data="queue:support")],
            [InlineKeyboardButton(text="👔 Написать менеджеру", callback_data="queue:manager")],
        ]
    )


def request_phone_keyboard() -> ReplyKeyboardMarkup:
    """One-time keyboard with a 'Share contact' button."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться номером телефона", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Нажмите кнопку ниже, чтобы поделиться номером",
    )


def remove_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")],
        ]
    )


def active_ticket_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Статус тикета", callback_data="ticket:status")],
            [InlineKeyboardButton(text="🔄 Сменить направление", callback_data="menu:main")],
        ]
    )
