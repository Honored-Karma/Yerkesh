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

from api.session import session_to_user_id
from config.settings import settings
from services.calendar_events import looks_like_calendar_request, try_create_event_from_text
from services.context_store import context_store
from services.google_calendar_service import gcal_service
from services.groq_service import groq_service, TaskType, detect_task_type
from services.embedding_service import embedding_service
from services.rag_service import rag_service
from services.search_service import search_service
from utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


SYSTEM_PROMPT = (
    "Ты полезный, вежливый и лаконичный AI-ассистент. "
    "Отвечай на языке пользователя. Если не знаешь — честно скажи."
)

AGENT_PROMPTS = {
    "rag": (
        "Ты отвечаешь на основе контекста из загруженных документов пользователя. "
        "Если ответа нет в контексте — честно скажи."
    ),
    "vision": (
        "Ты ассистент по работе с изображениями. "
        "Для генерации картинок пользователь может открыть раздел «Изображения» на сайте. "
        "Для анализа фото — загрузить файл там же."
    ),
    "voice": (
        "Пользователь может отправить голосовое сообщение — текст придёт уже распознанным. "
        "Отвечай на распознанный текст как на обычное сообщение."
    ),
    "calendar": (
        "Ты помогаешь планировать встречи. Если пользователь просит создать событие — "
        "опиши что будет создано; фактическое создание выполняет система автоматически."
    ),
}


class ChatRequest(BaseModel):
    message: str
    session_id: str = "web-default"
    agent: Optional[str] = "default"
    use_search: bool = False


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    agent: str


async def _build_messages(req: ChatRequest) -> list[dict]:
    """Собрать messages[] с учётом агента, RAG и поиска."""
    user_id = session_to_user_id(req.session_id)
    agent = req.agent or "default"

    system = SYSTEM_PROMPT
    if agent in AGENT_PROMPTS:
        system += "\n\n" + AGENT_PROMPTS[agent]

    history = await context_store.get_history(user_id)
    messages = [{"role": "system", "content": system}]
    messages += history
    user_content = req.message
    messages.append({"role": "user", "content": user_content})

    # RAG-агент: подмешиваем контекст из векторной базы
    if agent == "rag":
        query_embedding = await embedding_service.embed(req.message)
        if query_embedding:
            docs = await rag_service.search(query_embedding, top_k=5)
            if docs:
                context = "\n\n---\n\n".join(content for content, _ in docs)
                messages[-1]["content"] = (
                    f"Контекст из документов:\n{context}\n\nВопрос: {req.message}"
                )
            else:
                messages[-1]["content"] += (
                    "\n\n(В базе документов ничего не найдено — ответь исходя из общих знаний, "
                    "но предупреди что документы пусты или вопрос не по ним.)"
                )
        else:
            messages[-1]["content"] += (
                "\n\n(Сервис эмбеддингов недоступен — RAG временно не работает.)"
            )

    if req.use_search or agent == "search":
        results = await search_service.search(req.message)
        if results:
            snippets = "\n".join(f"- {r['title']}: {r['snippet']}" for r in results[:3])
            messages[-1]["content"] += f"\n\nРезультаты поиска:\n{snippets}"

    return messages


def _format_calendar_success(result: dict) -> str:
    start = result["start_datetime"][:16].replace("T", " ")
    return (
        f"📅 Событие создано: {result['summary']}\n"
        f"🕐 {start}\n"
        f"🔗 {result['event_url']}"
    )


async def _handle_calendar_agent(req: ChatRequest) -> Optional[str]:
    """Календарь: OAuth + создание события. Возвращает готовый ответ или None."""
    user_id = session_to_user_id(req.session_id)
    agent = req.agent or "default"

    if not settings.google_client_id:
        if agent == "calendar":
            return (
                "📅 Google Calendar не настроен на сервере (нет GOOGLE_CLIENT_ID). "
                "Обратитесь к администратору."
            )
        return None

    connected = await gcal_service.is_connected(user_id)

    if agent == "calendar" and not connected:
        auth_url = gcal_service.get_auth_url(user_id)
        return (
            "📅 Чтобы создавать события, подключите Google Calendar:\n"
            f"{auth_url}\n\n"
            "После авторизации повторите запрос, например: "
            "«запланируй встречу завтра в 14:00»."
        )

    if connected and looks_like_calendar_request(req.message):
        result = await try_create_event_from_text(user_id, req.message)
        if result and result.get("event_url"):
            return _format_calendar_success(result)
        if result and result.get("error") == "create_failed":
            return "❌ Не удалось создать событие. Переподключите календарь в настройках."

    return None


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Обычный запрос — возвращает полный ответ."""
    agent = req.agent or "default"

    cal_reply = await _handle_calendar_agent(req)
    if cal_reply:
        sid = session_to_user_id(req.session_id)
        await context_store.add_message(sid, "user", req.message)
        await context_store.add_message(sid, "assistant", cal_reply)
        return ChatResponse(reply=cal_reply, session_id=req.session_id, agent=agent)

    messages = await _build_messages(req)
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

    sid = session_to_user_id(req.session_id)
    await context_store.add_message(sid, "user", req.message)
    await context_store.add_message(sid, "assistant", reply)

    return ChatResponse(reply=reply, session_id=req.session_id, agent=agent)


@router.post("/stream")
async def chat_stream(req: ChatRequest):
    """Стриминг через SSE — каждый чанк отправляется сразу."""
    agent = req.agent or "default"

    async def event_generator() -> AsyncGenerator[str, None]:
        sid = session_to_user_id(req.session_id)

        cal_reply = await _handle_calendar_agent(req)
        if cal_reply:
            await context_store.add_message(sid, "user", req.message)
            await context_store.add_message(sid, "assistant", cal_reply)
            yield f"data: {json.dumps({'delta': cal_reply}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
            return

        try:
            messages = await _build_messages(req)
        except Exception as exc:
            logger.exception("stream_context_load_error", error=str(exc))
            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
            return

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
                        yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.exception("stream_groq_error", error=str(exc))
            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
            return

        try:
            await context_store.add_message(sid, "user", req.message)
            await context_store.add_message(sid, "assistant", full_text)
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
    history = await context_store.get_history(session_to_user_id(session_id))
    return {"session_id": session_id, "messages": history}


@router.delete("/history/{session_id}")
async def clear_history(session_id: str):
    """Очистить историю диалога."""
    await context_store.clear_history(session_to_user_id(session_id))
    return {"ok": True, "session_id": session_id}
