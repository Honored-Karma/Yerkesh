# PracticeYerko — Telegram Bot

## Структура проекта

```
PracticeYerko/
├── backend/                  # Вся логика бота (Python)
│   ├── config/               # Pydantic Settings (.env, .env.local)
│   ├── handlers/             # aiogram роутеры (команды бота)
│   ├── mcp_servers/          # MCP-серверы (PostgreSQL, filesystem, HTTP/SSE)
│   ├── services/             # Бизнес-логика (Groq, RAG, Redis, Calendar и др.)
│   ├── utils/                # Логирование, трейсинг
│   ├── tests/                # Pytest (unit, integration, property-based, LLM-judge)
│   ├── main.py               # Точка входа — Long Polling режим
│   ├── requirements.txt      # Python зависимости
│   └── pytest.ini            # Конфигурация тестов
│
├── frontend/                 # HTTP-слой (webhook + OAuth)
│   ├── webhook_server.py     # Webhook-режим вместо polling (aiohttp)
│   └── requirements.txt      # Ссылается на backend/requirements.txt
│
├── docker/                   # Конфиги для Docker-сервисов
│   ├── postgres/init.sql
│   ├── prometheus/prometheus.yml
│   └── grafana/provisioning/
├── deploy/                   # Ansible playbooks
├── Dockerfile                # Multi-stage build
├── docker-compose.yml        # Полный стек: бот + Redis + PostgreSQL + мониторинг
└── .env                      # Переменные окружения (создать из примера ниже)
```

## Быстрый старт

### 1. Создать `.env`
```
BOT_TOKEN=123456789:AAAA...
GROQ_API_KEY=gsk_...
REDIS_URL=redis://localhost:6379/0
```

### 2a. Запуск через Docker Compose (рекомендуется)
```bash
docker compose up -d
```

### 2b. Запуск локально — Polling режим
```bash
pip install -r backend/requirements.txt
python backend/main.py
```

### 2c. Запуск локально — Webhook режим
```bash
pip install -r backend/requirements.txt
python frontend/webhook_server.py
```

### 3. Тесты
```bash
pip install -r backend/requirements.txt pytest pytest-asyncio pytest-mock pytest-cov hypothesis
cd backend && pytest tests/ -m "not slow" -v
```

## Разделение backend / frontend

| Слой | Что содержит | Точка входа |
|------|-------------|-------------|
| `backend/` | Бот-логика, MCP-серверы, сервисы, тесты | `python backend/main.py` |
| `frontend/` | Webhook HTTP-сервер, OAuth callback | `python frontend/webhook_server.py` |

`frontend/webhook_server.py` автоматически добавляет `backend/` в `sys.path`, поэтому
все импорты (`config`, `handlers`, `services`, `utils`) работают без изменений.

## Деплой веб-интерфейса (Vercel + Railway API)

**Railway** — бэкенд (REST API):

1. Создайте сервис из репозитория (используется `railway.toml` → `uvicorn api.app:app`).
2. Добавьте переменные: `BOT_TOKEN`, `GROQ_API_KEY`, `REDIS_URL` (Redis-плагин Railway).
3. Скопируйте публичный URL, например `https://your-app.up.railway.app`.
4. Проверьте: `https://your-app.up.railway.app/health` → `{"status":"ok"}`.

**Vercel** — фронтенд (`frontend/web/`):

1. Import репозитория в Vercel.
   - **Root Directory:** `frontend/web` (рекомендуется) — используется `frontend/web/vercel.json`
   - или корень репозитория — используется корневой `vercel.json`
2. **Settings → Environment Variables** → добавьте:
   - `API_URL` = `https://your-app.up.railway.app` (без `/` в конце)
   - для Production, Preview и Development
3. Redeploy — при сборке создаётся `frontend/web/config.js` с вашим URL.
4. Сайт на всех устройствах сразу ходит на Railway (ручная настройка не нужна).

**Локально без Vercel:**

```bash
# Вариант A: только localhost
cd frontend/web && python -m http.server 5500
# откройте http://localhost:5500 — API по умолчанию http://localhost:8000

# Вариант B: указать Railway в config.js
cp frontend/web/config.example.js frontend/web/config.js
# отредактируйте DEFAULT_API_URL в config.js
```

**Переопределение URL в браузере:** Настройки → URL бэкенда (сохраняется в `localStorage` на этом устройстве).
