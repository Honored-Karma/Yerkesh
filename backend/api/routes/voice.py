"""
POST /api/voice/transcribe — распознавание речи для веб-чата (Groq Whisper).
"""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from services.voice_service import voice_service
from utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()

MAX_UPLOAD = 25 * 1024 * 1024
ALLOWED_SUFFIXES = {
    ".webm", ".wav", ".mp3", ".m4a", ".ogg", ".opus", ".mpeg", ".mpga", ".mp4", ".flac",
}


@router.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """Загрузить аудио с сайта → текст."""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Пустой файл")
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="Файл слишком большой (макс. 25 МБ)")

    name = (file.filename or "audio.webm").lower()
    suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ".webm"
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Формат не поддерживается. Допустимо: {', '.join(sorted(ALLOWED_SUFFIXES))}",
        )

    try:
        text = await voice_service.transcribe_upload(raw, suffix)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail="На сервере нет ffmpeg для .ogg. Запишите голос в браузере (webm) или загрузите wav/mp3.",
        ) from exc
    except Exception as exc:
        logger.exception("web_voice_transcribe_failed", error=str(exc))
        raise HTTPException(status_code=500, detail="Не удалось распознать речь") from exc

    text = (text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Речь не распознана — попробуйте записать ещё раз")

    return {"text": text}
