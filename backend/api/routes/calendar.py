"""
Google Calendar для веб-интерфейса (OAuth по session_id).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from api.session import session_to_user_id
from config.settings import settings
from services.google_calendar_service import gcal_service

router = APIRouter()


class CalendarStatus(BaseModel):
    connected: bool
    configured: bool
    session_id: str
    user_id: int


class AuthUrlResponse(BaseModel):
    auth_url: str
    session_id: str
    user_id: int


@router.get("/status", response_model=CalendarStatus)
async def calendar_status(session_id: str = Query("web-default")):
    user_id = session_to_user_id(session_id)
    configured = bool(settings.google_client_id and settings.google_client_secret)
    connected = await gcal_service.is_connected(user_id) if configured else False
    return CalendarStatus(
        connected=connected,
        configured=configured,
        session_id=session_id,
        user_id=user_id,
    )


@router.get("/auth-url", response_model=AuthUrlResponse)
async def calendar_auth_url(session_id: str = Query("web-default")):
    if not settings.google_client_id:
        raise HTTPException(
            status_code=503,
            detail="Google Calendar не настроен (GOOGLE_CLIENT_ID в .env)",
        )
    user_id = session_to_user_id(session_id)
    return AuthUrlResponse(
        auth_url=gcal_service.get_auth_url(user_id),
        session_id=session_id,
        user_id=user_id,
    )


@router.post("/disconnect")
async def calendar_disconnect(session_id: str = Query("web-default")):
    user_id = session_to_user_id(session_id)
    await gcal_service.disconnect(user_id)
    return {"ok": True, "session_id": session_id}
