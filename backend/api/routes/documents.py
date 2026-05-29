"""
POST /api/documents/upload  — загрузить документ (txt, pdf, md) → embeddings → pgvector
POST /api/documents/ask     — задать вопрос по документам (RAG)
GET  /api/documents/count   — сколько документов в базе
DELETE /api/documents       — очистить базу документов
"""
from __future__ import annotations

import io
from typing import List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from services.rag_service import rag_service
from services.embedding_service import embedding_service
from services.groq_service import groq_service, TaskType
from utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


class AskRequest(BaseModel):
    question: str
    top_k: int = 5
    session_id: str = "web-default"


class AskResponse(BaseModel):
    answer: str
    sources: List[str]
    question: str


class UploadResponse(BaseModel):
    ok: bool
    doc_id: int
    filename: str
    chunks: int


def _require_rag_db() -> None:
    if not rag_service._pool:
        raise HTTPException(
            status_code=503,
            detail=(
                "База документов недоступна: задайте DATABASE_URL (PostgreSQL + pgvector) "
                "в переменных окружения Railway."
            ),
        )


async def _require_embeddings() -> None:
    st = await embedding_service.status()
    if not st["ready"]:
        raise HTTPException(
            status_code=503,
            detail=(
                "Сервис эмбеддингов недоступен. Локально: запустите Ollama и "
                "ollama pull nomic-embed-text. На Railway: установится fastembed при деплое."
            ),
        )


def _split_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """Разбить текст на чанки с перекрытием."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i: i + chunk_size])
        chunks.append(chunk)
        i += chunk_size - overlap
    return [c for c in chunks if c.strip()]


def _extract_text(content: bytes, filename: str) -> str:
    """Извлечь текст из загруженного файла."""
    name = filename.lower()

    if name.endswith(".pdf"):
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                return "\n".join(p.extract_text() or "" for p in pdf.pages)
        except ImportError:
            # fallback без pdfplumber
            try:
                import pypdf
                reader = pypdf.PdfReader(io.BytesIO(content))
                return "\n".join(p.extract_text() or "" for p in reader.pages)
            except Exception as exc:
                raise HTTPException(status_code=422, detail=f"PDF parse error: {exc}")

    if name.endswith((".txt", ".md", ".rst", ".csv")):
        for enc in ("utf-8", "cp1251", "latin-1"):
            try:
                return content.decode(enc)
            except UnicodeDecodeError:
                continue
        raise HTTPException(status_code=422, detail="Cannot decode text file")

    raise HTTPException(
        status_code=415,
        detail=f"Unsupported file type '{filename}'. Supported: pdf, txt, md, rst, csv",
    )


@router.get("/status")
async def documents_status():
    """Готовность RAG: БД, эмбеддинги, число документов."""
    emb = await embedding_service.status()
    return {
        "database": rag_service._pool is not None,
        "embeddings": emb,
        "count": await rag_service.count(),
    }


@router.post("/upload", response_model=UploadResponse)
async def upload_document(file: UploadFile = File(...)):
    """Загрузить документ, разбить на чанки, сохранить эмбеддинги в pgvector."""
    _require_rag_db()
    await _require_embeddings()

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")

    text = _extract_text(content, file.filename or "file")
    if not text.strip():
        raise HTTPException(status_code=422, detail="Document appears to be empty")

    chunks = _split_text(text)
    logger.info("document_upload", filename=file.filename, chunks=len(chunks))

    ids = []
    for i, chunk in enumerate(chunks):
        embedding = await embedding_service.embed(chunk)
        if not embedding:
            raise HTTPException(
                status_code=503,
                detail="Не удалось построить эмбеддинг для чанка документа.",
            )
        doc_id = await rag_service.add_document(
            content=chunk,
            embedding=embedding,
            metadata={"filename": file.filename, "chunk": i, "total": len(chunks)},
        )
        if doc_id < 0:
            raise HTTPException(
                status_code=503,
                detail="Не удалось сохранить чанк в базу документов.",
            )
        ids.append(doc_id)

    await rag_service.rebuild_index_if_needed()

    return UploadResponse(
        ok=True,
        doc_id=ids[0] if ids else -1,
        filename=file.filename or "",
        chunks=len(ids),
    )


@router.post("/ask", response_model=AskResponse)
async def ask_documents(req: AskRequest):
    """RAG: найти релевантные чанки и ответить на вопрос через Groq."""
    _require_rag_db()
    await _require_embeddings()

    query_embedding = await embedding_service.embed(req.question)
    if not query_embedding:
        raise HTTPException(
            status_code=503,
            detail="Сервис эмбеддингов недоступен — не удалось обработать вопрос.",
        )

    docs = await rag_service.search(query_embedding, top_k=req.top_k)
    doc_count = await rag_service.count()
    if not docs:
        if doc_count == 0:
            return AskResponse(
                answer="Сначала загрузите документ (PDF, TXT или MD).",
                sources=[],
                question=req.question,
            )
        return AskResponse(
            answer=(
                "Документы есть в базе, но поиск не вернул фрагменты. "
                "Попробуйте переформулировать вопрос или загрузите файл заново."
            ),
            sources=[],
            question=req.question,
        )

    context = "\n\n---\n\n".join(content for content, _ in docs)
    sources = [content[:120] + "…" for content, _ in docs]

    messages = [
        {
            "role": "system",
            "content": (
                "Ты ассистент, который отвечает ТОЛЬКО на основе предоставленного контекста. "
                "Если ответа нет в контексте — честно скажи об этом. "
                "Цитируй источники где возможно."
            ),
        },
        {
            "role": "user",
            "content": f"Контекст:\n{context}\n\nВопрос: {req.question}",
        },
    ]

    try:
        answer = await groq_service.chat(
            messages=messages,
            task_type=TaskType.COMPLEX,
            user_id=0,
            user_message=req.question,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return AskResponse(answer=answer, sources=sources, question=req.question)


@router.get("/count")
async def count_documents():
    count = await rag_service.count()
    return {"count": count}


@router.delete("")
async def clear_documents():
    """Очистить все документы из векторной базы."""
    if not rag_service._pool:
        raise HTTPException(status_code=503, detail="RAG service not available")
    async with rag_service._pool.acquire() as conn:
        deleted = await conn.fetchval("DELETE FROM documents RETURNING id")
    logger.info("documents_cleared")
    return {"ok": True, "deleted": deleted or 0}
