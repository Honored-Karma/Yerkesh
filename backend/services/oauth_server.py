"""
OAuth2 callback HTTP-сервер на aiohttp.
Запускается параллельно с Telegram polling-ботом.
Слушает GET /oauth/callback?code=...&state=<user_id>
"""
from __future__ import annotations

from aiohttp import web

from services.google_calendar_service import gcal_service
from utils.logging import get_logger

logger = get_logger(__name__)

OAUTH_PORT = 8000


async def oauth_callback(request: web.Request) -> web.Response:
    code = request.rel_url.query.get("code")
    state = request.rel_url.query.get("state")  # user_id

    if not code or not state:
        logger.warning("oauth_callback_missing_params", params=dict(request.rel_url.query))
        return web.Response(
            text="<h2>❌ Ошибка: отсутствуют параметры code или state.</h2>",
            content_type="text/html",
            status=400,
        )

    try:
        user_id = int(state)
    except ValueError:
        return web.Response(
            text="<h2>❌ Ошибка: некорректный state.</h2>",
            content_type="text/html",
            status=400,
        )

    success = await gcal_service.exchange_code(user_id, code)

    if success:
        logger.info("oauth_callback_success", user_id=user_id)
        return web.Response(
            text=(
                "<html><body style='font-family:sans-serif;text-align:center;margin-top:80px'>"
                "<h2>✅ Google Calendar успешно подключён!</h2>"
                "<p>Вернитесь в Telegram и начните пользоваться календарём.</p>"
                "</body></html>"
            ),
            content_type="text/html",
        )
    else:
        return web.Response(
            text=(
                "<html><body style='font-family:sans-serif;text-align:center;margin-top:80px'>"
                "<h2>❌ Ошибка авторизации</h2>"
                "<p>Попробуйте снова через /calendar в Telegram.</p>"
                "</body></html>"
            ),
            content_type="text/html",
            status=500,
        )


async def start_oauth_server() -> web.AppRunner:
    """Запустить OAuth-сервер и вернуть runner для последующей остановки."""
    app = web.Application()
    app.router.add_get("/oauth/callback", oauth_callback)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", OAUTH_PORT).start()
    logger.info("oauth_server_started", port=OAUTH_PORT)
    return runner