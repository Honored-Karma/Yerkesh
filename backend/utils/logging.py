"""
Задача 0.4 — Логирование действий пользователя.
Задача 28 — Структурированное логирование (structlog, JSON, correlation request_id).
"""
from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from typing import Any

import structlog

from config.settings import settings

_request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    rid = _request_id_var.get()
    if not rid:
        rid = str(uuid.uuid4())[:8]
        _request_id_var.set(rid)
    return rid


def new_request_id() -> str:
    rid = str(uuid.uuid4())[:8]
    _request_id_var.set(rid)
    return rid


def add_request_id(logger, method, event_dict):
    event_dict["request_id"] = get_request_id()
    return event_dict


def setup_logging() -> None:
    log_level = getattr(logging, settings.log_level, logging.INFO)

    # Настраиваем стандартный logging
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s — %(message)s"
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    try:
        file_handler = RotatingFileHandler(
            settings.log_file, maxBytes=10 * 1024 * 1024,
            backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except Exception:
        pass  # файл недоступен — только stdout

    # structlog через stdlib (совместимо с любой версией)
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            add_request_id,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = __name__) -> Any:
    return structlog.get_logger(name)


def log_user_action(
    user_id: int,
    username: str | None,
    message_text: str,
    response_length: int,
) -> None:
    logger = get_logger("user_actions")
    logger.info(
        "user_message",
        user_id=user_id,
        username=username or "unknown",
        message_preview=message_text[:100],
        response_length=response_length,
    )