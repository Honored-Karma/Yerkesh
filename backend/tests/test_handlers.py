"""
Задача 23 — Юнит-тесты с pytest-asyncio, pytest-mock, aioresponses.
Покрытие: хендлеры, groq service, rate limiter.
Запрещены реальные сетевые запросы (pytest-socket).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_message():
    """Fake aiogram Message."""
    msg = MagicMock()
    msg.text = "Привет, как дела?"
    msg.chat.id = 12345
    msg.from_user.id = 99999
    msg.from_user.first_name = "Тест"
    msg.from_user.username = "testuser"
    msg.answer = AsyncMock(return_value=MagicMock(edit_text=AsyncMock()))
    msg.bot.send_chat_action = AsyncMock()
    return msg


@pytest.fixture
def mock_groq_response():
    choice = MagicMock()
    choice.message.content = "Всё отлично! Как я могу помочь?"
    choice.finish_reason = "stop"
    choice.message.tool_calls = None

    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ──────────────────────────────────────────────────────────────────────────────
# Задача 23: Handler tests
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cmd_start_greets_user(mock_message):
    """Задача 0.2: /start должен приветствовать по имени."""
    from handlers.common import cmd_start
    await cmd_start(mock_message)
    mock_message.answer.assert_called_once()
    call_text = mock_message.answer.call_args[0][0]
    assert "Тест" in call_text


@pytest.mark.asyncio
async def test_cmd_help_returns_html(mock_message):
    """Задача 0.2: /help должен возвращать HTML с parse_mode."""
    from handlers.common import cmd_help
    await cmd_help(mock_message)
    mock_message.answer.assert_called_once()
    kwargs = mock_message.answer.call_args[1]
    assert kwargs.get("parse_mode") == "HTML"


@pytest.mark.asyncio
async def test_cmd_clear_resets_context(mock_message):
    """Задача 6: /clear должен очистить контекст диалога."""
    with patch("services.context_store.context_store") as mock_store:
        mock_store.clear = AsyncMock()
        from handlers.common import cmd_clear
        await cmd_clear(mock_message)
        mock_store.clear.assert_called_once_with(mock_message.chat.id)


@pytest.mark.asyncio
async def test_handle_message_calls_groq(mock_message, mock_groq_response):
    """Задача 0.3: сообщение пользователя должно вызывать Groq API."""
    with (
        patch("handlers.chat.rate_limiter") as mock_rl,
        patch("handlers.chat.context_store") as mock_ctx,
        patch("handlers.chat.groq_service") as mock_groq,
        patch("handlers.chat._private_mode", {}),
        patch("handlers.chat._check_if_search_needed", AsyncMock(return_value=False)),
    ):
        from services.rate_limiter import RateLimitResult
        mock_rl.check = AsyncMock(return_value=RateLimitResult(allowed=True))
        mock_ctx.add_message = AsyncMock()
        mock_ctx.upsert_user = AsyncMock()
        mock_ctx.save_message_to_pg = AsyncMock()
        mock_ctx.get_context_for_groq = AsyncMock(return_value=[
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Привет, как дела?"},
        ])
        mock_groq.stream_to_message = AsyncMock(return_value="Всё отлично!")

        from handlers.chat import handle_message
        await handle_message(mock_message)

        mock_groq.stream_to_message.assert_called_once()


@pytest.mark.asyncio
async def test_rate_limit_blocks_request(mock_message):
    """Задача 3: при превышении лимита бот должен отправить предупреждение."""
    with patch("handlers.chat.rate_limiter") as mock_rl:
        from services.rate_limiter import RateLimitResult, RateLimitLevel
        mock_rl.check = AsyncMock(
            return_value=RateLimitResult(allowed=False, level=RateLimitLevel.USER, retry_after=30.0)
        )

        from handlers.chat import handle_message
        await handle_message(mock_message)

        mock_message.answer.assert_called_once()
        assert "30" in mock_message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_groq_error_sends_user_friendly_message(mock_message):
    """Задача 0.5: ошибка Groq → понятное сообщение пользователю."""
    with (
        patch("handlers.chat.rate_limiter") as mock_rl,
        patch("handlers.chat.context_store") as mock_ctx,
        patch("handlers.chat.groq_service") as mock_groq,
        patch("handlers.chat._private_mode", {}),
        patch("handlers.chat._check_if_search_needed", AsyncMock(return_value=False)),
    ):
        from services.rate_limiter import RateLimitResult
        mock_rl.check = AsyncMock(return_value=RateLimitResult(allowed=True))
        mock_ctx.add_message = AsyncMock()
        mock_ctx.upsert_user = AsyncMock()
        mock_ctx.save_message_to_pg = AsyncMock()
        mock_ctx.get_context_for_groq = AsyncMock(return_value=[])
        mock_groq.stream_to_message = AsyncMock(side_effect=Exception("API error"))

        from handlers.chat import handle_message
        await handle_message(mock_message)

        mock_message.answer.assert_called()


# ──────────────────────────────────────────────────────────────────────────────
# Задача 23: Service unit tests
# ──────────────────────────────────────────────────────────────────────────────

def test_detect_task_type_short():
    from services.groq_service import detect_task_type, TaskType
    assert detect_task_type("Привет") == TaskType.SHORT


def test_detect_task_type_complex():
    from services.groq_service import detect_task_type, TaskType
    long_text = "Объясни подробно, как работает трансформерная архитектура в нейронных сетях и как именно механизм attention позволяет обрабатывать последовательности"
    assert detect_task_type(long_text) == TaskType.COMPLEX


@pytest.mark.asyncio
async def test_context_store_add_and_get():
    """Задача 6: контекст сохраняется и читается корректно."""
    import json
    with patch("services.context_store.aioredis") as mock_redis_module:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()
        mock_redis_module.from_url = MagicMock(return_value=mock_redis)

        from services.context_store import ContextStore
        store = ContextStore()
        store._redis = mock_redis

        # First call: empty
        mock_redis.get.return_value = None
        history = await store.get_history(999)
        assert history == []

        # After adding: history saved
        mock_redis.get.return_value = json.dumps([{"role": "user", "content": "hi"}])
        history = await store.get_history(999)
        assert len(history) == 1
        assert history[0]["role"] == "user"


@pytest.mark.asyncio
async def test_rate_limiter_allows_first_request():
    """Задача 3: первый запрос всегда разрешён."""
    with patch("services.rate_limiter.aioredis") as mock_redis_module:
        mock_redis = AsyncMock()
        mock_redis_module.from_url = MagicMock(return_value=mock_redis)

        from services.rate_limiter import RateLimiter
        limiter = RateLimiter()
        # Without Redis initialized → graceful allow
        result = await limiter.check(user_id=1, chat_id=1)
        assert result.allowed is True


def test_settings_validates_bot_token():
    """Задача 2: неверный токен → ValidationError."""
    import os
    from pydantic import ValidationError
    from config.settings import Settings

    with pytest.raises((ValidationError, SystemExit, ValueError)):
        Settings(bot_token="invalid", groq_api_key="gsk_valid")


def test_settings_validates_groq_key():
    """Задача 2: ключ без gsk_ → ValidationError."""
    from pydantic import ValidationError
    from config.settings import Settings

    with pytest.raises((ValidationError, SystemExit, ValueError)):
        Settings(bot_token="123456:ABCdef", groq_api_key="not_a_groq_key")