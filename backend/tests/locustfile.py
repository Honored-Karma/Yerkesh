"""
Задача 26 — Нагрузочное тестирование с Locust.
500 одновременных пользователей: 70% текст, 20% голос, 10% изображения.
Запуск: locust -f tests/locustfile.py --host=http://localhost:8443 --users=500 --spawn-rate=10
"""
from __future__ import annotations

import base64
import io
import json
import os
import random
import struct
import time
import wave

from locust import HttpUser, TaskSet, between, events, task
from locust.runners import MasterRunner, WorkerRunner

# ── Sample payloads ────────────────────────────────────────────────────────────

TEXT_QUESTIONS = [
    "Что такое Python?",
    "Объясни машинное обучение простыми словами.",
    "Какая столица Казахстана?",
    "Напиши пример кода Hello World на Python.",
    "Чем отличается list от tuple в Python?",
    "Что такое REST API?",
    "Как работает база данных PostgreSQL?",
    "Объясни принцип работы алгоритма сортировки пузырьком.",
    "Что такое Docker и зачем он нужен?",
    "Как написать unit-тест на pytest?",
]


def _make_minimal_ogg_bytes() -> bytes:
    """Generate minimal valid-looking binary payload for voice upload test."""
    # OGG magic bytes + random data (won't actually decode, tests upload path)
    return b"OggS" + os.urandom(256)


def _make_minimal_jpeg_bytes() -> bytes:
    """Minimal valid JPEG header bytes."""
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        + os.urandom(128)
        + b"\xff\xd9"
    )


def _make_telegram_update(update_type: str, chat_id: int, user_id: int) -> dict:
    """Build a fake Telegram update payload."""
    base = {
        "update_id": random.randint(100000, 999999),
        "message": {
            "message_id": random.randint(1, 99999),
            "from": {
                "id": user_id,
                "first_name": f"User{user_id}",
                "username": f"user_{user_id}",
                "is_bot": False,
            },
            "chat": {"id": chat_id, "type": "private"},
            "date": int(time.time()),
        },
    }

    if update_type == "text":
        base["message"]["text"] = random.choice(TEXT_QUESTIONS)

    elif update_type == "voice":
        base["message"]["voice"] = {
            "file_id": f"voice_{random.randint(1, 99999)}",
            "file_unique_id": f"uniq_{random.randint(1, 99999)}",
            "duration": random.randint(2, 30),
            "mime_type": "audio/ogg",
            "file_size": random.randint(5000, 200000),
        }

    elif update_type == "photo":
        base["message"]["photo"] = [
            {
                "file_id": f"photo_{random.randint(1, 99999)}",
                "file_unique_id": f"uniq_{random.randint(1, 99999)}",
                "width": 800,
                "height": 600,
                "file_size": random.randint(50000, 500000),
            }
        ]

    return base


# ── Task Sets ──────────────────────────────────────────────────────────────────

class TextUserTasks(TaskSet):
    """70% of users — send text messages."""

    @task(8)
    def send_question(self) -> None:
        user_id = random.randint(1000, 9999)
        chat_id = user_id
        update = _make_telegram_update("text", chat_id, user_id)
        with self.client.post(
            "/webhook",
            json=update,
            headers={"Content-Type": "application/json"},
            name="/webhook [text]",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 204):
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")

    @task(2)
    def send_start_command(self) -> None:
        user_id = random.randint(1000, 9999)
        chat_id = user_id
        update = _make_telegram_update("text", chat_id, user_id)
        update["message"]["text"] = "/start"
        with self.client.post(
            "/webhook",
            json=update,
            headers={"Content-Type": "application/json"},
            name="/webhook [/start]",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 204):
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")


class VoiceUserTasks(TaskSet):
    """20% of users — send voice messages."""

    @task
    def send_voice(self) -> None:
        user_id = random.randint(10000, 19999)
        chat_id = user_id
        update = _make_telegram_update("voice", chat_id, user_id)
        with self.client.post(
            "/webhook",
            json=update,
            headers={"Content-Type": "application/json"},
            name="/webhook [voice]",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 204):
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")


class PhotoUserTasks(TaskSet):
    """10% of users — send photos."""

    @task
    def send_photo(self) -> None:
        user_id = random.randint(20000, 29999)
        chat_id = user_id
        update = _make_telegram_update("photo", chat_id, user_id)
        with self.client.post(
            "/webhook",
            json=update,
            headers={"Content-Type": "application/json"},
            name="/webhook [photo]",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 204):
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")


class HealthCheckTasks(TaskSet):
    """Periodic health check."""

    @task
    def check_health(self) -> None:
        with self.client.get("/health", name="/health", catch_response=True) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Health check failed: {resp.status_code}")


# ── User types ─────────────────────────────────────────────────────────────────

class TextUser(HttpUser):
    """70% of load — text messages."""
    tasks = [TextUserTasks]
    weight = 70
    wait_time = between(1, 4)


class VoiceUser(HttpUser):
    """20% of load — voice messages."""
    tasks = [VoiceUserTasks]
    weight = 20
    wait_time = between(3, 8)


class PhotoUser(HttpUser):
    """10% of load — photo messages."""
    tasks = [PhotoUserTasks]
    weight = 10
    wait_time = between(5, 15)


# ── Event hooks for reporting ──────────────────────────────────────────────────

_stats: dict = {
    "total_requests": 0,
    "failures": 0,
    "latencies": [],
}


@events.request.add_listener
def on_request(
    request_type, name, response_time, response_length, exception, **kwargs
) -> None:
    _stats["total_requests"] += 1
    _stats["latencies"].append(response_time)
    if exception:
        _stats["failures"] += 1


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs) -> None:
    latencies = sorted(_stats["latencies"])
    if not latencies:
        return

    def pct(p: int) -> float:
        idx = int(len(latencies) * p / 100)
        return latencies[min(idx, len(latencies) - 1)]

    print("\n" + "=" * 50)
    print("📊 НАГРУЗОЧНЫЙ ТЕСТ — РЕЗУЛЬТАТЫ")
    print("=" * 50)
    print(f"  Total requests : {_stats['total_requests']}")
    print(f"  Failures       : {_stats['failures']}")
    print(f"  Error rate     : {_stats['failures'] / max(_stats['total_requests'], 1) * 100:.1f}%")
    print(f"  p50 latency    : {pct(50):.0f} ms")
    print(f"  p95 latency    : {pct(95):.0f} ms")
    print(f"  p99 latency    : {pct(99):.0f} ms")
    print(f"  Avg latency    : {sum(latencies)/len(latencies):.0f} ms")
    print("=" * 50)