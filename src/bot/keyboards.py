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
            [InlineKeyboardButton(text="💬 Поддержка", callback_data="queue:support")],
            [InlineKeyboardButton(text="👔 Менеджер", callback_data="queue:manager")],
            [InlineKeyboardButton(text="📂 Мои обращения", callback_data="ticket:list")],
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
            [InlineKeyboardButton(text="📋 Статус", callback_data="ticket:status")],
            [InlineKeyboardButton(text="✅ Закрыть текущее", callback_data="ticket:close")],
            [InlineKeyboardButton(text="🆕 Новое обращение", callback_data="ticket:new")],
            [InlineKeyboardButton(text="📂 Мои обращения", callback_data="ticket:list")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")],
        ]
    )
