"""
Задача 19 — Поиск в интернете через Tavily API.
Кэширование результатов на 1 час в Redis.
LLM (function calling) решает, нужен ли поиск.
"""
from __future__ import annotations

import hashlib
import json
from typing import List, Optional

import httpx
import redis.asyncio as aioredis

from config.settings import settings
from utils.logging import get_logger

logger = get_logger(__name__)

CACHE_TTL = 3600  # 1 hour


class SearchService:
    def __init__(self) -> None:
        self._redis: Optional[aioredis.Redis] = None

    async def init(self) -> None:
        self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()

    def _cache_key(self, query: str) -> str:
        h = hashlib.md5(query.lower().strip().encode()).hexdigest()
        return f"search:{h}"

    async def search(self, query: str, max_results: int = 5) -> List[dict]:
        """Returns list of {title, url, snippet}."""
        cache_key = self._cache_key(query)

        # Check cache
        if self._redis:
            cached = await self._redis.get(cache_key)
            if cached:
                logger.info("search_cache_hit", query=query)
                return json.loads(cached)

        results = await self._tavily_search(query, max_results)

        # Store in cache
        if self._redis and results:
            await self._redis.setex(
                cache_key, CACHE_TTL, json.dumps(results, ensure_ascii=False)
            )

        return results

    async def _tavily_search(self, query: str, max_results: int) -> List[dict]:
        if not settings.tavily_api_key:
            return [{"title": "Search unavailable", "url": "", "snippet": "TAVILY_API_KEY not configured"}]

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": settings.tavily_api_key,
                        "query": query,
                        "max_results": max_results,
                        "search_depth": "basic",
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            results = []
            for r in data.get("results", []):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", "")[:400],
                })
            return results

        except Exception as exc:
            logger.warning("tavily_search_failed", error=str(exc))
            return []

    def format_for_prompt(self, results: List[dict]) -> str:
        if not results:
            return "Результаты поиска не найдены."
        lines = ["📌 Результаты поиска:"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. **{r['title']}**\n   {r['snippet']}\n   🔗 {r['url']}")
        return "\n\n".join(lines)


# Groq function-calling tool definition for search
SEARCH_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the internet for current information. Use when asked about recent events, news, prices, or facts that may have changed.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query in the user's language",
                }
            },
            "required": ["query"],
        },
    },
}

# Singleton
search_service = SearchService()
