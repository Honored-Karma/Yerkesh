"""
Задача 18 — Голосовые сообщения через Groq Whisper API.
Конвертация .ogg → .wav через ffmpeg.
Автоматически ищет ffmpeg в стандартных путях Windows.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import List

from config.settings import settings
from utils.logging import get_logger

logger = get_logger(__name__)

MAX_FILE_SIZE = 24 * 1024 * 1024
CHUNK_DURATION_SEC = 280

# Стандартные пути ffmpeg на Windows
FFMPEG_CANDIDATES = [
    "ffmpeg",                          # если прописан в PATH
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
]


def _find_ffmpeg() -> str:
    """Найти ffmpeg — в PATH или по стандартным путям Windows."""
    # shutil.which проверяет PATH
    found = shutil.which("ffmpeg")
    if found:
        return found
    # Проверяем фиксированные пути
    for path in FFMPEG_CANDIDATES[1:]:
        if os.path.isfile(path):
            return path
    return "ffmpeg"  # fallback — выдаст понятную ошибку при вызове


FFMPEG = _find_ffmpeg()


class VoiceService:
    def __init__(self) -> None:
        from groq import AsyncGroq
        self.client = AsyncGroq(api_key=settings.groq_api_key)
        logger.info("voice_service_init", ffmpeg_path=FFMPEG)

    async def _convert_ogg_to_wav(self, input_path: str, output_path: str) -> None:
        """Конвертация .ogg/opus → .wav через ffmpeg."""
        if not os.path.isfile(FFMPEG) and FFMPEG != "ffmpeg":
            raise FileNotFoundError(
                f"ffmpeg не найден. Скачайте с https://www.gyan.dev/ffmpeg/builds/ "
                f"и распакуйте в C:\\ffmpeg"
            )
        proc = await asyncio.create_subprocess_exec(
            FFMPEG, "-y", "-i", input_path,
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            output_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                "ffmpeg не смог конвертировать файл. "
                "Убедитесь что ffmpeg установлен: ffmpeg -version"
            )

    async def _split_audio(self, wav_path: str) -> List[str]:
        tmpdir = tempfile.mkdtemp()
        pattern = os.path.join(tmpdir, "chunk_%03d.wav")
        proc = await asyncio.create_subprocess_exec(
            FFMPEG, "-y", "-i", wav_path,
            "-f", "segment",
            "-segment_time", str(CHUNK_DURATION_SEC),
            "-c", "copy",
            pattern,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        chunks = sorted(Path(tmpdir).glob("chunk_*.wav"))
        return [str(c) for c in chunks]

    async def transcribe(self, ogg_bytes: bytes) -> str:
        """bytes → wav → Groq Whisper → текст."""
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(ogg_bytes)
            ogg_path = f.name

        wav_path = ogg_path.replace(".ogg", ".wav")

        try:
            await self._convert_ogg_to_wav(ogg_path, wav_path)

            if os.path.getsize(wav_path) > MAX_FILE_SIZE:
                chunks = await self._split_audio(wav_path)
            else:
                chunks = [wav_path]

            results = []
            for chunk_path in chunks:
                with open(chunk_path, "rb") as audio_file:
                    transcription = await self.client.audio.transcriptions.create(
                        file=audio_file,
                        model=settings.groq_model_whisper,
                        response_format="text",
                        language="ru",
                    )
                results.append(transcription)

            return " ".join(results)

        except Exception as exc:
            logger.exception("voice_transcription_failed", error=str(exc))
            raise
        finally:
            for path in [ogg_path, wav_path]:
                try:
                    os.unlink(path)
                except OSError:
                    pass


# Singleton
voice_service = VoiceService()