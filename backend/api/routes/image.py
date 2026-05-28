"""
POST /api/image/generate — генерация изображения через Pollinations.AI (бесплатно)
POST /api/image/analyze  — анализ загруженного изображения через Groq Vision
"""
from __future__ import annotations

import base64
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from config.settings import settings
from utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


class GenerateRequest(BaseModel):
    prompt: str
    width: int = 1024
    height: int = 1024
    model: str = "flux"   # flux | turbo | gptimage


class GenerateResponse(BaseModel):
    url: str
    prompt: str


class AnalyzeResponse(BaseModel):
    description: str


@router.post("/generate", response_model=GenerateResponse)
async def generate_image(req: GenerateRequest):
    """Генерация картинки через Pollinations.AI — бесплатно, без токена."""
    encoded = quote(req.prompt)
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={req.width}&height={req.height}&model={req.model}&nologo=true"
    )

    # Проверяем что URL отвечает
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.head(url, follow_redirects=True)
            if resp.status_code >= 400:
                raise HTTPException(status_code=502, detail="Image generation failed")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Image generation timed out")

    logger.info("image_generated", prompt=req.prompt[:50])
    return GenerateResponse(url=url, prompt=req.prompt)


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_image(file: UploadFile = File(...)):
    """Анализ изображения через Groq llama-vision."""
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 10MB)")

    media_type = file.content_type or "image/jpeg"
    image_b64 = base64.b64encode(content).decode()

    try:
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
                            "image_url": {"url": f"data:{media_type};base64,{image_b64}"},
                        },
                        {
                            "type": "text",
                            "text": "Опиши это изображение подробно. Если есть текст — выведи его.",
                        },
                    ],
                }
            ],
        )
        description = resp.choices[0].message.content
    except Exception as exc:
        logger.exception("vision_api_error", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))

    return AnalyzeResponse(description=description)
