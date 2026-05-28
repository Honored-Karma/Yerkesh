"""
Задача 3 — Многоуровневая система rate limiting.
Алгоритм Token Bucket с Redis. Три уровня: per-user, per-chat, global.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import redis.asyncio as aioredis

from config.settings import settings
from utils.logging import get_logger

logger = get_logger(__name__)


class RateLimitLevel(str, Enum):
    USER = "user"
    CHAT = "chat"
    GLOBAL = "global"


@dataclass
class RateLimitResult:
    allowed: bool
    level: Optional[RateLimitLevel] = None
    retry_after: float = 0.0


class TokenBucket:
    """
    Lua-script based Token Bucket in Redis.
    Atomic: no race conditions on concurrent access.
    """

    LUA_SCRIPT = """
    local key = KEYS[1]
    local capacity = tonumber(ARGV[1])
    local refill_rate = tonumber(ARGV[2])
    local now = tonumber(ARGV[3])
    local requested = tonumber(ARGV[4])

    local data = redis.call('HMGET', key, 'tokens', 'last_refill')
    local tokens = tonumber(data[1]) or capacity
    local last_refill = tonumber(data[2]) or now

    local elapsed = now - last_refill
    tokens = math.min(capacity, tokens + elapsed * refill_rate)

    if tokens >= requested then
        tokens = tokens - requested
        redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
        redis.call('EXPIRE', key, 120)
        return {1, tokens}
    else
        redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
        redis.call('EXPIRE', key, 120)
        local wait = (requested - tokens) / refill_rate
        return {0, wait}
    end
    """

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis = redis_client
        self._script = self._redis.register_script(self.LUA_SCRIPT)

    async def consume(
        self,
        key: str,
        capacity: int,
        refill_rate: float,
        tokens: int = 1,
    ) -> tuple[bool, float]:
        """
        Returns (allowed, retry_after_seconds).
        refill_rate = tokens per second.
        """
        now = time.time()
        result = await self._script(
            keys=[key],
            args=[capacity, refill_rate, now, tokens],
        )
        allowed = bool(result[0])
        extra = float(result[1])
        return allowed, (0.0 if allowed else extra)


class RateLimiter:
    """
    Three-level rate limiter:
      - per_user: 10 req/min  → capacity=10, rate=10/60 t/s
      - per_chat: 50 req/min  → capacity=50, rate=50/60 t/s
      - global:   rpm/tpm     → capacity=rpm, rate=rpm/60 t/s
    """

    def __init__(self) -> None:
        self._redis: Optional[aioredis.Redis] = None
        self._bucket: Optional[TokenBucket] = None

    async def init(self) -> None:
        try:
            self._redis = aioredis.from_url(
                settings.redis_url, decode_responses=True
            )
            await self._redis.ping()
            self._bucket = TokenBucket(self._redis)
            logger.info("rate_limiter_ready", redis_url=settings.redis_url)
        except Exception as exc:
            logger.warning("redis_unavailable_rate_limiter_disabled", error=str(exc))
            self._redis = None
            self._bucket = None

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()

    async def check(self, user_id: int, chat_id: int) -> RateLimitResult:
        if not self._bucket:
            # Redis not available — allow all (graceful degradation)
            return RateLimitResult(allowed=True)

        rpm_u = settings.rate_limit_per_user
        rpm_c = settings.rate_limit_per_chat
        rpm_g = settings.rate_limit_global_rpm

        checks = [
            (
                f"rl:user:{user_id}",
                rpm_u,
                rpm_u / 60,
                RateLimitLevel.USER,
            ),
            (
                f"rl:chat:{chat_id}",
                rpm_c,
                rpm_c / 60,
                RateLimitLevel.CHAT,
            ),
            (
                "rl:global",
                rpm_g,
                rpm_g / 60,
                RateLimitLevel.GLOBAL,
            ),
        ]

        for key, capacity, rate, level in checks:
            allowed, retry = await self._bucket.consume(key, capacity, rate)
            if not allowed:
                logger.warning(
                    "rate_limit_hit",
                    level=level,
                    user_id=user_id,
                    chat_id=chat_id,
                    retry_after=round(retry, 1),
                )
                return RateLimitResult(
                    allowed=False, level=level, retry_after=retry
                )

        return RateLimitResult(allowed=True)


# Singleton
rate_limiter = RateLimiter()
