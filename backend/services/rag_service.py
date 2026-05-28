"""
Задача 14 — RAG-пайплайн: Ollama embeddings + PostgreSQL/pgvector.
Команда /ask <вопрос> → top-5 документов → Groq для финального ответа.
"""
from __future__ import annotations

import json
from typing import List, Optional, Tuple

from config.settings import settings
from utils.logging import get_logger

logger = get_logger(__name__)

EMBEDDING_DIM = 768  # nomic-embed-text / mxbai-embed-large dimension


class RAGService:
    def __init__(self) -> None:
        self._pool = None

    async def init(self) -> None:
        if not settings.database_url:
            logger.warning("rag_service_disabled", reason="DATABASE_URL not set")
            return
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(settings.database_url)
            await self._ensure_tables()
            logger.info("rag_service_ready")
        except Exception as exc:
            logger.warning("rag_service_init_failed", error=str(exc))

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    async def _ensure_tables(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                CREATE EXTENSION IF NOT EXISTS vector;
                CREATE TABLE IF NOT EXISTS documents (
                    id SERIAL PRIMARY KEY,
                    content TEXT NOT NULL,
                    embedding vector(768),
                    metadata JSONB DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS documents_embedding_idx
                    ON documents USING ivfflat (embedding vector_cosine_ops)
                    WITH (lists = 100);
                """
            )

    async def add_document(
        self,
        content: str,
        embedding: List[float],
        metadata: dict | None = None,
    ) -> int:
        if not self._pool:
            return -1
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO documents (content, embedding, metadata) "
                "VALUES ($1, $2::vector, $3) RETURNING id",
                content,
                json.dumps(embedding),
                json.dumps(metadata or {}),
            )
        return row["id"]

    async def search(
        self, query_embedding: List[float], top_k: int = 5
    ) -> List[Tuple[str, float]]:
        """Return list of (content, similarity_score)."""
        if not self._pool:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT content,
                       1 - (embedding <=> $1::vector) AS similarity
                FROM documents
                ORDER BY embedding <=> $1::vector
                LIMIT $2
                """,
                json.dumps(query_embedding),
                top_k,
            )
        return [(r["content"], float(r["similarity"])) for r in rows]

    async def count(self) -> int:
        if not self._pool:
            return 0
        async with self._pool.acquire() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM documents")

    async def add_documents_bulk(
        self,
        contents: List[str],
        embeddings: List[List[float]],
        metadata_list: List[dict] | None = None,
    ) -> List[int]:
        """Bulk insert for loading 1000+ documents efficiently."""
        if not self._pool:
            return []
        if metadata_list is None:
            metadata_list = [{}] * len(contents)

        ids = []
        async with self._pool.acquire() as conn:
            for content, embedding, meta in zip(contents, embeddings, metadata_list):
                row = await conn.fetchrow(
                    "INSERT INTO documents (content, embedding, metadata) "
                    "VALUES ($1, $2::vector, $3) RETURNING id",
                    content,
                    json.dumps(embedding),
                    json.dumps(meta),
                )
                ids.append(row["id"])
        logger.info("rag_bulk_insert", count=len(ids))
        return ids


# Singleton
rag_service = RAGService()
