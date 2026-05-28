"""
FastAPI приложение — REST API для веб-сайта.
Переиспользует все существующие сервисы бота (groq, rag, search, voice и др.).
Запуск: uvicorn api.app:app --reload  (из папки backend/)
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import agents, chat, documents, image, tools
from services import context_store, gcal_service, rag_service, rate_limiter, search_service
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
    yield
    await rate_limiter.close()
    await context_store.close()
    await rag_service.close()
    await search_service.close()
    await gcal_service.close()


app = FastAPI(
    title="YerkoBot API",
    description="REST API для веб-интерфейса AI-ассистента",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — разрешаем Vercel и локальный dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "https://practice-yerko.vercel.app",  # ← точный URL твоего Vercel
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Роуты
app.include_router(chat.router,      prefix="/api/chat",      tags=["Chat"])
app.include_router(agents.router,    prefix="/api/agents",    tags=["Agents"])
app.include_router(image.router,     prefix="/api/image",     tags=["Image"])
app.include_router(tools.router,     prefix="/api/tools",     tags=["MCP Tools"])
app.include_router(documents.router, prefix="/api/documents", tags=["Documents"])


@app.get("/health")
async def health():
    return {"status": "ok"}
