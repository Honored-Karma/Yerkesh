"""
Задача 0.1 — Echo-бот.
Задача 0.2 — Команды /start и /help с HTML-форматированием.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

router = Router(name="common")

HELP_TEXT = (
    "<b>📚 Доступные команды:</b>\n\n"
    "/start — Приветствие\n"
    "/help — Список команд\n"
    "/private — 🔒 Приватный режим (Ollama)\n"
    "/ask [вопрос] — 🔍 RAG-поиск по базе знаний\n"
    "/files — 📁 Просмотр файлов через MCP\n"
    "/benchmark — 📊 Сравнение Groq vs Ollama\n"
    "/imagine [описание] — 🎨 Генерация изображения\n"
    "/calendar — 📅 Подключить Google Calendar\n"
    "/model pull [имя] — ⬇️ Загрузить модель Ollama\n"
    "/clear — 🗑️ Очистить историю диалога\n\n"
    "<i>Просто напишите любой вопрос — я отвечу!</i>"
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Задача 0.2: приветствие по имени."""
    name = message.from_user.first_name if message.from_user else "друг"
    await message.answer(
        f"👋 Привет, <b>{name}</b>!\n\n"
        "Я AI-ассистент на базе Groq. Задай мне любой вопрос, и я отвечу!\n\n"
        "Используй /help для просмотра всех команд.",
        parse_mode="HTML",
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Задача 0.2: /help с HTML-форматированием."""
    await message.answer(HELP_TEXT, parse_mode="HTML")


@router.message(Command("clear"))
async def cmd_clear(message: Message) -> None:
    from services.context_store import context_store
    await context_store.clear(message.chat.id)
    await message.answer("🗑️ История диалога очищена.")