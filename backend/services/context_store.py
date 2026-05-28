"""
Задача 6 — Контекст диалога с автосуммаризацией через LLM.
Хранение истории в Redis. Подсчёт токенов через tiktoken.
PostgreSQL-логирование через asyncpg.
"""
from __future__ import annotations

import json
from typing import List, Optional

import asyncpg
import redis.asyncio as aioredis

from config.settings import settings
from utils.logging import get_logger

logger = get_logger(__name__)

MAX_CONTEXT_TOKENS = 6000   # leave headroom below model's 8K limit
SUMMARY_KEEP_LAST = 4       # keep N most recent messages after summarisation


def _count_tokens(messages: List[dict]) -> int:
    """Approximate token count without tiktoken dependency crash."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        total = 0
        for m in messages:
            total += 4  # message overhead
            total += len(enc.encode(m.get("content", "")))
        return total
    except Exception:
        # fallback: 1 token ≈ 4 chars
        return sum(len(m.get("content", "")) // 4 for m in messages)


class ContextStore:
    def __init__(self) -> None:
        self._redis: Optional[aioredis.Redis] = None
        self._pg: Optional[asyncpg.Pool] = None

    async def init(self) -> None:
        # Redis
        self._redis = aioredis.from_url(
            settings.redis_url, decode_responses=True
        )

        # PostgreSQL
        if settings.database_url:
            try:
                self._pg = await asyncpg.create_pool(
                    settings.database_url, min_size=1, max_size=5
                )
                await self._ensure_tables()
                logger.info("pg_pool_ready")
            except Exception as exc:
                logger.warning("pg_pool_failed", error=str(exc))
                self._pg = None
        else:
            logger.warning("pg_not_configured", hint="Set DATABASE_URL in .env")

    async def _ensure_tables(self) -> None:
        """Создать таблицы если не существуют."""
        async with self._pg.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id    BIGINT PRIMARY KEY,
                    username   TEXT,
                    first_name TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id         BIGSERIAL PRIMARY KEY,
                    chat_id    BIGINT NOT NULL,
                    user_id    BIGINT,
                    role       TEXT NOT NULL,
                    content    TEXT NOT NULL,
                    model_used TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
        if self._pg:
            await self._pg.close()

    # ── Redis helpers ──────────────────────────────────────────────────────

    def _key(self, chat_id: int) -> str:
        return f"ctx:{chat_id}"

    async def get_history(self, chat_id: int) -> List[dict]:
        if not self._redis:
            return []
        raw = await self._redis.get(self._key(chat_id))
        if not raw:
            return []
        try:
            return json.loads(raw)
        except Exception:
            return []

    async def save_history(self, chat_id: int, history: List[dict]) -> None:
        if not self._redis:
            return
        await self._redis.setex(
            self._key(chat_id),
            60 * 60 * 24,  # TTL 24 hours
            json.dumps(history, ensure_ascii=False),
        )

    async def add_message(self, chat_id: int, role: str, content: str) -> None:
        history = await self.get_history(chat_id)
        history.append({"role": role, "content": content})
        await self.save_history(chat_id, history)

    async def get_context_for_groq(
        self,
        chat_id: int,
        groq_client,
        system_prompt: str,
    ) -> List[dict]:
        """
        Returns messages list ready for Groq API.
        Auto-summarises old messages if token limit exceeded.
        """
        history = await self.get_history(chat_id)
        token_count = _count_tokens(history)

        if token_count > MAX_CONTEXT_TOKENS and len(history) > SUMMARY_KEEP_LAST:
            logger.info(
                "context_summarisation_triggered",
                chat_id=chat_id,
                token_count=token_count,
            )
            history = await self._summarise(chat_id, history, groq_client)

        return [{"role": "system", "content": system_prompt}] + history

    async def _summarise(
        self, chat_id: int, history: List[dict], groq_client
    ) -> List[dict]:
        """Summarise old messages, keep last N exchanges."""
        old = history[:-SUMMARY_KEEP_LAST]
        recent = history[-SUMMARY_KEEP_LAST:]

        conversation_text = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in old
        )
        summary_prompt = (
            "Кратко суммаризируй следующий диалог в 3–5 предложениях. "
            "Сохрани ключевые факты и предпочтения пользователя.\n\n"
            + conversation_text
        )

        try:
            resp = await groq_client.chat.completions.create(
                messages=[{"role": "user", "content": summary_prompt}],
                model=settings.groq_model_fast,
                temperature=0.3,
                max_tokens=300,
            )
            summary = resp.choices[0].message.content
        except Exception as exc:
            logger.warning("summarisation_failed", error=str(exc))
            summary = "[Предыдущий контекст диалога был сокращён]"

        new_history = [
            {
                "role": "system",
                "content": f"[Краткое содержание предыдущего диалога]: {summary}",
            }
        ] + recent

        await self.save_history(chat_id, new_history)
        return new_history

    async def clear(self, chat_id: int) -> None:
        if self._redis:
            await self._redis.delete(self._key(chat_id))

    # ── PostgreSQL ─────────────────────────────────────────────────────────

    async def upsert_user(
        self,
        user_id: int,
        username: str | None,
        first_name: str | None,
    ) -> None:
        if not self._pg:
            return
        try:
            async with self._pg.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO users (user_id, username, first_name, updated_at)
                    VALUES ($1, $2, $3, NOW())
                    ON CONFLICT (user_id) DO UPDATE
                        SET username   = EXCLUDED.username,
                            first_name = EXCLUDED.first_name,
                            updated_at = NOW()
                    """,
                    user_id, username, first_name,
                )
        except Exception as exc:
            logger.warning("upsert_user_failed", error=str(exc))

    async def save_message_to_pg(
        self,
        chat_id: int,
        user_id: int,
        role: str,
        content: str,
        model_used: str | None = None,
    ) -> None:
        if not self._pg:
            return
        try:
            async with self._pg.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO messages (chat_id, user_id, role, content, model_used)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    chat_id, user_id, role, content, model_used,
                )
        except Exception as exc:
            logger.warning("save_message_to_pg_failed", error=str(exc))


# Singleton
context_store = ContextStore()