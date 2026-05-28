"""
Задача 18 — Голосовые сообщения через Groq Whisper API.
"""
from __future__ import annotations

import io

from aiogram import Router, F
from aiogram.types import Message

from services.groq_service import groq_service, TaskType
from services.voice_service import voice_service
from utils.logging import get_logger

logger = get_logger(__name__)
router = Router(name="voice")


@router.message(F.voice)
async def handle_voice(message: Message) -> None:
    await message.answer("🎙️ Распознаю речь…")

    try:
        # Download voice file
        voice = message.voice
        file = await message.bot.get_file(voice.file_id)
        byte_io = io.BytesIO()
        await message.bot.download_file(file.file_path, byte_io)
        ogg_bytes = byte_io.getvalue()

        # Transcribe
        transcribed_text = await voice_service.transcribe(ogg_bytes)

        if not transcribed_text.strip():
            await message.answer("❌ Не удалось распознать речь.")
            return

        await message.answer(f"📝 <b>Распознанный текст:</b>\n{transcribed_text}", parse_mode="HTML")

        # Get LLM response for transcribed text
        response = await groq_service.chat(
            messages=[
                {
                    "role": "system",
                    "content": "Ты полезный AI-ассистент. Отвечай на языке пользователя.",
                },
                {"role": "user", "content": transcribed_text},
            ],
            task_type=TaskType.SHORT,
            user_id=message.from_user.id,
            user_message=transcribed_text,
        )
        await message.answer(response)

    except Exception as exc:
        logger.exception("voice_handler_error", error=str(exc))
        await message.answer("❌ Ошибка при обработке голосового сообщения. Попробуйте позже.")
