"""
Queue and ticket action handlers.
"""
from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.keyboards import active_ticket_keyboard, main_menu_keyboard
from src.bot.states import UserFlow
from src.db.models import QueueType, TicketStatus
from src.services.ticket_service import TicketService
from src.services.user_service import UserService

router = Router(name="queue_select")
logger = structlog.get_logger(__name__)

_QUEUE_LABELS = {
    QueueType.support: "Поддержка",
    QueueType.manager: "Менеджер",
}


def _queue_label(queue: QueueType) -> str:
    return _QUEUE_LABELS.get(queue, queue.value)


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

    zammad_user_id = await user_service.get_zammad_user_id(call.from_user.id)
    if zammad_user_id is None:
        await call.message.answer(
            "⚠️ Не удалось найти ваш профиль. Пожалуйста, введите /start и поделитесь номером."
        )
        return

    await call.message.answer("⏳ Открываю обращение…")

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
        await call.message.answer("😔 Не удалось создать обращение. Попробуйте чуть позже.")
        return

    await state.update_data(active_queue=queue.value)
    await state.set_state(UserFlow.in_ticket)

    state_name = (zammad_ticket.state or {}).get("name", "open")
    from src.services.ticket_service import _status_display, _zammad_state_to_status

    status_label = _status_display(_zammad_state_to_status(state_name))
    queue_label = _queue_label(queue)
    verb = "создано" if created else "открыто"

    text = (
        f"✅ Обращение {verb}!\n\n"
        f"📁 <b>Направление:</b> {queue_label}\n"
        f"🎫 <b>Тикет:</b> #{zammad_ticket.number}\n"
        f"📊 <b>Статус:</b> {status_label}\n\n"
        "Напишите сообщение — отправим его в этот тикет."
    )
    await call.message.answer(text, parse_mode="HTML", reply_markup=active_ticket_keyboard())


@router.callback_query(F.data == "menu:main")
async def handle_back_to_menu(call: CallbackQuery, state: FSMContext) -> None:
    if call.message is None:
        return
    await call.answer()
    await state.set_state(UserFlow.main_menu)
    await call.message.answer(
        "Выберите направление:\n"
        "• 💬 Поддержка\n"
        "• 👔 Менеджер\n"
        "• 📂 Мои обращения",
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(F.data == "ticket:new")
async def handle_new_ticket(call: CallbackQuery, state: FSMContext) -> None:
    if call.message is None:
        return
    await call.answer()
    await state.set_state(UserFlow.main_menu)
    await call.message.answer(
        "🆕 Новое обращение. Выберите направление:",
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
        await call.message.answer("Нет активного обращения. Нажмите «🆕 Новое обращение».")
        return

    queue = QueueType(queue_raw)
    info = await ticket_service.get_active_ticket_info(call.from_user.id, queue)
    if info is None:
        # Ticket may have been closed by agent — sync from Zammad to confirm
        synced = await ticket_service.sync_status_by_queue(call.from_user.id, queue)
        if synced is None:
            await call.message.answer("Активное обращение не найдено.")
            return
        number, status_label, was_closed = synced
        if was_closed:
            await state.set_state(UserFlow.main_menu)
            await call.message.answer(
                f"🔴 Тикет <b>#{number}</b> закрыт (убран менеджером).\n"
                "Если вопрос не решён — создайте новое обращение:",
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(),
            )
            return
        await call.message.answer(
            f"🎫 <b>Тикет #{number}</b>\n📊 <b>Статус:</b> {status_label}",
            parse_mode="HTML",
        )
        return

    number, status_label = info
    await call.message.answer(
        f"🎫 <b>Тикет #{number}</b>\n📊 <b>Статус:</b> {status_label}",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "ticket:close")
async def handle_ticket_close(
    call: CallbackQuery,
    state: FSMContext,
    ticket_service: TicketService,
    correlation_id: str,
) -> None:
    if call.message is None or call.from_user is None:
        return
    await call.answer()

    data = await state.get_data()
    queue_raw = data.get("active_queue")
    if not queue_raw:
        await call.message.answer("Нет активного обращения для закрытия.")
        return

    number = await ticket_service.close_active_ticket(
        telegram_id=call.from_user.id,
        queue=QueueType(queue_raw),
        correlation_id=correlation_id,
    )
    if number is None:
        # Check if already closed by agent
        synced = await ticket_service.sync_status_by_queue(call.from_user.id, QueueType(queue_raw))
        if synced and synced[2]:  # was_closed=True
            await state.set_state(UserFlow.main_menu)
            await call.message.answer(
                f"🔴 Тикет <b>#{synced[0]}</b> уже закрыт менеджером.\n"
                "Если вопрос не решён — создайте новое:",
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(),
            )
            return
        await call.message.answer("Не нашёл активного обращения.")
        return

    await state.set_state(UserFlow.main_menu)
    await call.message.answer(
        f"✅ Тикет #{number} закрыт.\nНужно новое обращение — выберите направление:",
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(F.data == "ticket:list")
async def handle_ticket_list(
    call: CallbackQuery,
    state: FSMContext,
    ticket_service: TicketService,
) -> None:
    if call.message is None or call.from_user is None:
        return
    await call.answer()

    items = await ticket_service.list_recent_tickets(call.from_user.id, limit=8)
    if not items:
        await call.message.answer("Пока нет обращений. Нажмите «🆕 Новое обращение».", reply_markup=main_menu_keyboard())
        return

    lines = ["📂 <b>Ваши обращения</b>:"]
    buttons: list[list[InlineKeyboardButton]] = []

    for item in items:
        queue_label = _queue_label(item["queue"])
        status = item["status"]
        active_mark = " • активный" if item["is_active"] else ""
        status_label = ""
        if status == TicketStatus.open:
            status_label = "🟢 открыт"
        elif status == TicketStatus.new:
            status_label = "🆕 новый"
        elif status == TicketStatus.closed:
            status_label = "🔴 закрыт"
        elif status == TicketStatus.merged:
            status_label = "🔀 объединён"
        else:
            status_label = f"⏳ {status.value}"

        lines.append(f"• #{item['number']} — {queue_label}, {status_label}{active_mark}")

        if status not in (TicketStatus.closed, TicketStatus.merged):
            buttons.append([
                InlineKeyboardButton(
                    text=f"↪️ Выбрать #{item['number']}",
                    callback_data=f"ticket:use:{item['zammad_ticket_id']}",
                )
            ])

    buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")])

    await call.message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await state.set_state(UserFlow.main_menu)


@router.callback_query(F.data.startswith("ticket:use:"))
async def handle_use_ticket(
    call: CallbackQuery,
    state: FSMContext,
    ticket_service: TicketService,
) -> None:
    if call.message is None or call.from_user is None or not call.data:
        return
    await call.answer()

    try:
        zammad_ticket_id = int(call.data.split(":", maxsplit=2)[2])
    except Exception:
        await call.message.answer("Не удалось выбрать обращение.")
        return

    result = await ticket_service.activate_ticket_context(call.from_user.id, zammad_ticket_id)
    if result is None:
        await call.message.answer("Это обращение уже закрыто или недоступно.")
        return

    queue, number = result
    await state.update_data(active_queue=queue.value)
    await state.set_state(UserFlow.in_ticket)

    await call.message.answer(
        f"↪️ Активировано обращение <b>#{number}</b> ({_queue_label(queue)}).\n"
        "Пишите сообщение — отправим именно туда.",
        parse_mode="HTML",
        reply_markup=active_ticket_keyboard(),
    )
