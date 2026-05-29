"""
FastAPI приложение — REST API для веб-сайта.
Переиспользует все существующие сервисы бота (groq, rag, search, voice и др.).
Запуск: uvicorn api.app:app --reload  (из папки backend/)
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from api.routes import agents, calendar, chat, documents, image, tools
from handlers.mcp_handler import mcp_aggregator
from handlers.setup_mcp import setup_mcp
from services import context_store, embedding_service, gcal_service, rag_service, rate_limiter, search_service
from utils.logging import setup_logging
from utils.tracing import setup_tracing


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Инициализация и завершение — переиспользуем сервисы бота."""
    setup_logging()
    setup_tracing()
    await rate_limiter.init()
    await context_store.init()
    await rag_service.init()
    await search_service.init()
    await gcal_service.init()
    await setup_mcp()           # ← инициализация MCP агрегатора
    yield
    await rate_limiter.close()
    await context_store.close()
    await rag_service.close()
    await search_service.close()
    await gcal_service.close()
    await mcp_aggregator.stop_all()  # ← graceful shutdown MCP


app = FastAPI(
    title="YerkoBot API",
    description="REST API для веб-интерфейса AI-ассистента",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — разрешаем Vercel и локальный dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # ← открываем для всех; сузь до конкретного домена в проде
    allow_credentials=False,      # ← с allow_origins=["*"] credentials должны быть False
    allow_methods=["*"],
    allow_headers=["*"],
)

# Роуты
app.include_router(chat.router,      prefix="/api/chat",      tags=["Chat"])
app.include_router(agents.router,    prefix="/api/agents",    tags=["Agents"])
app.include_router(calendar.router,  prefix="/api/calendar",  tags=["Calendar"])
app.include_router(image.router,     prefix="/api/image",     tags=["Image"])
app.include_router(tools.router,     prefix="/api/tools",     tags=["MCP Tools"])
app.include_router(documents.router, prefix="/api/documents", tags=["Documents"])


@app.get("/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
):
    """OAuth2 callback для Google Calendar (веб и Telegram используют один redirect URI)."""
    if not code or not state:
        return HTMLResponse(
            "<h2>❌ Ошибка: отсутствуют параметры code или state.</h2>",
            status_code=400,
        )
    try:
        user_id = int(state)
    except ValueError:
        return HTMLResponse("<h2>❌ Ошибка: некорректный state.</h2>", status_code=400)

    success = await gcal_service.exchange_code(user_id, code)
    if success:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;text-align:center;margin-top:80px'>"
            "<h2>✅ Google Calendar подключён!</h2>"
            "<p>Можете вернуться на сайт и создавать события через агента «Календарь».</p>"
            "</body></html>"
        )
    return HTMLResponse(
        "<html><body style='font-family:sans-serif;text-align:center;margin-top:80px'>"
        "<h2>❌ Ошибка авторизации</h2>"
        "<p>Попробуйте подключить календарь снова.</p>"
        "</body></html>",
        status_code=500,
    )


@app.get("/health")
async def health():
    emb = await embedding_service.status()
    return {
        "status": "ok",
        "rag": {
            "database": rag_service._pool is not None,
            "embeddings": emb["ready"],
            "documents": await rag_service.count(),
        },
    }
