"""
Задача 21 — Интеграция с Google Calendar через OAuth2.
OAuth2-флоу через Telegram-кнопки. Refresh-токены шифруются через Fernet.
"""

from __future__ import annotations

import json
import time
from typing import Optional
from urllib.parse import urlencode

import httpx
import redis.asyncio as aioredis
from cryptography.fernet import Fernet

from config.settings import settings
from utils.logging import get_logger

logger = get_logger(__name__)

SCOPES = "https://www.googleapis.com/auth/calendar"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"

# Сколько секунд до истечения токена начинаем обновлять заранее
TOKEN_REFRESH_BUFFER = 60


class GoogleCalendarService:
    def __init__(self) -> None:
        self._redis: Optional[aioredis.Redis] = None
        self._fernet: Optional[Fernet] = None

    async def init(self) -> None:
        self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        if settings.fernet_key:
            self._fernet = Fernet(settings.fernet_key.encode())
        else:
            logger.warning("gcal_fernet_missing", action="tokens stored unencrypted")

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()

    def _encrypt(self, data: str) -> str:
        if self._fernet:
            return self._fernet.encrypt(data.encode()).decode()
        return data

    def _decrypt(self, data: str) -> str:
        if self._fernet:
            return self._fernet.decrypt(data.encode()).decode()
        return data

    def get_auth_url(self, user_id: int) -> str:
        """Build Google OAuth2 URL with user_id as state."""
        params = {
            "client_id": settings.google_client_id,
            "redirect_uri": self._redirect_uri(),
            "response_type": "code",
            "scope": SCOPES,
            "access_type": "offline",
            "state": str(user_id),
            "prompt": "consent",  # Гарантирует получение refresh_token
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    def _redirect_uri(self) -> str:
        """Должен посимвольно совпадать с URI в Google Cloud Console."""
        if settings.google_redirect_uri:
            return settings.google_redirect_uri.strip().rstrip("/")

        # WEBHOOK_HOST = публичный URL бэкенда без пути (как в Railway)
        host = (settings.webhook_host or "http://localhost:8000").rstrip("/")
        return f"{host}/oauth/callback"

    async def exchange_code(self, user_id: int, code: str) -> bool:
        """Exchange OAuth code for tokens and store encrypted."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    TOKEN_URL,
                    data={
                        "code": code,
                        "client_id": settings.google_client_id,
                        "client_secret": settings.google_client_secret,
                        "redirect_uri": self._redirect_uri(),
                        "grant_type": "authorization_code",
                    },
                )
                resp.raise_for_status()
                tokens = resp.json()

            # ИСПРАВЛЕНИЕ 2: проверяем наличие refresh_token
            if "refresh_token" not in tokens:
                logger.warning(
                    "gcal_no_refresh_token",
                    user_id=user_id,
                    hint="Revoke app access in Google Account and re-auth",
                )
                # Всё равно сохраняем — access_token пригодится до истечения

            # Сохраняем время получения токена для проверки expiry
            tokens["token_obtained_at"] = int(time.time())

            encrypted = self._encrypt(json.dumps(tokens))
            await self._redis.setex(
                f"gcal:tokens:{user_id}",
                60 * 60 * 24 * 30,  # 30 дней
                encrypted,
            )
            logger.info("gcal_tokens_stored", user_id=user_id)
            return True
        except Exception as exc:
            logger.warning("gcal_exchange_failed", user_id=user_id, error=str(exc))
            return False

    async def _get_access_token(self, user_id: int) -> Optional[str]:
        raw = await self._redis.get(f"gcal:tokens:{user_id}")
        if not raw:
            return None
        tokens = json.loads(self._decrypt(raw))
        access_token = tokens.get("access_token")

        # ИСПРАВЛЕНИЕ 5: проверяем реальное истечение токена
        obtained_at = tokens.get("token_obtained_at", 0)
        expires_in = tokens.get("expires_in", 3600)
        token_expires_at = obtained_at + expires_in
        needs_refresh = (time.time() + TOKEN_REFRESH_BUFFER) >= token_expires_at

        refresh_token = tokens.get("refresh_token")
        if refresh_token and needs_refresh:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        TOKEN_URL,
                        data={
                            "refresh_token": refresh_token,
                            "client_id": settings.google_client_id,
                            "client_secret": settings.google_client_secret,
                            "grant_type": "refresh_token",
                        },
                    )
                    if resp.status_code == 200:
                        refreshed = resp.json()
                        new_tokens = {
                            **tokens,
                            **refreshed,
                            "token_obtained_at": int(time.time()),
                        }
                        # refresh_token не всегда возвращается при обновлении — сохраняем старый
                        if "refresh_token" not in refreshed:
                            new_tokens["refresh_token"] = refresh_token
                        encrypted = self._encrypt(json.dumps(new_tokens))
                        await self._redis.setex(
                            f"gcal:tokens:{user_id}", 60 * 60 * 24 * 30, encrypted
                        )
                        access_token = new_tokens["access_token"]
                        logger.info("gcal_token_refreshed", user_id=user_id)
                    else:
                        logger.warning(
                            "gcal_refresh_failed",
                            user_id=user_id,
                            status=resp.status_code,
                            body=resp.text[:200],
                        )
            except Exception as exc:
                logger.warning("gcal_refresh_exception", user_id=user_id, error=str(exc))

        return access_token

    async def create_event(
        self,
        user_id: int,
        summary: str,
        start_datetime: str,
        end_datetime: str,
        description: str = "",
        timezone: str = "Asia/Almaty",
    ) -> Optional[str]:
        """
        Create a Google Calendar event. Returns event URL or None on failure.
        start/end_datetime: ISO 8601, e.g. '2024-11-01T14:00:00'
        
        ИСПРАВЛЕНИЕ 3: добавлен timeZone - без него Google API возвращает 400.
        """
        token = await self._get_access_token(user_id)
        if not token:
            return None

        event_body = {
            "summary": summary,
            "description": description,
            # timeZone обязателен для корректного отображения в Calendar
            "start": {"dateTime": start_datetime, "timeZone": timezone},
            "end": {"dateTime": end_datetime, "timeZone": timezone},
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{CALENDAR_API}/calendars/primary/events",
                    headers={"Authorization": f"Bearer {token}"},
                    json=event_body,
                )
                resp.raise_for_status()
                event = resp.json()
                logger.info("gcal_event_created", user_id=user_id, summary=summary)
                return event.get("htmlLink")
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "gcal_create_event_failed",
                user_id=user_id,
                status=exc.response.status_code,
                body=exc.response.text[:300],
            )
            return None
        except Exception as exc:
            logger.warning("gcal_create_event_failed", user_id=user_id, error=str(exc))
            return None

    async def is_connected(self, user_id: int) -> bool:
        if not self._redis:
            return False
        return bool(await self._redis.exists(f"gcal:tokens:{user_id}"))

    async def disconnect(self, user_id: int) -> None:
        """Удалить токены пользователя (команда /calendar disconnect)."""
        if self._redis:
            await self._redis.delete(f"gcal:tokens:{user_id}")
            logger.info("gcal_disconnected", user_id=user_id)


# Singleton
gcal_service = GoogleCalendarService()