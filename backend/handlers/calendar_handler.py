"""
Задача 21 — Google Calendar через OAuth2. Inline-кнопки для авторизации.
LLM извлекает дату/время из сообщения и создаёт событие.

ИСПРАВЛЕНИЯ:
1. try_create_event_from_message: добавлена обработка случая когда
   tool_calls is None или пустой (LLM может не вызвать инструмент).
2. Добавлена команда /calendar disconnect для отключения аккаунта.
3. Таймзона берётся из константы (Asia/Almaty) и передаётся в create_event.
4. system-промпт уточнён: явно указываем формат ISO 8601 без timezone offset
   (timezone передаётся отдельно через параметр timeZone в API).
"""
from __future__ import annotations

import json
from datetime import datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from config.settings import settings
from services.google_calendar_service import gcal_service
from services.groq_service import groq_service
from utils.logging import get_logger

logger = get_logger(__name__)
router = Router(name="calendar")

TIMEZONE = "Asia/Almaty"

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
                    "description": "ISO 8601 start datetime WITHOUT timezone offset, e.g. '2024-11-01T14:00:00'",
                },
                "end_datetime": {
                    "type": "string",
                    "description": "ISO 8601 end datetime WITHOUT timezone offset (1 hour after start if not specified)",
                },
                "description": {"type": "string", "description": "Event description"},
            },
            "required": ["summary", "start_datetime", "end_datetime"],
        },
    },
}


@router.message(Command("calendar"))
async def cmd_calendar(message: Message) -> None:
    uid = message.from_user.id

    if not settings.google_client_id:
        await message.answer("❌ Google Calendar не настроен (GOOGLE_CLIENT_ID отсутствует в .env).")
        return

    # Поддержка подкоманды: /calendar disconnect
    args = (message.text or "").split(maxsplit=1)
    if len(args) > 1 and args[1].strip().lower() == "disconnect":
        await gcal_service.disconnect(uid)
        await message.answer("🔌 Google Calendar отключён.")
        return

    connected = await gcal_service.is_connected(uid)
    if connected:
        await message.answer(
            "✅ Google Calendar подключён!\n"
            "Напишите, например: «запланируй встречу завтра в 14:00» — и я создам событие.\n\n"
            "Чтобы отключить: /calendar disconnect"
        )
        return

    auth_url = gcal_service.get_auth_url(uid)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔗 Подключить Google Calendar", url=auth_url)
    ]])
    await message.answer(
        "📅 <b>Подключение Google Calendar</b>\n\n"
        "Нажмите кнопку ниже, чтобы авторизоваться через Google:",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


async def try_create_event_from_message(message: Message) -> bool:
    """
    Called from chat handler if message looks like calendar request.
    Returns True if event was created.
    """
    uid = message.from_user.id
    if not await gcal_service.is_connected(uid):
        return False

    calendar_keywords = ["встреч", "запланируй", "назначь", "событие", "remind", "meeting", "созвон"]
    text_lower = (message.text or "").lower()
    if not any(kw in text_lower for kw in calendar_keywords):
        return False

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
                        "Return datetime in ISO 8601 format WITHOUT timezone offset (e.g. 2024-11-01T14:00:00)."
                    ),
                },
                {"role": "user", "content": message.text},
            ],
            tools=[EXTRACT_EVENT_TOOL],
            tool_choice={"type": "function", "function": {"name": "create_calendar_event"}},
        )

        # ИСПРАВЛЕНИЕ 1: защита от None/пустого tool_calls
        choice = resp.choices[0].message
        if not choice.tool_calls:
            logger.warning("gcal_no_tool_call", user_id=uid, text=message.text[:100])
            return False

        tool_call = choice.tool_calls[0]
        args = json.loads(tool_call.function.arguments)
        logger.info("gcal_extracted_event", user_id=uid, args=args)

        event_url = await gcal_service.create_event(
            uid,
            summary=args["summary"],
            start_datetime=args["start_datetime"],
            end_datetime=args["end_datetime"],
            description=args.get("description", ""),
            timezone=TIMEZONE,
        )

        if event_url:
            start_display = args["start_datetime"][:16].replace("T", " ")
            await message.answer(
                f"📅 Событие создано: <b>{args['summary']}</b>\n"
                f"🕐 {start_display} ({TIMEZONE})\n"
                f"🔗 <a href='{event_url}'>Открыть в Calendar</a>",
                parse_mode="HTML",
            )
            return True
        else:
            await message.answer(
                "❌ Не удалось создать событие. Попробуйте переподключить календарь через /calendar."
            )

    except Exception as exc:
        logger.warning("calendar_event_extract_failed", error=str(exc))

    return False