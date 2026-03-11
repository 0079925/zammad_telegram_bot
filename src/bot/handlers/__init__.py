from aiogram import Router

from src.bot.handlers import contact, media, message, queue_select, start

# Build the top-level router and include all sub-routers in priority order
main_router = Router(name="main")
main_router.include_router(start.router)
main_router.include_router(contact.router)
main_router.include_router(queue_select.router)
main_router.include_router(media.router)
main_router.include_router(message.router)

__all__ = ["main_router"]
