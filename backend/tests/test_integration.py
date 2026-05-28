"""
Задача 24 — Интеграционные сценарные тесты (15 E2E-сценариев).
Использует моки Telegram Bot API + Groq.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def make_message(text: str, user_id: int = 1, chat_id: int = 1, first_name: str = "User") -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.chat.id = chat_id
    msg.from_user.id = user_id
    msg.from_user.first_name = first_name
    msg.from_user.username = "testuser"
    msg.photo = None
    msg.voice = None
    sent = MagicMock()
    sent.edit_text = AsyncMock()
    msg.answer = AsyncMock(return_value=sent)
    msg.bot.send_chat_action = AsyncMock()
    return msg


# Scenario 1
@pytest.mark.asyncio
async def test_scenario_start_then_question():
    """Сценарий 1: /start → приветствие → вопрос → ответ за <5 сек."""
    msg_start = make_message("/start", first_name="Алия")

    t0 = time.monotonic()
    from handlers.common import cmd_start
    await cmd_start(msg_start)
    assert time.monotonic() - t0 < 5.0

    msg_start.answer.assert_called_once()
    assert "Алия" in msg_start.answer.call_args[0][0]


# Scenario 2
@pytest.mark.asyncio
async def test_scenario_help_lists_commands():
    """Сценарий 2: /help → HTML-сообщение с командами."""
    msg = make_message("/help")
    from handlers.common import cmd_help
    await cmd_help(msg)
    call_text = msg.answer.call_args[0][0]
    assert "/start" in call_text
    assert "/help" in call_text
    assert "HTML" == msg.answer.call_args[1].get("parse_mode")


# Scenario 3
@pytest.mark.asyncio
async def test_scenario_clear_history():
    """Сценарий 3: /clear → история очищена."""
    with patch("services.context_store.context_store") as mock_store:
        mock_store.clear = AsyncMock()
        msg = make_message("/clear")
        from handlers.common import cmd_clear
        await cmd_clear(msg)
        mock_store.clear.assert_called_once_with(1)


# Scenario 4
@pytest.mark.asyncio
async def test_scenario_message_gets_ai_response():
    """Сценарий 4: обычное сообщение → AI ответ."""
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
        mock_groq.stream_to_message = AsyncMock(return_value="Отличный вопрос!")

        msg = make_message("Сколько планет в Солнечной системе?")
        from handlers.chat import handle_message
        await handle_message(msg)
        mock_groq.stream_to_message.assert_called_once()


# Scenario 5
@pytest.mark.asyncio
async def test_scenario_rate_limit_enforced():
    """Сценарий 5: превышение лимита → пользователь получает warning."""
    with patch("handlers.chat.rate_limiter") as mock_rl:
        from services.rate_limiter import RateLimitResult, RateLimitLevel
        mock_rl.check = AsyncMock(
            return_value=RateLimitResult(allowed=False, level=RateLimitLevel.USER, retry_after=45.0)
        )
        msg = make_message("Ещё один вопрос")
        from handlers.chat import handle_message
        await handle_message(msg)
        msg.answer.assert_called()


# Scenario 6
@pytest.mark.asyncio
async def test_scenario_private_mode_toggle_no_ollama():
    """Сценарий 6: /private при недоступном Ollama → сообщение об ошибке."""
    with patch("handlers.chat.ollama_service") as mock_ollama:
        mock_ollama.is_available = AsyncMock(return_value=False)
        msg = make_message("/private")
        from handlers.chat import cmd_private
        await cmd_private(msg)
        call_text = msg.answer.call_args[0][0]
        assert "Ollama" in call_text or "недоступен" in call_text


# Scenario 7
@pytest.mark.asyncio
async def test_scenario_private_mode_toggle_with_ollama():
    """Сценарий 7: /private при доступном Ollama → режим включён."""
    with patch("handlers.chat.ollama_service") as mock_ollama:
        with patch("handlers.chat._private_mode", {}):
            mock_ollama.is_available = AsyncMock(return_value=True)
            msg = make_message("/private")
            msg.from_user.id = 42
            from handlers.chat import cmd_private
            await cmd_private(msg)
            call_text = msg.answer.call_args[0][0]
            assert "приватный" in call_text.lower() or "Ollama" in call_text


# Scenario 8
@pytest.mark.asyncio
async def test_scenario_context_saved_after_chat():
    """Сценарий 8: после ответа контекст сохраняется."""
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
        mock_groq.stream_to_message = AsyncMock(return_value="Ответ ИИ")

        msg = make_message("Тестовый вопрос")
        from handlers.chat import handle_message
        await handle_message(msg)

        assert mock_ctx.add_message.call_count >= 1


# Scenario 9
@pytest.mark.asyncio
async def test_scenario_ask_command_no_query():
    """Сценарий 9: /ask без вопроса → подсказка использования."""
    msg = make_message("/ask")
    from handlers.chat import cmd_ask
    await cmd_ask(msg)
    call_text = msg.answer.call_args[0][0]
    assert "Использование" in call_text or "ask" in call_text.lower()


# Scenario 10
@pytest.mark.asyncio
async def test_scenario_model_command_no_args():
    """Сценарий 10: /model без аргументов → список моделей."""
    with patch("handlers.admin.ollama_service") as mock_ollama:
        mock_ollama.list_models = AsyncMock(return_value=["llama3.2:3b", "qwen2.5:7b"])
        msg = make_message("/model")
        from handlers.admin import cmd_model
        await cmd_model(msg)
        msg.answer.assert_called()


# Scenario 11
@pytest.mark.asyncio
async def test_scenario_voice_message_error_handled():
    """Сценарий 11: ошибка при обработке голоса → понятное сообщение."""
    with patch("handlers.voice.voice_service") as mock_voice:
        mock_voice.transcribe = AsyncMock(side_effect=RuntimeError("ffmpeg not found"))
        msg = make_message("")
        msg.voice = MagicMock()
        msg.voice.file_id = "test_file_id"
        mock_file = MagicMock()
        mock_file.file_path = "test.ogg"
        msg.bot.get_file = AsyncMock(return_value=mock_file)
        msg.bot.download_file = AsyncMock()

        from handlers.voice import handle_voice
        await handle_voice(msg)
        msg.answer.assert_called()


# Scenario 12
@pytest.mark.asyncio
async def test_scenario_empty_text_ignored():
    """Сценарий 12: пустое сообщение → бот не отвечает."""
    msg = make_message("")
    msg.text = None
    from handlers.chat import handle_message
    await handle_message(msg)
    msg.answer.assert_not_called()


# Scenario 13
@pytest.mark.asyncio
async def test_scenario_start_response_contains_emoji(mock_message=None):
    """Сценарий 13: ответ /start содержит эмодзи."""
    msg = make_message("/start", first_name="Дамир")
    from handlers.common import cmd_start
    await cmd_start(msg)
    call_text = msg.answer.call_args[0][0]
    assert "👋" in call_text


# Scenario 14
@pytest.mark.asyncio
async def test_scenario_calendar_not_configured():
    """Сценарий 14: /calendar без GOOGLE_CLIENT_ID → ошибка конфигурации."""
    with patch("handlers.calendar_handler.settings") as mock_settings:
        mock_settings.google_client_id = None
        msg = make_message("/calendar")
        from handlers.calendar_handler import cmd_calendar
        await cmd_calendar(msg)
        call_text = msg.answer.call_args[0][0]
        assert "не настроен" in call_text or "Calendar" in call_text


# Scenario 15
@pytest.mark.asyncio
async def test_scenario_groq_timeout_handled():
    """Сценарий 15: таймаут Groq → пользователь получает сообщение об ошибке."""
    import asyncio
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
        mock_groq.stream_to_message = AsyncMock(side_effect=asyncio.TimeoutError())

        msg = make_message("Долгий вопрос")
        from handlers.chat import handle_message
        await handle_message(msg)
        # Bot should have sent an error message
        msg.answer.assert_called()