"""
Эмбеддинги для RAG: Ollama (локально) → fastembed (fallback для Railway/облака).
Размерность 768 (nomic-embed-text) совместима с pgvector в rag_service.
"""
from __future__ import annotations

import asyncio
from typing import List, Optional

from services.ollama_service import ollama_service
from utils.logging import get_logger

logger = get_logger(__name__)

FASTEMBED_MODEL = "nomic-ai/nomic-embed-text-v1.5"
OLLAMA_EMBED_TIMEOUT = 8.0


class EmbeddingService:
    def __init__(self) -> None:
        self._fastembed = None
        self._fastembed_failed = False

    def _get_fastembed(self):
        if self._fastembed_failed:
            return None
        if self._fastembed is None:
            try:
                from fastembed import TextEmbedding

                self._fastembed = TextEmbedding(model_name=FASTEMBED_MODEL)
                logger.info("embedding_fastembed_ready", model=FASTEMBED_MODEL)
            except Exception as exc:
                self._fastembed_failed = True
                logger.warning("embedding_fastembed_init_failed", error=str(exc))
                return None
        return self._fastembed

    def _embed_sync_fastembed(self, text: str) -> List[float]:
        model = self._get_fastembed()
        if not model:
            return []
        vectors = list(model.embed([text]))
        if not vectors:
            return []
        vec = vectors[0]
        return vec.tolist() if hasattr(vec, "tolist") else list(vec)

    async def embed(self, text: str) -> List[float]:
        """Вернуть вектор 768d или [] если ни один провайдер недоступен."""
        if not text.strip():
            return []

        try:
            ollama_vec = await ollama_service.embed(
                text, timeout=OLLAMA_EMBED_TIMEOUT
            )
            if ollama_vec:
                return ollama_vec
        except Exception as exc:
            logger.debug("ollama_embed_skipped", error=str(exc))

        try:
            return await asyncio.to_thread(self._embed_sync_fastembed, text)
        except Exception as exc:
            logger.warning("fastembed_embed_failed", error=str(exc))
            return []

    async def status(self) -> dict:
        ollama_ok = await ollama_service.is_available()
        fastembed_ok = self._get_fastembed() is not None
        return {
            "ollama": ollama_ok,
            "fastembed": fastembed_ok,
            "ready": ollama_ok or fastembed_ok,
        }


embedding_service = EmbeddingService()
