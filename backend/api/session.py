"""Вспомогательные функции для веб-сессий."""


def session_to_user_id(session_id: str) -> int:
    """Строковый session_id → числовой id (контекст, OAuth state)."""
    if session_id.isdigit():
        return int(session_id)
    return abs(hash(session_id)) % (10 ** 15)
