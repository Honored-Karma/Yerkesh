"""
Задача 13 — Гибридный режим Groq / Ollama (приватный режим /private).
Задача 14 — RAG-пайплайн на локальных эмбеддингах через Ollama.
Задача 15 — Бенчмарк Groq vs Ollama.
Задача 16 — Function calling в Ollama-моделях.
Задача 17 — Динамическая загрузка моделей (/model pull <name>).
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from config.settings import settings
from utils.logging import get_logger

logger = get_logger(__name__)


class OllamaService:
    def __init__(self) -> None:
        self.base_url = settings.ollama_base_url
        self.default_model = settings.ollama_model

    async def _post(self, path: str, payload: dict, timeout: float = 120.0) -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{self.base_url}{path}", json=payload)
            resp.raise_for_status()
            return resp.json()

    async def _get(self, path: str, timeout: float = 10.0) -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{self.base_url}{path}")
            resp.raise_for_status()
            return resp.json()

    # ──────────────────────────────────────────────────────────────────────
    # Задача 13: Basic chat completion
    # ──────────────────────────────────────────────────────────────────────
    async def chat(
        self,
        prompt: str,
        model: Optional[str] = None,
        system: str = "Ты полезный AI-ассистент.",
    ) -> str:
        model = model or self.default_model
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }
        data = await self._post("/api/chat", payload)
        return data.get("message", {}).get("content", "")

    # ──────────────────────────────────────────────────────────────────────
    # Задача 14: Generate embeddings
    # ──────────────────────────────────────────────────────────────────────
    async def embed(
        self, text: str, model: str = "nomic-embed-text"
    ) -> List[float]:
        payload = {"model": model, "input": text}
        data = await self._post("/api/embed", payload)
        # Ollama returns {"embeddings": [[...]]}
        embeddings = data.get("embeddings", [[]])
        return embeddings[0] if embeddings else []

    # ──────────────────────────────────────────────────────────────────────
    # Задача 15: Benchmark single question
    # ──────────────────────────────────────────────────────────────────────
    async def benchmark_question(
        self, question: str, model: Optional[str] = None
    ) -> Dict[str, Any]:
        model = model or self.default_model
        t0 = time.monotonic()
        response = await self.chat(question, model=model)
        latency = time.monotonic() - t0
        word_count = len(response.split())
        approx_tokens = int(word_count * 1.3)
        return {
            "platform": "ollama",
            "model": model,
            "latency_s": round(latency, 3),
            "response_len": len(response),
            "approx_tokens": approx_tokens,
            "tokens_per_sec": round(approx_tokens / latency, 1) if latency else 0,
        }

    # ──────────────────────────────────────────────────────────────────────
    # Задача 16: Function calling
    # ──────────────────────────────────────────────────────────────────────
    async def chat_with_tools(
        self,
        prompt: str,
        tools: List[dict],
        model: Optional[str] = None,
    ) -> dict:
        """
        Attempt function calling via Ollama tool_call support.
        Returns {"text": ..., "tool_calls": [...], "parse_error": ...}
        """
        model = model or self.default_model
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "tools": tools,
            "stream": False,
        }
        try:
            data = await self._post("/api/chat", payload, timeout=60.0)
            message = data.get("message", {})
            tool_calls = message.get("tool_calls", [])
            text = message.get("content", "")
            return {"text": text, "tool_calls": tool_calls, "parse_error": None}
        except Exception as exc:
            logger.warning("ollama_tool_call_failed", error=str(exc))
            return {"text": "", "tool_calls": [], "parse_error": str(exc)}

    # ──────────────────────────────────────────────────────────────────────
    # Задача 17: Pull model with progress updates
    # ──────────────────────────────────────────────────────────────────────
    async def pull_model_with_progress(
        self, model_name: str
    ) -> AsyncGenerator[str, None]:
        """
        Yields status lines during pull. Caller should update Telegram message every 5s.
        """
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/api/pull",
                    json={"name": model_name},
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            status = data.get("status", "")
                            completed = data.get("completed", 0)
                            total = data.get("total", 0)
                            if total > 0:
                                pct = int(completed / total * 100)
                                yield f"{status}: {pct}%"
                            else:
                                yield status
                        except json.JSONDecodeError:
                            yield line
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 507:  # Insufficient Storage
                yield "❌ Недостаточно места на диске"
            else:
                yield f"❌ Ошибка HTTP {exc.response.status_code}"
        except httpx.ConnectError:
            yield "❌ Ollama недоступен. Убедитесь, что сервер запущен."
        except Exception as exc:
            yield f"❌ Ошибка: {exc}"

    async def list_models(self) -> List[str]:
        try:
            data = await self._get("/api/tags")
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

    async def is_available(self) -> bool:
        try:
            await self._get("/api/tags", timeout=3.0)
            return True
        except Exception:
            return False


# Singleton
ollama_service = OllamaService()
