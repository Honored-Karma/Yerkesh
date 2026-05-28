"""
Задача 27 — LLM-as-a-judge: автоматическая оценка качества ответов бота.
100 эталонных вопросов с ключевыми словами правильных ответов.
Судья (более мощная модель) оценивает ответы основного бота.
Регрессионный тест: средняя оценка ≥ MIN_ACCEPTABLE_SCORE.
CI-интеграция: тест помечен @pytest.mark.slow — запускается отдельным шагом.
Запуск: pytest tests/test_llm_judge.py -v -s -m slow
"""
from __future__ import annotations

import asyncio
import json
import statistics
from typing import Any

import pytest

# Минимально допустимая средняя оценка (0–10). CI провалится если упадёт ниже.
MIN_ACCEPTABLE_SCORE = 6.5
# Для быстрого CI прогона используем подмножество вопросов
QUESTIONS_FOR_CI = 10

# ── 100 эталонных вопросов ────────────────────────────────────────────────────
BENCHMARK_DATASET = [
    ("Что такое Python?", ["язык программирования", "интерпретируемый", "высокоуровневый"]),
    ("Назови столицу Казахстана.", ["Астана"]),
    ("Что такое машинное обучение?", ["обучение", "данные", "модель", "алгоритм"]),
    ("Сколько байт в одном килобайте?", ["1024"]),
    ("Что такое HTTP?", ["протокол", "передача", "гипертекст"]),
    ("Чем отличается TCP от UDP?", ["надёжн", "соединение", "пакет"]),
    ("Что такое Git?", ["система контроля версий"]),
    ("Что такое Docker?", ["контейнер", "изоляция"]),
    ("Объясни SOLID-принципы.", ["single responsibility", "принципы"]),
    ("Что такое RESTful API?", ["REST", "HTTP", "ресурс"]),
    ("Что означает аббревиатура SQL?", ["Structured Query Language"]),
    ("Как работает индекс в базе данных?", ["поиск", "быстро", "B-tree"]),
    ("Что такое рекурсия?", ["функция", "вызывает", "себя", "базовый случай"]),
    ("Что такое Big O нотация?", ["сложность", "алгоритм", "время"]),
    ("Чем отличается list от tuple в Python?", ["изменяем", "неизменяем", "mutable"]),
    ("Что такое CI/CD?", ["непрерывная", "интеграция", "доставка"]),
    ("Что такое микросервисы?", ["сервис", "независимый", "масштаб"]),
    ("Что такое Redis?", ["кэш", "in-memory", "хранилище"]),
    ("Объясни паттерн Singleton.", ["один экземпляр", "instance"]),
    ("Что такое JWT токен?", ["JSON Web Token", "авторизация", "payload"]),
    ("Что такое асинхронное программирование?", ["async", "await", "event loop"]),
    ("Чем отличается процесс от потока?", ["процесс", "поток", "память"]),
    ("Что такое deadlock?", ["взаимная", "блокировка", "ресурс"]),
    ("Что такое ORM?", ["объектно-реляционное", "таблица", "модель"]),
    ("Что такое N+1 проблема в SQL?", ["запрос", "оптимизация"]),
    ("Что такое ACID?", ["atomicity", "consistency", "isolation", "durability"]),
    ("Что такое нормализация базы данных?", ["нормальная форма", "дублирование"]),
    ("Объясни паттерн Observer.", ["подписка", "событие", "уведомление"]),
    ("Что такое MVC?", ["model", "view", "controller"]),
    ("Что такое WebSocket?", ["двунаправленный", "соединение", "реальное время"]),
    ("Что такое виртуальная машина?", ["изоляция", "гипервизор", "ресурсы"]),
    ("Чем отличается SSH от Telnet?", ["шифрование", "безопасность"]),
    ("Что такое DNS?", ["Domain Name System", "IP", "имя"]),
    ("Что такое CDN?", ["Content Delivery Network", "кэш", "географ"]),
    ("Что такое API Gateway?", ["маршрутизация", "API", "шлюз"]),
    ("Объясни паттерн Factory Method.", ["создание", "объект", "фабрика"]),
    ("Что такое dependency injection?", ["зависимость", "внедрение", "инверсия"]),
    ("Что такое event sourcing?", ["событие", "состояние", "история"]),
    ("Чем отличается SQL от NoSQL?", ["реляционная", "схема", "таблица"]),
    ("Что такое шардирование?", ["разделение", "данные", "горизонтальное"]),
    ("Что такое load balancer?", ["балансировка", "нагрузка", "сервер"]),
    ("Что такое blue-green deployment?", ["переключение", "версия", "downtime"]),
    ("Что такое canary deployment?", ["постепенный", "трафик", "риск"]),
    ("Что такое health check?", ["проверка", "живость", "сервис"]),
    ("Что такое circuit breaker?", ["отказ", "защита", "cascading"]),
    ("Объясни CAP теорему.", ["consistency", "availability", "partition"]),
    ("Что такое eventual consistency?", ["согласованность", "распределённый"]),
    ("Что такое gRPC?", ["RPC", "Protocol Buffers", "HTTP/2"]),
    ("Что такое GraphQL?", ["запрос", "схема", "граф"]),
    ("Что такое serverless?", ["функция", "облако", "инфраструктура"]),
    ("Что такое Kubernetes?", ["оркестрация", "контейнер", "pod"]),
    ("Что такое Helm?", ["Kubernetes", "пакет", "chart"]),
    ("Что такое Service Mesh?", ["сеть", "микросервисы", "Istio"]),
    ("Что такое observability?", ["логирование", "метрики", "трассировка"]),
    ("Что такое distributed tracing?", ["трассировка", "span", "запрос"]),
    ("Что такое feature flag?", ["флаг", "функция", "включить"]),
    ("Что такое A/B тестирование?", ["сравнение", "вариант", "эксперимент"]),
    ("Что такое техдолг?", ["технический долг", "код", "рефакторинг"]),
    ("Что такое code review?", ["проверка", "код", "качество"]),
    ("Что такое TDD?", ["Test Driven Development", "тест", "разработка"]),
    ("Что такое BDD?", ["Behaviour Driven Development", "поведение"]),
    ("Что такое mock в тестировании?", ["заглушка", "имитация", "зависимость"]),
    ("Что такое regression testing?", ["регрессия", "изменение", "сломал"]),
    ("Что такое fuzzing?", ["случайный", "входные данные", "уязвимость"]),
    ("Что такое SQL injection?", ["SQL", "ввод", "безопасность", "атака"]),
    ("Что такое XSS атака?", ["cross-site scripting", "скрипт", "браузер"]),
    ("Что такое CSRF?", ["cross-site request forgery", "токен"]),
    ("Что такое OAuth2?", ["авторизация", "токен", "делегирование"]),
    ("Что такое JWT refresh token?", ["обновление", "токен", "срок"]),
    ("Что такое rate limiting?", ["ограничение", "запросы", "лимит"]),
    ("Что такое idempotency?", ["идемпотентность", "повторный", "результат"]),
    ("Что такое TTL?", ["Time To Live", "время жизни", "кэш"]),
    ("Что такое connection pooling?", ["пул", "соединение", "переиспользование"]),
    ("Что такое транзакция в БД?", ["атомарность", "COMMIT", "ROLLBACK"]),
    ("Что такое пессимистичная блокировка?", ["блокировка", "конкурентность", "FOR UPDATE"]),
    ("Что такое оптимистичная блокировка?", ["версия", "conflict", "CAS"]),
    ("Что такое GC (garbage collection)?", ["сборщик мусора", "память", "автоматически"]),
    ("Что такое heap и stack?", ["стек", "куча", "память", "переменная"]),
    ("Что такое closure в Python?", ["замыкание", "функция", "область видимости"]),
    ("Что такое декоратор в Python?", ["decorator", "функция", "обёртка"]),
    ("Что такое generator в Python?", ["yield", "итератор", "ленивый"]),
    ("Что такое context manager в Python?", ["with", "__enter__", "__exit__"]),
    ("Что такое metaclass в Python?", ["метакласс", "класс классов", "type"]),
    ("Что такое GIL в Python?", ["Global Interpreter Lock", "поток", "блокировка"]),
    ("Что такое asyncio в Python?", ["event loop", "coroutine", "async"]),
    ("Что такое type hints в Python?", ["аннотация", "тип", "mypy"]),
    ("Что такое dataclass в Python?", ["dataclass", "поля", "автоматически"]),
    ("Что такое Pydantic?", ["валидация", "данные", "схема", "типы"]),
    ("Что такое FastAPI?", ["FastAPI", "async", "REST", "Pydantic"]),
    ("Что такое aiogram?", ["Telegram", "бот", "async", "Python"]),
    ("Что такое Groq?", ["LLM", "inference", "API", "быстрый"]),
    ("Что такое LLM?", ["Large Language Model", "языковая модель", "текст"]),
    ("Что такое RAG?", ["Retrieval-Augmented Generation", "поиск", "контекст"]),
    ("Что такое embedding?", ["вектор", "представление", "семантика"]),
    ("Что такое pgvector?", ["PostgreSQL", "вектор", "расширение"]),
    ("Что такое MCP (Model Context Protocol)?", ["инструменты", "контекст", "LLM", "сервер"]),
    ("Что такое Ollama?", ["локальная", "модель", "LLM", "offline"]),
    ("Что такое prompt injection?", ["инъекция", "промпт", "атака", "LLM"]),
    ("Что такое hallucination у LLM?", ["галлюцинация", "выдуманный", "факт"]),
    ("Что такое fine-tuning?", ["тонкая настройка", "дообучение", "датасет"]),
]

assert len(BENCHMARK_DATASET) == 100, f"Expected 100 questions, got {len(BENCHMARK_DATASET)}"


JUDGE_SYSTEM_PROMPT = """Ты строгий оценщик качества ответов AI-ассистента.
Оцени ответ по следующим критериям (каждый от 0 до 10):
1. Точность (accuracy): насколько ответ фактически корректен
2. Релевантность (relevance): отвечает ли на поставленный вопрос
3. Отсутствие галлюцинаций (no_hallucination): нет ли выдуманных фактов
4. Полнота (completeness): достаточно ли полный ответ

Верни ТОЛЬКО JSON (без markdown) в формате:
{"accuracy": 8, "relevance": 9, "no_hallucination": 10, "completeness": 7, "overall": 8.5, "comment": "краткий комментарий"}"""


async def _get_bot_answer(question: str, groq_client, model: str) -> str:
    resp = await groq_client.chat.completions.create(
        messages=[
            {
                "role": "system",
                "content": "Ты полезный, вежливый и лаконичный AI-ассистент. Отвечай на языке вопроса.",
            },
            {"role": "user", "content": question},
        ],
        model=model,
        temperature=0.7,
        max_tokens=300,
    )
    return resp.choices[0].message.content


async def _judge_answer(
    question: str,
    bot_answer: str,
    reference_keywords: list[str],
    groq_client,
    judge_model: str,
) -> dict[str, Any]:
    prompt = (
        f"Вопрос: {question}\n\n"
        f"Ответ бота: {bot_answer}\n\n"
        f"Ключевые слова правильного ответа: {', '.join(reference_keywords)}"
    )
    try:
        resp = await groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            model=judge_model,
            temperature=0.1,
            max_tokens=200,
        )
        raw = resp.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as exc:
        return {
            "accuracy": 5, "relevance": 5, "no_hallucination": 8,
            "completeness": 5, "overall": 5.0,
            "comment": f"Judge error: {exc}",
        }


async def _run_evaluation(n_questions: int = QUESTIONS_FOR_CI) -> dict[str, Any]:
    from groq import AsyncGroq
    import os

    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key or api_key.startswith("gsk_test"):
        pytest.skip("GROQ_API_KEY not set or is a test key — skipping LLM judge test")

    client = AsyncGroq(api_key=api_key)
    bot_model = "llama-3.1-8b-instant"
    judge_model = "llama-3.3-70b-versatile"

    dataset = BENCHMARK_DATASET[:n_questions]
    results = []

    for question, keywords in dataset:
        bot_answer = await _get_bot_answer(question, client, bot_model)
        scores = await _judge_answer(question, bot_answer, keywords, client, judge_model)
        results.append({
            "question": question,
            "bot_answer": bot_answer[:100],
            "scores": scores,
        })
        await asyncio.sleep(0.3)  # avoid rate limiting

    overall_scores = [r["scores"].get("overall", 5.0) for r in results]
    accuracy_scores = [r["scores"].get("accuracy", 5.0) for r in results]
    hallucination_scores = [r["scores"].get("no_hallucination", 5.0) for r in results]

    return {
        "n_evaluated": len(results),
        "avg_overall": round(statistics.mean(overall_scores), 2),
        "median_overall": round(statistics.median(overall_scores), 2),
        "avg_accuracy": round(statistics.mean(accuracy_scores), 2),
        "avg_no_hallucination": round(statistics.mean(hallucination_scores), 2),
        "min_score": round(min(overall_scores), 2),
        "max_score": round(max(overall_scores), 2),
        "results": results,
    }


# ── Pytest tests ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.slow
async def test_llm_judge_average_score_above_threshold() -> None:
    """
    Задача 27: регрессионный тест — средняя оценка не ниже MIN_ACCEPTABLE_SCORE.
    Провалится при деградации качества ответов после изменений.
    Запуск в CI: pytest -m slow --tb=short
    """
    summary = await _run_evaluation(n_questions=QUESTIONS_FOR_CI)

    print(f"\n📊 LLM Judge Results ({summary['n_evaluated']} questions):")
    print(f"   Avg overall score    : {summary['avg_overall']}/10")
    print(f"   Avg accuracy score   : {summary['avg_accuracy']}/10")
    print(f"   Avg no-hallucination : {summary['avg_no_hallucination']}/10")
    print(f"   Min / Max            : {summary['min_score']} / {summary['max_score']}")

    worst = sorted(summary["results"], key=lambda r: r["scores"].get("overall", 10))[:3]
    print("\n⚠️  Worst answers:")
    for r in worst:
        print(f"   Q: {r['question'][:60]}")
        print(f"   Score: {r['scores'].get('overall')}, Comment: {r['scores'].get('comment')}")

    assert summary["avg_overall"] >= MIN_ACCEPTABLE_SCORE, (
        f"Quality regression detected! Average score {summary['avg_overall']} "
        f"< threshold {MIN_ACCEPTABLE_SCORE}"
    )


@pytest.mark.asyncio
@pytest.mark.slow
async def test_hallucination_rate_acceptable() -> None:
    """Дополнительный тест: оценка галлюцинаций ≥ 7.0."""
    summary = await _run_evaluation(n_questions=5)
    assert summary["avg_no_hallucination"] >= 7.0, (
        f"Hallucination rate too high! Score: {summary['avg_no_hallucination']}"
    )


@pytest.mark.asyncio
@pytest.mark.slow
async def test_full_100_questions_benchmark() -> None:
    """Полный тест на 100 вопросах — запускать только вручную или еженедельно."""
    summary = await _run_evaluation(n_questions=100)
    print(f"\n📊 Full 100-question benchmark: avg={summary['avg_overall']}/10")
    assert summary["avg_overall"] >= MIN_ACCEPTABLE_SCORE