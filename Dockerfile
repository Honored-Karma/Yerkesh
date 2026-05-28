# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Runtime dependencies + Node.js (нужен для npx @modelcontextprotocol/server-filesystem)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libpq5 \
    curl \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Non-root user (Задача 29: security best practice)
RUN groupadd --gid 1001 botuser \
    && useradd --uid 1001 --gid botuser --shell /bin/bash --create-home botuser

WORKDIR /app

COPY --from=builder /install /usr/local

# Копируем backend/ и frontend/ в контейнер
COPY --chown=botuser:botuser backend/ ./backend/
COPY --chown=botuser:botuser frontend/ ./frontend/

RUN mkdir -p /app/logs && chown -R botuser:botuser /app/logs

USER botuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/oauth/callback || exit 0

EXPOSE 8000 8443

# По умолчанию запускаем polling-режим из backend/
CMD ["python", "backend/main.py"]
