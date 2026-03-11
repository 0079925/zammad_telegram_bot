"""
Media / attachment handler.

Supports: photos, documents, audio, video, voice, video_note.
Only active in UserFlow.in_ticket state.

Security checks:
    - File size <= max_attachment_size_bytes
    - MIME type in allowed_content_types
    - Filename sanitized before upload
"""
from __future__ import annotations

import mimetypes
import re
from pathlib import PurePosixPath

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.bot.keyboards import main_menu_keyboard
from src.bot.states import UserFlow
from src.config import get_settings
from src.db.models import QueueType
from src.services.ticket_service import TicketService

router = Router(name="media")
logger = structlog.get_logger(__name__)

_SAFE_FILENAME_RE = re.compile(r"[^\w.\-]")

_TOO_LARGE = (
    "❌ Файл слишком большой. Максимальный размер: {limit_mb} МБ.\n"
    "Отправьте файл меньшего размера."
)
_WRONG_TYPE = (
    "❌ Этот тип файла не поддерживается.\n"
    "Допустимые форматы: изображения, PDF, документы Word/Excel, аудио, видео."
)
_SEND_ERROR = "😔 Не удалось передать файл. Попробуйте позже."


def _sanitize_name(name: str) -> str:
    safe = _SAFE_FILENAME_RE.sub("_", PurePosixPath(name).name)
    return safe[:200] or "file"


async def _process_attachment(
    message: Message,
    state: FSMContext,
    ticket_service: TicketService,
    correlation_id: str,
    *,
    file_id: str,
    filename: str,
    file_size: int | None,
    content_type: str,
) -> None:
    cfg = get_settings()

    # Size check
    if file_size and file_size > cfg.max_attachment_size_bytes:
        limit_mb = cfg.max_attachment_size_bytes // (1024 * 1024)
        await message.answer(_TOO_LARGE.format(limit_mb=limit_mb))
        return

    # Type check
    if content_type not in cfg.allowed_content_types:
        await message.answer(_WRONG_TYPE)
        return

    data = await state.get_data()
    queue_raw = data.get("active_queue")
    if not queue_raw:
        await message.answer(
            "⚠️ Нет активного обращения. Выберите направление:",
            reply_markup=main_menu_keyboard(),
        )
        return

    queue = QueueType(queue_raw)
    user = message.from_user
    if user is None:
        return

    # Download from Telegram
    try:
        bot_file = await message.bot.get_file(file_id)  # type: ignore[union-attr]
        file_bytes = await message.bot.download_file(bot_file.file_path)  # type: ignore[union-attr]
        content = file_bytes.read() if hasattr(file_bytes, "read") else bytes(file_bytes)
    except Exception as exc:
        logger.error("telegram_file_download_failed", error=str(exc), file_id=file_id)
        await message.answer(_SEND_ERROR)
        return

    caption = message.caption or ""
    safe_name = _sanitize_name(filename)

    sent = await ticket_service.add_attachment_article(
        telegram_id=user.id,
        queue=queue,
        caption=caption,
        filename=safe_name,
        content=content,
        content_type=content_type,
        correlation_id=correlation_id,
    )

    if not sent:
        await state.set_state(UserFlow.main_menu)
        await message.answer(
            "ℹ️ Ваше предыдущее обращение закрыто. Выберите направление для нового:",
            reply_markup=main_menu_keyboard(),
        )


# ── Handlers per media type ───────────────────────────────────────────────────

@router.message(UserFlow.in_ticket, F.photo)
async def handle_photo(
    message: Message, state: FSMContext, ticket_service: TicketService, correlation_id: str
) -> None:
    photo = message.photo[-1]  # largest resolution
    await _process_attachment(
        message, state, ticket_service, correlation_id,
        file_id=photo.file_id,
        filename="photo.jpg",
        file_size=photo.file_size,
        content_type="image/jpeg",
    )


@router.message(UserFlow.in_ticket, F.document)
async def handle_document(
    message: Message, state: FSMContext, ticket_service: TicketService, correlation_id: str
) -> None:
    doc = message.document
    if doc is None:
        return
    ct = doc.mime_type or "application/octet-stream"
    await _process_attachment(
        message, state, ticket_service, correlation_id,
        file_id=doc.file_id,
        filename=doc.file_name or "document",
        file_size=doc.file_size,
        content_type=ct,
    )


@router.message(UserFlow.in_ticket, F.voice)
async def handle_voice(
    message: Message, state: FSMContext, ticket_service: TicketService, correlation_id: str
) -> None:
    voice = message.voice
    if voice is None:
        return
    await _process_attachment(
        message, state, ticket_service, correlation_id,
        file_id=voice.file_id,
        filename="voice.ogg",
        file_size=voice.file_size,
        content_type="audio/ogg",
    )


@router.message(UserFlow.in_ticket, F.audio)
async def handle_audio(
    message: Message, state: FSMContext, ticket_service: TicketService, correlation_id: str
) -> None:
    audio = message.audio
    if audio is None:
        return
    ct = audio.mime_type or "audio/mpeg"
    await _process_attachment(
        message, state, ticket_service, correlation_id,
        file_id=audio.file_id,
        filename=audio.file_name or "audio.mp3",
        file_size=audio.file_size,
        content_type=ct,
    )


@router.message(UserFlow.in_ticket, F.video)
async def handle_video(
    message: Message, state: FSMContext, ticket_service: TicketService, correlation_id: str
) -> None:
    video = message.video
    if video is None:
        return
    ct = video.mime_type or "video/mp4"
    await _process_attachment(
        message, state, ticket_service, correlation_id,
        file_id=video.file_id,
        filename=video.file_name or "video.mp4",
        file_size=video.file_size,
        content_type=ct,
    )
