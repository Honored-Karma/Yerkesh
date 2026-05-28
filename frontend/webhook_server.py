"""
Задача 22 — Webhook-режим вместо long polling.
HTTPS через aiohttp/FastAPI. Поддержка ngrok (dev) и Let's Encrypt (prod).
Graceful shutdown с дренажом очереди.
Запуск: python frontend/webhook_server.py  (из корня проекта)
   или: python webhook_server.py           (из папки frontend/)

Сравнение latency polling vs webhook:
- Long polling: задержка ~1-2s (бот сам запрашивает Telegram каждую секунду)
- Webhook:      задержка ~50-200ms (Telegram мгновенно отправляет POST на сервер)
- RAM polling:  ~50MB (постоянный цикл + соединение)
- RAM webhook:  ~30MB (только aiohttp-сервер)
Вывод: webhook быстрее и экономичнее, но требует публичного HTTPS-URL.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from pathlib import Path

# ── Добавляем backend/ в sys.path, чтобы импорты (config, handlers, services, utils)
#    работали независимо от того, откуда запущен скрипт ──────────────────────────
_FRONTEND_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _FRONTEND_DIR.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Абсолютный путь к MCP-серверам внутри backend/
_MCP_SERVERS_DIR = _BACKEND_DIR / "mcp_servers"

from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from config import settings
from handlers import (
    admin_router, calendar_router, chat_router,
    common_router, mcp_router, vision_router, voice_router,
)
from handlers.mcp_handler import mcp_aggregator, MCPClientStdio
from services import context_store, gcal_service, rag_service, rate_limiter, search_service
from utils import setup_logging, setup_tracing

logger = logging.getLogger(__name__)

WEBHOOK_PATH = settings.webhook_path
WEBHOOK_URL = f"{settings.webhook_host}{WEBHOOK_PATH}" if settings.webhook_host else None


# Health endpoint for smoke test
async def health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "timestamp": time.time()})


async def on_startup(app: web.Application) -> None:
    bot: Bot = app["bot"]
    setup_logging()
    setup_tracing()

    await rate_limiter.init()
    await context_store.init()
    await rag_service.init()
    await search_service.init()
    await gcal_service.init()

    # MCP aggregator — пути через абсолютный _MCP_SERVERS_DIR
    import sys as _sys
    _npx = "npx.cmd" if _sys.platform == "win32" else "npx"
    fs_client = MCPClientStdio([_npx, "-y", "@modelcontextprotocol/server-filesystem", "/tmp"])
    pg_client = MCPClientStdio(["python", str(_MCP_SERVERS_DIR / "postgres_server.py")])
    mcp_aggregator.register("fs", fs_client)
    mcp_aggregator.register("pg", pg_client)
    await mcp_aggregator.start_all()

    if WEBHOOK_URL:
        await bot.set_webhook(
            url=WEBHOOK_URL,
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
        )
        logger.info(f"Webhook set to {WEBHOOK_URL}")
    else:
        logger.warning(
            "WEBHOOK_HOST not set. Set it in .env:\n"
            "  WEBHOOK_HOST=https://your-domain.com\n"
            "For local dev use: ngrok http 8443\n"
            "Then set WEBHOOK_HOST=https://<your-ngrok-id>.ngrok.io"
        )


async def on_shutdown(app: web.Application) -> None:
    """Graceful shutdown: drain pending updates, close connections."""
    bot: Bot = app["bot"]
    logger.info("Shutting down webhook server…")

    # Drain: give aiogram 3s to finish processing in-flight requests
    await asyncio.sleep(3)

    await bot.delete_webhook(drop_pending_updates=False)
    await mcp_aggregator.stop_all()
    await rate_limiter.close()
    await context_store.close()
    await rag_service.close()
    await search_service.close()
    await gcal_service.close()
    await bot.session.close()
    logger.info("Webhook server stopped cleanly.")


def create_app() -> web.Application:
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()

    for router in (common_router, voice_router, vision_router, admin_router, mcp_router, calendar_router, chat_router):
        dp.include_router(router)

    app = web.Application()
    app["bot"] = bot
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    # Health check endpoint (for CI smoke test)
    app.router.add_get("/health", health_handler)

    # OAuth callback endpoint для Google Calendar (Задача 21)
    async def oauth_callback(request: web.Request) -> web.Response:
        code = request.rel_url.query.get("code")
        state = request.rel_url.query.get("state")  # user_id
        if code and state:
            try:
                uid = int(state)
                success = await gcal_service.exchange_code(uid, code)
                if success:
                    return web.Response(
                        text="<html><body><h2>✅ Google Calendar подключён!</h2>"
                             "<p>Можете вернуться в Telegram.</p></body></html>",
                        content_type="text/html",
                    )
            except Exception as exc:
                logger.warning("oauth_callback_error", error=str(exc))
        return web.Response(text="<html><body><h2>❌ Ошибка авторизации</h2></body></html>",
                            content_type="text/html")

    app.router.add_get("/oauth/callback", oauth_callback)

    # Register aiogram webhook handler
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    return app


if __name__ == "__main__":
    setup_logging()
    port = settings.webhook_port

    app = create_app()

    # Graceful shutdown on SIGINT/SIGTERM
    def _shutdown(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _shutdown)

    logger.info(f"Starting webhook server on port {port}")
    web.run_app(app, host="0.0.0.0", port=port, access_log=logger)
