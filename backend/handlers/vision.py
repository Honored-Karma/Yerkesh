"""
Задача 20 — Генерация изображений (Pollinations.AI) и анализ фото (Groq vision).
Pollinations.AI: бесплатно, без токена, без регистрации.
"""
from __future__ import annotations

import base64
import io
from typing import Optional
from urllib.parse import quote

import httpx
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from config.settings import settings
from utils.logging import get_logger

logger = get_logger(__name__)
router = Router(name="vision")


async def _analyze_image_with_groq(image_b64: str, media_type: str = "image/jpeg") -> str:
    """Use Groq llama-3.2-11b-vision-preview to describe an image."""
    from groq import AsyncGroq
    client = AsyncGroq(api_key=settings.groq_api_key)
    resp = await client.chat.completions.create(
        model=settings.groq_model_vision,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{image_b64}"
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Опиши это изображение подробно на русском языке. "
                            "Если есть текст — выведи его (OCR)."
                        ),
                    },
                ],
            }
        ],
    )
    return resp.choices[0].message.content


async def _generate_image_pollinations(prompt: str) -> Optional[bytes]:
    """
    Generate image via Pollinations.AI — бесплатно, без токена.
    Docs: https://pollinations.ai
    """
    try:
        encoded = quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true"

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            return resp.content  # PNG/JPEG bytes

    except Exception as exc:
        logger.warning("pollinations_generate_failed", error=str(exc))
        return None


@router.message(Command("imagine"))
async def cmd_imagine(message: Message) -> None:
    prompt = (message.text or "").removeprefix("/imagine").strip()
    if not prompt:
        await message.answer("Использование: /imagine <описание изображения>")
        return

    sent = await message.answer("🎨 Генерирую изображение…")

    image_bytes = await _generate_image_pollinations(prompt)
    if image_bytes:
        await message.answer_photo(
            BufferedInputFile(image_bytes, filename="image.jpg"),
            caption=f"🎨 <b>{prompt}</b>",
            parse_mode="HTML",
        )
        await sent.delete()
    else:
        await sent.edit_text("❌ Ошибка генерации. Попробуйте позже.")


@router.message(F.photo)
async def handle_photo(message: Message) -> None:
    """Задача 20: пользователь отправил фото → Groq Vision."""
    await message.answer("🔍 Анализирую изображение…")

    try:
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        byte_io = io.BytesIO()
        await message.bot.download_file(file.file_path, byte_io)

        image_b64 = base64.b64encode(byte_io.getvalue()).decode()
        description = await _analyze_image_with_groq(image_b64)
        await message.answer(
            f"👁️ <b>Анализ изображения:</b>\n\n{description}",
            parse_mode="HTML",
        )

    except Exception as exc:
        logger.exception("photo_handler_error", error=str(exc))
        await message.answer("❌ Ошибка при анализе изображения.")