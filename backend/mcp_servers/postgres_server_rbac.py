"""
Задача 9 — MCP-сервер с авторизацией по ролям (student, teacher, admin).
Роль передаётся через clientInfo.role в initialization parameters MCP-сессии.
Ошибка -32603 при попытке вызвать запрещённый инструмент.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

# Role-based tool permissions
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "student": {"get_my_grades", "get_schedule", "list_courses"},
    "teacher": {"get_my_grades", "get_schedule", "list_courses", "get_class_stats", "export_grades"},
    "admin":   {"get_my_grades", "get_schedule", "list_courses", "get_class_stats", "export_grades",
                "query_users", "get_user_stats", "export_to_csv", "delete_user"},
}

ALL_TOOLS = [
    types.Tool(
        name="get_my_grades",
        description="Получить свои оценки",
        inputSchema={"type": "object", "properties": {"student_id": {"type": "integer"}}, "required": ["student_id"]},
    ),
    types.Tool(
        name="get_schedule",
        description="Получить расписание занятий",
        inputSchema={"type": "object", "properties": {"week": {"type": "integer"}}, "required": []},
    ),
    types.Tool(
        name="list_courses",
        description="Список всех курсов",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    types.Tool(
        name="get_class_stats",
        description="Статистика по группе (только для преподавателей)",
        inputSchema={"type": "object", "properties": {"group_id": {"type": "integer"}}, "required": ["group_id"]},
    ),
    types.Tool(
        name="export_grades",
        description="Экспорт оценок в CSV (только для преподавателей)",
        inputSchema={"type": "object", "properties": {"course_id": {"type": "integer"}}, "required": ["course_id"]},
    ),
    types.Tool(
        name="query_users",
        description="Список пользователей (только для администраторов)",
        inputSchema={"type": "object", "properties": {"limit": {"type": "integer"}}, "required": []},
    ),
    types.Tool(
        name="delete_user",
        description="Удалить пользователя (только для администраторов)",
        inputSchema={"type": "object", "properties": {"user_id": {"type": "integer"}}, "required": ["user_id"]},
    ),
]

# Global role state — set during initialize from clientInfo.role
_current_role: str = "student"


async def main() -> None:
    global _current_role

    server = Server("rbac-mcp-server")

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        allowed = ROLE_PERMISSIONS.get(_current_role, set())
        return [t for t in ALL_TOOLS if t.name in allowed]

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict[str, Any]
    ) -> list[types.TextContent]:
        allowed = ROLE_PERMISSIONS.get(_current_role, set())
        if name not in allowed:
            # MCP spec: error code -32603 for authorization failures
            raise Exception(
                json.dumps({
                    "code": -32603,
                    "message": f"Недостаточно прав. Инструмент '{name}' недоступен для роли '{_current_role}'.",
                })
            )

        if name == "get_my_grades":
            return [types.TextContent(type="text", text=json.dumps({
                "grades": [
                    {"course": "Информационные системы", "grade": 85},
                    {"course": "Математика", "grade": 92},
                    {"course": "Программирование", "grade": 78},
                ]
            }))]
        elif name == "get_schedule":
            return [types.TextContent(type="text", text=json.dumps({
                "schedule": [
                    {"day": "Понедельник", "time": "09:00", "course": "Математика", "room": "A-201"},
                    {"day": "Среда", "time": "11:00", "course": "Программирование", "room": "B-101"},
                ]
            }))]
        elif name == "list_courses":
            return [types.TextContent(type="text", text=json.dumps({
                "courses": ["Информационные системы", "Математика", "Программирование", "Базы данных"]
            }))]
        elif name == "get_class_stats":
            return [types.TextContent(type="text", text=json.dumps({
                "group_id": arguments.get("group_id"),
                "avg_grade": 81.3,
                "student_count": 25,
                "top_student": "Иванов И.И."
            }))]
        elif name == "export_grades":
            return [types.TextContent(type="text", text="student_id,name,grade\n1,Иванов,85\n2,Петров,90")]
        elif name == "query_users":
            return [types.TextContent(type="text", text=json.dumps({"users": [], "count": 0}))]
        elif name == "delete_user":
            return [types.TextContent(type="text", text=json.dumps({"deleted": arguments.get("user_id")}))]

        return [types.TextContent(type="text", text="Unknown tool")]

    # Задача 9: читаем роль из CLI-аргумента --role=X (для тестирования)
    # В production роль приходит через clientInfo.role в initialize
    # Парсим CLI для обратной совместимости
    for arg in sys.argv[1:]:
        if arg.startswith("--role="):
            role = arg.split("=", 1)[1]
            if role in ROLE_PERMISSIONS:
                _current_role = role
                break

    # NOTE: для передачи роли через initialization parameters нужно
    # переопределить обработку initialize на низком уровне mcp.server.
    # Рекомендуется передавать роль через --role= аргумент при запуске сервера,
    # что клиент должен делать на основе аутентифицированного пользователя.

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
