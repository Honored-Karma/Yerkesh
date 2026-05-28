"""
GET  /api/agents       — список доступных агентов
GET  /api/agents/{id}  — описание конкретного агента
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class Agent(BaseModel):
    id: str
    name: str
    description: str
    icon: str
    available: bool = True


AGENTS = [
    Agent(
        id="default",
        name="Основной",
        description="Умный универсальный ассистент на базе Llama 3.3 70B",
        icon="🤖",
    ),
    Agent(
        id="search",
        name="Поиск",
        description="Отвечает с актуальными данными из интернета через Tavily",
        icon="🔍",
    ),
    Agent(
        id="rag",
        name="Документы",
        description="Отвечает на основе загруженных тобой документов (RAG)",
        icon="📄",
    ),
    Agent(
        id="vision",
        name="Изображения",
        description="Анализирует фото и генерирует картинки",
        icon="🖼️",
    ),
    Agent(
        id="voice",
        name="Голос",
        description="Транскрибирует аудио через Groq Whisper",
        icon="🎙️",
    ),
    Agent(
        id="calendar",
        name="Календарь",
        description="Создаёт события в Google Calendar",
        icon="📅",
    ),
]


@router.get("", response_model=list[Agent])
async def list_agents():
    return AGENTS


@router.get("/{agent_id}", response_model=Agent)
async def get_agent(agent_id: str):
    for agent in AGENTS:
        if agent.id == agent_id:
            return agent
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
