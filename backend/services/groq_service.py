"""
Задача 4 — Стриминговая выдача ответов (stream=True + edit_message_text с debounce).
Задача 5 — Multi-model router с fallback.
Задача 0.3 — Базовый запрос к Groq API.
Задача 0.5 — Обработка ошибок и таймаутов.
"""
from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import AsyncGenerator, List, Optional

from groq import AsyncGroq, APIConnectionError, APITimeoutError, RateLimitError
from aiogram import Bot
from aiogram.types import Message

from config.settings import settings
from utils.logging import get_logger, log_user_action
from utils.tracing import traced_span

logger = get_logger(__name__)

STREAM_DEBOUNCE = 1.5  # seconds between Telegram edits


class TaskType(str, Enum):
    SHORT = "short"
    COMPLEX = "complex"
    AUDIO = "audio"
    VISION = "vision"


def detect_task_type(text: str) -> TaskType:
    """Simple heuristic to route between fast and smart models."""
    if len(text) < 80:
        return TaskType.SHORT
    keywords_complex = [
        "объясни", "расскажи подробно", "напиши код", "анализ",
        "сравни", "почему", "как работает",
    ]
    if any(kw in text.lower() for kw in keywords_complex):
        return TaskType.COMPLEX
    return TaskType.SHORT


class GroqService:
    def __init__(self) -> None:
        self.client = AsyncGroq(api_key=settings.groq_api_key)

        # Model priority lists for fallback (Задача 5)
        self._model_chain = {
            TaskType.SHORT: [
                settings.groq_model_fast,
                settings.groq_model_smart,
            ],
            TaskType.COMPLEX: [
                settings.groq_model_smart,
                settings.groq_model_fast,
            ],
            TaskType.VISION: [
                settings.groq_model_vision,
                settings.groq_model_smart,
            ],
        }

    def _get_model_chain(self, task_type: TaskType) -> List[str]:
        return self._model_chain.get(task_type, [settings.groq_model_fast])

    async def chat(
        self,
        messages: List[dict],
        task_type: TaskType = TaskType.SHORT,
        user_id: int = 0,
        user_message: str = "",
    ) -> str:
        """
        Задача 5: attempt models in priority order, fallback on errors.
        Задача 0.5: wrap in try/except with timeout.
        """
        chain = self._get_model_chain(task_type)

        for model in chain:
            try:
                with traced_span("groq_chat", model=model, task=task_type):
                    resp = await asyncio.wait_for(
                        self.client.chat.completions.create(
                            messages=messages,
                            model=model,
                            temperature=0.7,
                        ),
                        timeout=settings.groq_timeout,
                    )
                content = resp.choices[0].message.content
                log_user_action(user_id, None, user_message, len(content))
                logger.info("groq_response_ok", model=model, length=len(content))
                return content

            except (APIConnectionError, asyncio.TimeoutError) as exc:
                logger.warning(
                    "groq_model_unavailable",
                    model=model,
                    reason=str(exc),
                    fallback_next=chain[chain.index(model) + 1]
                    if model != chain[-1]
                    else "none",
                )
                if model == chain[-1]:
                    raise
                continue

            except RateLimitError as exc:
                logger.warning("groq_rate_limit", model=model, error=str(exc))
                if model == chain[-1]:
                    raise
                continue

            except Exception:
                logger.exception("groq_unexpected_error", model=model)
                if model == chain[-1]:
                    raise
                continue

        raise RuntimeError("All Groq models failed")

    async def stream_to_message(
        self,
        messages: List[dict],
        sent_message: Message,
        task_type: TaskType = TaskType.SHORT,
    ) -> str:
        """
        Задача 4: stream=True + debounced edit_message_text.
        Updates the Telegram message incrementally, not more than once per DEBOUNCE seconds.
        Returns full accumulated text.
        """
        chain = self._get_model_chain(task_type)
        model = chain[0]

        full_text = ""
        last_edit = 0.0
        buffer = ""

        try:
            async with await self.client.chat.completions.create(
                messages=messages,
                model=model,
                temperature=0.7,
                stream=True,
            ) as stream:
                async for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    buffer += delta
                    full_text += delta

                    now = time.monotonic()
                    if now - last_edit >= STREAM_DEBOUNCE and buffer.strip():
                        try:
                            await sent_message.edit_text(full_text or "…")
                            last_edit = now
                            buffer = ""
                        except Exception:
                            pass  # ignore Telegram edit errors mid-stream

        except Exception as exc:
            logger.warning("stream_interrupted", error=str(exc))
            if not full_text:
                full_text = "Ошибка при получении ответа. Попробуйте ещё раз."

        # Final edit with complete text
        if full_text.strip():
            try:
                await sent_message.edit_text(full_text)
            except Exception:
                pass

        return full_text


# Singleton
groq_service = GroqService()
