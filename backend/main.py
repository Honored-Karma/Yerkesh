import asyncio
from aiogram import Bot, Dispatcher
from config import settings
from handlers import admin_router, calendar_router, chat_router, common_router, mcp_router, vision_router, voice_router
from handlers.setup_mcp import setup_mcp
from services import context_store, gcal_service, rag_service, rate_limiter, search_service
from services.oauth_server import start_oauth_server
from services.shutdown import shutdown
from utils import setup_logging, setup_tracing


async def main() -> None:
    setup_logging()
    setup_tracing()
    bot = Bot(token=settings.bot_token)

    # Удаляем вебхук перед стартом polling — иначе Telegram вернёт Conflict-ошибку,
    # если ранее был активен webhook-режим (frontend/webhook_server.py).
    await bot.delete_webhook(drop_pending_updates=False)

    dp = Dispatcher()
    for router in (common_router, voice_router, vision_router, admin_router, mcp_router, calendar_router, chat_router):
        dp.include_router(router)
    await rate_limiter.init()
    await context_store.init()
    await rag_service.init()
    await search_service.init()
    await gcal_service.init()
    await setup_mcp()
    oauth_runner = await start_oauth_server()
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await shutdown(bot, oauth_runner)


if __name__ == "__main__":
    asyncio.run(main())
