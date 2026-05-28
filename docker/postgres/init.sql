-- Задача 8, 14, 29: инициализация схемы БД при первом запуске контейнера

CREATE EXTENSION IF NOT EXISTS vector;

-- Таблица пользователей бота
CREATE TABLE IF NOT EXISTS bot_users (
    user_id      BIGINT PRIMARY KEY,
    username     VARCHAR(64),
    first_name   VARCHAR(128),
    last_name    VARCHAR(128),
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    last_seen    TIMESTAMPTZ DEFAULT NOW(),
    message_count INTEGER DEFAULT 0,
    is_banned    BOOLEAN DEFAULT FALSE
);

-- Таблица сообщений для аналитики
CREATE TABLE IF NOT EXISTS messages (
    id          SERIAL PRIMARY KEY,
    user_id     BIGINT REFERENCES bot_users(user_id) ON DELETE CASCADE,
    chat_id     BIGINT NOT NULL,
    role        VARCHAR(16) NOT NULL,   -- 'user' | 'assistant'
    content     TEXT NOT NULL,
    model_used  VARCHAR(64),
    latency_ms  INTEGER,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- RAG документы с эмбеддингами (Задача 14)
CREATE TABLE IF NOT EXISTS documents (
    id        SERIAL PRIMARY KEY,
    content   TEXT NOT NULL,
    embedding vector(768),
    metadata  JSONB DEFAULT '{}',
    source    VARCHAR(256),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS documents_embedding_idx
    ON documents USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Сессии диалогов
CREATE TABLE IF NOT EXISTS sessions (
    id          SERIAL PRIMARY KEY,
    chat_id     BIGINT NOT NULL,
    user_id     BIGINT,
    started_at  TIMESTAMPTZ DEFAULT NOW(),
    ended_at    TIMESTAMPTZ,
    message_count INTEGER DEFAULT 0
);

-- Индексы
CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages(user_id);
CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_bot_users_last_seen ON bot_users(last_seen);

-- Примерные данные для тестирования
INSERT INTO bot_users (user_id, username, first_name, message_count)
VALUES
    (1001, 'alice', 'Алиса', 42),
    (1002, 'bob', 'Боб', 17),
    (1003, 'carol', 'Кэрол', 5)
ON CONFLICT DO NOTHING;
