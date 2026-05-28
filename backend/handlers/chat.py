"""
Задача 0.3 — Запрос к Groq API.
Задача 0.5 — Обработка ошибок и таймаутов.
Задача 4   — Стриминг с debounce.
Задача 5   — Multi-model router.
Задача 6   — Контекст диалога с автосуммаризацией.
Задача 13  — Приватный режим (Ollama).
Задача 19  — Поиск в интернете.
Задача 21  — Интеграция с Google Calendar (вызов try_create_event_from_message).
"""
from __future__ import annotations

import asyncio
import json
from contextlib import suppress

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config.settings import settings
from services.context_store import context_store
from services.groq_service import groq_service, TaskType, detect_task_type
from services.ollama_service import ollama_service
from services.rate_limiter import rate_limiter
from services.search_service import search_service, SEARCH_TOOL_DEFINITION
from utils.logging import get_logger, new_request_id
from utils.tracing import traced_span

# Импортируем функцию создания события — lazy чтобы избежать циклов
# Задача 21: вызывается из handle_message перед обычным Groq-ответом

logger = get_logger(__name__)
router = Router(name="chat")

SYSTEM_PROMPT = (
    "Ты полезный, вежливый и лаконичный AI-ассистент в Telegram. "
    "Отвечай на языке пользователя. Если не знаешь — честно скажи. "
    "ВАЖНО: ты НЕ умеешь самостоятельно создавать события в Google Calendar, "
    "отправлять напоминания или планировать встречи — это делает отдельный модуль. "
    "Никогда не говори что создал событие, напоминание или встречу — "
    "если событие создано, пользователь уже получил отдельное подтверждение с ссылкой."
)

# Per-user private mode flag stored in memory (production: store in Redis)
_private_mode: dict[int, bool] = {}


@router.message(Command("private"))
async def cmd_private(message: Message) -> None:
    """Задача 13: переключение в приватный режим."""
    uid = message.from_user.id
    if _private_mode.get(uid):
        _private_mode[uid] = False
        await message.answer("🌐 Переключено в облачный режим (Groq).")
    else:
        available = await ollama_service.is_available()
        if not available:
            await message.answer(
                "❌ Ollama недоступен. Убедитесь, что сервер запущен:\n"
                "<code>ollama serve</code>",
                parse_mode="HTML",
            )
            return
        _private_mode[uid] = True
        await message.answer(
            "🔒 <b>Приватный режим включён</b>\n"
            "Все сообщения обрабатываются локально через Ollama.\n"
            "Данные не покидают ваш компьютер.",
            parse_mode="HTML",
        )


@router.message(Command("ask"))
async def cmd_ask(message: Message) -> None:
    """Задача 14: RAG-поиск по базе знаний."""
    from services.rag_service import rag_service

    query = (message.text or "").removeprefix("/ask").strip()
    if not query:
        await message.answer("Использование: /ask <ваш вопрос>")
        return

    await message.answer("🔍 Ищу в базе знаний…")

    try:
        query_embedding = await ollama_service.embed(query)
        docs = await rag_service.search(query_embedding, top_k=5)

        if not docs:
            context_text = "Релевантных документов не найдено."
        else:
            context_text = "\n\n".join(
                f"[Документ {i+1}]: {doc}" for i, (doc, _) in enumerate(docs)
            )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Контекст из базы знаний:\n{context_text}\n\n"
                    f"Вопрос: {query}"
                ),
            },
        ]
        answer = await groq_service.chat(
            messages, task_type=TaskType.COMPLEX,
            user_id=message.from_user.id, user_message=query
        )
        await message.answer(answer)

    except Exception as exc:
        logger.exception("rag_query_failed", error=str(exc))
        await message.answer("❌ Ошибка при поиске. Попробуйте позже.")


@router.message()
async def handle_message(message: Message) -> None:
    """Main message handler — routes to Groq or Ollama."""
    if not message.text:
        return

    uid = message.from_user.id
    chat_id = message.chat.id
    text = message.text
    new_request_id()

    with traced_span("handle_message", user_id=str(uid), chat_id=str(chat_id)):

        # ── Rate limiting ─────────────────────────────────────────────────
        rl = await rate_limiter.check(uid, chat_id)
        if not rl.allowed:
            wait = round(rl.retry_after)
            await message.answer(
                f"⏳ Слишком много запросов. Подождите {wait} сек.\n"
                f"(Лимит: {rl.level})"
            )
            return

        await message.bot.send_chat_action(chat_id=chat_id, action="typing")

        # ── Private mode: Ollama ──────────────────────────────────────────
        if _private_mode.get(uid):
            try:
                response = await ollama_service.chat(text, system=SYSTEM_PROMPT)
                await message.answer(f"🔒 {response}")
            except Exception as exc:
                logger.exception("ollama_chat_failed", error=str(exc))
                await message.answer(
                    "❌ Ollama временно недоступен. Используйте обычный режим."
                )
            return

        # ── Groq mode ─────────────────────────────────────────────────────
        try:
            task_type = detect_task_type(text)
            username = message.from_user.username if message.from_user else None
            first_name = message.from_user.first_name if message.from_user else None

            # Сохраняем пользователя и сообщение в PostgreSQL
            await context_store.upsert_user(uid, username, first_name)
            await context_store.save_message_to_pg(chat_id, uid, "user", text)

            # Add user message to context
            await context_store.add_message(chat_id, "user", text)

            # Build full context
            messages = await context_store.get_context_for_groq(
                chat_id, groq_service.client, SYSTEM_PROMPT
            )

            # ── Google Calendar (Задача 21) ───────────────────────────────
            # Проверяем до стриминга: если сообщение похоже на запрос
            # к календарю — создаём событие и выходим.
            from handlers.calendar_handler import try_create_event_from_message
            calendar_handled = await try_create_event_from_message(message)
            if calendar_handled:
                # Событие создано — сохраняем в контекст и выходим
                await context_store.add_message(chat_id, "assistant", "[Событие создано в Google Calendar]")
                return

            # ── Search decision via function calling (Задача 19) ──────────
            need_search = await _check_if_search_needed(messages, text)
            if need_search:
                search_results = await search_service.search(text)
                if search_results:
                    search_context = search_service.format_for_prompt(search_results)
                    messages[-1]["content"] += f"\n\n{search_context}"

            # Stream response
            sent = await message.answer("💭 …")
            full_response = await groq_service.stream_to_message(
                messages, sent, task_type
            )

            # Сохраняем ответ бота в PostgreSQL
            if full_response:
                await context_store.add_message(chat_id, "assistant", full_response)
                await context_store.save_message_to_pg(
                    chat_id, uid, "assistant", full_response,
                    model_used=settings.groq_model_smart,
                )

        except asyncio.TimeoutError:
            logger.warning("groq_timeout", user_id=uid)
            with suppress(Exception):
                await message.answer(
                    "⏱️ Сервис временно недоступен, попробуйте через минуту."
                )
        except Exception as exc:
            logger.exception("handle_message_error", error=str(exc))
            with suppress(Exception):
                await message.answer(
                    "❌ Произошла ошибка. Попробуйте позже."
                )


async def _check_if_search_needed(messages: list, user_text: str) -> bool:
    """
    Задача 19: function calling to decide if web search is needed.
    Returns True if LLM requests the web_search tool.
    """
    try:
        resp = await groq_service.client.chat.completions.create(
            messages=messages[-3:],  # only recent context
            model=settings.groq_model_fast,
            tools=[SEARCH_TOOL_DEFINITION],
            tool_choice="auto",
            max_tokens=50,
        )
        choice = resp.choices[0]
        return (
            choice.finish_reason == "tool_calls"
            and bool(choice.message.tool_calls)
        )
    except Exception:
        return False