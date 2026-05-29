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

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from config.settings import settings
from services.calendar_events import TIMEZONE, try_create_event_from_text
from services.google_calendar_service import gcal_service
from utils.logging import get_logger

logger = get_logger(__name__)
router = Router(name="calendar")


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
    result = await try_create_event_from_text(uid, message.text or "")
    if not result:
        return False
    if result.get("error") == "create_failed":
        await message.answer(
            "❌ Не удалось создать событие. Попробуйте переподключить календарь через /calendar."
        )
        return False

    start_display = result["start_datetime"][:16].replace("T", " ")
    await message.answer(
        f"📅 Событие создано: <b>{result['summary']}</b>\n"
        f"🕐 {start_display} ({TIMEZONE})\n"
        f"🔗 <a href='{result['event_url']}'>Открыть в Calendar</a>",
        parse_mode="HTML",
    )
    return True