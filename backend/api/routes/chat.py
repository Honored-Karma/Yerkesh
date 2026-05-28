"""
POST /api/chat        — обычный запрос, возвращает JSON
POST /api/chat/stream — стриминг через Server-Sent Events (SSE)
GET  /api/chat/history/{session_id} — история диалога
DELETE /api/chat/history/{session_id} — очистить историю
"""
from __future__ import annotations

import json
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.context_store import context_store
from services.groq_service import groq_service, TaskType, detect_task_type
from services.search_service import search_service, SEARCH_TOOL_DEFINITION
from utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


def _session_to_id(session_id: str) -> int:
    """Конвертируем строковый session_id в int для context_store."""
    return abs(hash(session_id)) % (10 ** 15)


SYSTEM_PROMPT = (
    "Ты полезный, вежливый и лаконичный AI-ассистент. "
    "Отвечай на языке пользователя. Если не знаешь — честно скажи."
)


class ChatRequest(BaseModel):
    message: str
    session_id: str = "web-default"
    agent: Optional[str] = "default"   # default | search | rag | private
    use_search: bool = False


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    agent: str


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Обычный запрос — возвращает полный ответ."""
    history = await context_store.get_history(_session_to_id(req.session_id))
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += history
    messages.append({"role": "user", "content": req.message})

    # Поиск если нужен
    if req.use_search or req.agent == "search":
        results = await search_service.search(req.message)
        if results:
            snippets = "\n".join(f"- {r['title']}: {r['snippet']}" for r in results[:3])
            messages[-1]["content"] += f"\n\nРезультаты поиска:\n{snippets}"

    task_type = detect_task_type(req.message)
    try:
        reply = await groq_service.chat(
            messages=messages,
            task_type=task_type,
            user_id=0,
            user_message=req.message,
        )
    except Exception as exc:
        logger.exception("chat_api_error", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))

    # Сохраняем в контекст
    await context_store.add_message(_session_to_id(req.session_id), "user", req.message)
    await context_store.add_message(_session_to_id(req.session_id), "assistant", reply)

    return ChatResponse(reply=reply, session_id=req.session_id, agent=req.agent or "default")


@router.post("/stream")
async def chat_stream(req: ChatRequest):
    """Стриминг через SSE — каждый чанк отправляется сразу."""

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            history = await context_store.get_history(_session_to_id(req.session_id))
        except Exception as exc:
            logger.exception("stream_context_load_error", error=str(exc))
            history = []

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages += history
        messages.append({"role": "user", "content": req.message})

        if req.use_search or req.agent == "search":
            results = await search_service.search(req.message)
            if results:
                snippets = "\n".join(f"- {r['title']}: {r['snippet']}" for r in results[:3])
                messages[-1]["content"] += f"\n\nРезультаты поиска:\n{snippets}"

        from config.settings import settings
        from groq import AsyncGroq
        client = AsyncGroq(api_key=settings.groq_api_key)

        full_text = ""
        try:
            async with await client.chat.completions.create(
                messages=messages,
                model=settings.groq_model_fast,
                temperature=0.7,
                stream=True,
            ) as stream:
                async for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        full_text += delta
                        data = json.dumps({"delta": delta}, ensure_ascii=False)
                        yield f"data: {data}\n\n"
        except Exception as exc:
            logger.exception("stream_groq_error", error=str(exc))
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            return

        # Сохраняем историю после стрима
        try:
            await context_store.add_message(_session_to_id(req.session_id), "user", req.message)
            await context_store.add_message(_session_to_id(req.session_id), "assistant", full_text)
        except Exception as exc:
            logger.exception("stream_context_save_error", error=str(exc))
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/history/{session_id}")
async def get_history(session_id: str):
    """История диалога для конкретной сессии."""
    history = await context_store.get_history(_session_to_id(session_id))
    return {"session_id": session_id, "messages": history}


@router.delete("/history/{session_id}")
async def clear_history(session_id: str):
    """Очистить историю диалога."""
    await context_store.clear_history(_session_to_id(session_id) if not session_id.isdigit() else int(session_id))
    return {"ok": True, "session_id": session_id}
