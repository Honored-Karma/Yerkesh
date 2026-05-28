"""
Задача 8 — Собственный MCP-сервер для работы с PostgreSQL.
Инструменты: query_users, get_user_stats, export_to_csv.
Транспорт: stdio.
"""
from __future__ import annotations

import csv
import io
import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("PostgreSQL MCP Server")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/botdb")


def _get_connection():
    """Sync connection for MCP (runs in separate process)."""
    import psycopg2
    return psycopg2.connect(DATABASE_URL)


@mcp.tool()
def query_users(
    limit: int = 10,
    offset: int = 0,
    search: str = "",
) -> str:
    """
    Получает список пользователей бота из базы данных.
    Параметры:
      limit  — количество записей (по умолчанию 10)
      offset — смещение (по умолчанию 0)
      search — фильтр по имени или username (необязательно)
    """
    try:
        conn = _get_connection()
        cur = conn.cursor()
        if search:
            cur.execute(
                """
                SELECT user_id, username, first_name, last_name, created_at, message_count
                FROM bot_users
                WHERE username ILIKE %s OR first_name ILIKE %s
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (f"%{search}%", f"%{search}%", limit, offset),
            )
        else:
            cur.execute(
                """
                SELECT user_id, username, first_name, last_name, created_at, message_count
                FROM bot_users
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description]
        conn.close()

        if not rows:
            return json.dumps({"users": [], "count": 0})
        users = [dict(zip(columns, row)) for row in rows]
        # Convert datetime to string
        for u in users:
            for k, v in u.items():
                if hasattr(v, "isoformat"):
                    u[k] = v.isoformat()
        return json.dumps({"users": users, "count": len(users)}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def get_user_stats() -> str:
    """
    Возвращает агрегированную статистику пользователей бота:
    общее количество, активных за 7 дней, топ-10 по сообщениям.
    """
    try:
        conn = _get_connection()
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM bot_users")
        total = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM bot_users WHERE last_seen >= NOW() - INTERVAL '7 days'"
        )
        active_7d = cur.fetchone()[0]

        cur.execute(
            """
            SELECT user_id, username, first_name, message_count
            FROM bot_users
            ORDER BY message_count DESC
            LIMIT 10
            """
        )
        top_users = [
            {"user_id": r[0], "username": r[1], "first_name": r[2], "message_count": r[3]}
            for r in cur.fetchall()
        ]
        conn.close()

        return json.dumps({
            "total_users": total,
            "active_7d": active_7d,
            "top_users": top_users,
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def export_to_csv(table: str = "bot_users", limit: int = 1000) -> str:
    """
    Экспортирует таблицу в CSV-формат (возвращает строку CSV).
    Параметр table: имя таблицы (по умолчанию 'bot_users').
    Параметр limit: максимальное количество строк (по умолчанию 1000).
    """
    ALLOWED_TABLES = {"bot_users", "messages", "sessions"}
    if table not in ALLOWED_TABLES:
        return f"Ошибка: таблица '{table}' не разрешена. Доступно: {', '.join(ALLOWED_TABLES)}"

    try:
        conn = _get_connection()
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {table} LIMIT %s", (limit,))  # noqa: S608
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description]
        conn.close()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([str(v) if v is not None else "" for v in row])

        return output.getvalue()
    except Exception as exc:
        return f"Ошибка экспорта: {exc}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
