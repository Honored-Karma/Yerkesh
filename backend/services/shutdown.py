"""Graceful shutdown всех сервисов. Вынесено из main.py."""
from aiohttp.web import AppRunner
from aiogram import Bot

from handlers.mcp_handler import mcp_aggregator
from services import context_store, gcal_service, rag_service, rate_limiter, search_service


async def shutdown(bot: Bot, oauth_runner: AppRunner) -> None:
    await oauth_runner.cleanup()
    await mcp_aggregator.stop_all()
    await rate_limiter.close()
    await context_store.close()
    await rag_service.close()
    await search_service.close()
    await gcal_service.close()
    await bot.session.close()