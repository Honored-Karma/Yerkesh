"""
Создание событий Google Calendar из текста сообщения.
Используется Telegram-ботом и веб-API.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from config.settings import settings
from services.google_calendar_service import gcal_service
from services.groq_service import groq_service
from utils.logging import get_logger

logger = get_logger(__name__)

TIMEZONE = "Asia/Almaty"

CALENDAR_KEYWORDS = (
    "встреч", "запланируй", "назначь", "событие", "remind", "meeting", "созвон",
    "календар", "напомни", "schedule", "appointment",
)

EXTRACT_EVENT_TOOL = {
    "type": "function",
    "function": {
        "name": "create_calendar_event",
        "description": "Extract event details from user message to create a calendar event",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Event title"},
                "start_datetime": {
                    "type": "string",
                    "description": "ISO 8601 start datetime WITHOUT timezone offset",
                },
                "end_datetime": {
                    "type": "string",
                    "description": "ISO 8601 end datetime WITHOUT timezone offset",
                },
                "description": {"type": "string", "description": "Event description"},
            },
            "required": ["summary", "start_datetime", "end_datetime"],
        },
    },
}


def looks_like_calendar_request(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in CALENDAR_KEYWORDS)


async def try_create_event_from_text(user_id: int, text: str) -> Optional[dict[str, Any]]:
    """
    Если пользователь подключил календарь и текст похож на запрос события —
    создаёт событие. Возвращает dict с summary, start_datetime, event_url или None.
    """
    if not await gcal_service.is_connected(user_id):
        return None
    if not looks_like_calendar_request(text):
        return None

    try:
        now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        resp = await groq_service.client.chat.completions.create(
            model=settings.groq_model_fast,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Current datetime: {now_str} (timezone: {TIMEZONE}). "
                        "Extract calendar event details from the user message. "
                        "Return datetime in ISO 8601 format WITHOUT timezone offset."
                    ),
                },
                {"role": "user", "content": text},
            ],
            tools=[EXTRACT_EVENT_TOOL],
            tool_choice={"type": "function", "function": {"name": "create_calendar_event"}},
        )

        choice = resp.choices[0].message
        if not choice.tool_calls:
            logger.warning("gcal_no_tool_call", user_id=user_id, text=text[:100])
            return None

        args = json.loads(choice.tool_calls[0].function.arguments)
        event_url = await gcal_service.create_event(
            user_id,
            summary=args["summary"],
            start_datetime=args["start_datetime"],
            end_datetime=args["end_datetime"],
            description=args.get("description", ""),
            timezone=TIMEZONE,
        )
        if not event_url:
            return {"error": "create_failed"}

        return {
            "summary": args["summary"],
            "start_datetime": args["start_datetime"],
            "event_url": event_url,
        }
    except Exception as exc:
        logger.warning("calendar_event_extract_failed", error=str(exc))
        return None
