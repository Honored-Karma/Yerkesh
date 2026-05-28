"""
Конфигурация pytest: общие фикстуры, отключение реальных сетевых запросов.
Задача 23: pytest-socket запрещает реальные сетевые вызовы в unit-тестах.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Env stub so Settings() doesn't fail without a real .env ──────────────────
os.environ.setdefault("BOT_TOKEN", "123456789:AABBCCDDEEFFaabbccddeeff-test_token")
os.environ.setdefault("GROQ_API_KEY", "gsk_test_fake_key_for_unit_tests_only")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


# ── Async event loop ──────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def event_loop_policy():
    import asyncio
    return asyncio.DefaultEventLoopPolicy()


# ── Reusable message mock ─────────────────────────────────────────────────────
@pytest.fixture
def mock_message():
    msg = MagicMock()
    msg.text = "Тестовый вопрос"
    msg.chat.id = 12345
    msg.from_user.id = 99999
    msg.from_user.first_name = "Тест"
    msg.from_user.username = "testuser"
    msg.photo = None
    msg.voice = None
    sent = MagicMock()
    sent.edit_text = AsyncMock()
    sent.delete = AsyncMock()
    msg.answer = AsyncMock(return_value=sent)
    msg.answer_photo = AsyncMock()
    msg.answer_document = AsyncMock()
    msg.bot = MagicMock()
    msg.bot.send_chat_action = AsyncMock()
    msg.bot.get_file = AsyncMock(return_value=MagicMock(file_path="test.ogg"))
    msg.bot.download_file = AsyncMock()
    return msg


@pytest.fixture
def mock_groq_client():
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = "Тестовый ответ от AI"
    choice.finish_reason = "stop"
    choice.message.tool_calls = None
    resp = MagicMock()
    resp.choices = [choice]
    client.chat.completions.create = AsyncMock(return_value=resp)
    return client


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.set = AsyncMock(return_value=True)
    r.setex = AsyncMock(return_value=True)
    r.delete = AsyncMock(return_value=1)
    r.exists = AsyncMock(return_value=0)
    r.aclose = AsyncMock()
    return r


# ── Markers ───────────────────────────────────────────────────────────────────
def pytest_configure(config):
    config.addinivalue_line("markers", "slow: mark test as slow (LLM judge, integration)")
    config.addinivalue_line("markers", "e2e: mark test as end-to-end scenario")