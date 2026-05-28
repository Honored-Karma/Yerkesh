"""
Задача 25 — Property-based тестирование через Hypothesis.
Тесты для парсинга команд, валидации входа, нормализации текста.
"""
from __future__ import annotations

import re
from hypothesis import given, settings, assume
from hypothesis import strategies as st


# ──────────────────────────────────────────────────────────────────────────────
# Helper functions under test
# ──────────────────────────────────────────────────────────────────────────────

def normalize_text(text: str) -> str:
    """Remove extra whitespace, normalize unicode."""
    if not isinstance(text, str):
        raise TypeError("Expected str")
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    return text


def extract_command_args(text: str) -> tuple[str, str]:
    """Extract command name and args from '/command args'."""
    if not text.startswith("/"):
        return "", text
    parts = text.split(None, 1)
    cmd = parts[0].lstrip("/").lower()
    args = parts[1] if len(parts) > 1 else ""
    return cmd, args


def is_valid_user_id(user_id: int) -> bool:
    return isinstance(user_id, int) and 0 < user_id < 10**12


def truncate_for_telegram(text: str, max_len: int = 4096) -> str:
    """Truncate text to Telegram's max message length."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def count_tokens_approx(text: str) -> int:
    """Approximate token count (1 token ≈ 4 chars)."""
    return max(1, len(text) // 4)


# ──────────────────────────────────────────────────────────────────────────────
# Property-based tests
# ──────────────────────────────────────────────────────────────────────────────

@given(st.text(max_size=4096))
def test_normalize_text_never_crashes(text: str) -> None:
    """Задача 25: нормализация не должна падать на любой строке."""
    result = normalize_text(text)
    assert isinstance(result, str)


@given(st.text(max_size=4096))
def test_normalize_text_returns_string(text: str) -> None:
    """Нормализация всегда возвращает строку."""
    assert isinstance(normalize_text(text), str)


@given(st.text(max_size=4096))
def test_normalize_text_idempotent(text: str) -> None:
    """Двойная нормализация === одинарная (идемпотентность)."""
    once = normalize_text(text)
    twice = normalize_text(once)
    assert once == twice


@given(st.text(min_size=1, max_size=4096))
def test_truncate_never_exceeds_limit(text: str) -> None:
    """Задача 25: усечённый текст никогда не превышает лимит."""
    result = truncate_for_telegram(text)
    assert len(result) <= 4096


@given(st.text(max_size=4096))
def test_truncate_never_crashes(text: str) -> None:
    """truncate_for_telegram не падает ни на какой строке."""
    result = truncate_for_telegram(text)
    assert isinstance(result, str)


@given(st.text(max_size=4096))
def test_truncate_preserves_short_text(text: str) -> None:
    """Текст короче лимита сохраняется без изменений."""
    assume(len(text) <= 4090)
    assert truncate_for_telegram(text) == text


@given(st.text(max_size=4096))
def test_extract_command_never_crashes(text: str) -> None:
    """extract_command_args не должна падать ни на чём."""
    cmd, args = extract_command_args(text)
    assert isinstance(cmd, str)
    assert isinstance(args, str)


@given(
    st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Ll", "Lu")))
)
def test_extract_command_lowercases(command: str) -> None:
    """Команды приводятся к нижнему регистру."""
    text = f"/{command} some args"
    cmd, _ = extract_command_args(text)
    assert cmd == command.lower()


@given(st.integers())
def test_is_valid_user_id_never_crashes(uid: int) -> None:
    """is_valid_user_id не должна падать."""
    result = is_valid_user_id(uid)
    assert isinstance(result, bool)


@given(st.integers(min_value=1, max_value=10**11))
def test_valid_user_ids_accepted(uid: int) -> None:
    """Корректные user_id принимаются."""
    assert is_valid_user_id(uid) is True


@given(st.integers(max_value=0))
def test_nonpositive_user_ids_rejected(uid: int) -> None:
    """Нулевые и отрицательные user_id отклоняются."""
    assert is_valid_user_id(uid) is False


@given(st.text(max_size=10000))
@settings(max_examples=200)
def test_count_tokens_always_positive(text: str) -> None:
    """Подсчёт токенов всегда возвращает положительное число."""
    assert count_tokens_approx(text) >= 1


@given(st.text(max_size=4096))
def test_normalize_removes_leading_trailing_whitespace(text: str) -> None:
    """После нормализации нет ведущих/хвостовых пробелов."""
    result = normalize_text(text)
    assert result == result.strip()