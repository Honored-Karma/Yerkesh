"""
Задача 11 — MCP Resources и Prompts.
Resources: шаблоны документов из «БД» (здесь — in-memory словарь).
Prompts:   преднастроенные промпты для типовых задач.
Транспорт: stdio.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

# ── Simulated DB of document templates ────────────────────────────────────────
DOCUMENT_TEMPLATES: dict[str, str] = {
    "report_template": (
        "# Отчёт\n\n**Дата:** {date}\n**Автор:** {author}\n\n"
        "## Введение\n{intro}\n\n## Основная часть\n{body}\n\n## Заключение\n{conclusion}"
    ),
    "letter_template": (
        "Уважаемый(-ая) {name},\n\n{body}\n\nС уважением,\n{sender}"
    ),
    "meeting_notes_template": (
        "# Протокол совещания\n\n**Дата:** {date}\n**Участники:** {participants}\n\n"
        "## Повестка дня\n{agenda}\n\n## Решения\n{decisions}\n\n## Ответственные\n{responsible}"
    ),
    "technical_spec_template": (
        "# Техническое задание\n\n**Проект:** {project}\n**Версия:** {version}\n\n"
        "## Цели\n{goals}\n\n## Требования\n{requirements}\n\n## Ограничения\n{constraints}"
    ),
}

# ── Preconfigured prompts ──────────────────────────────────────────────────────
PRESET_PROMPTS: dict[str, dict] = {
    "summarize": {
        "name": "summarize",
        "description": "Кратко суммаризировать текст",
        "arguments": [
            {"name": "text", "description": "Текст для суммаризации", "required": True},
            {"name": "language", "description": "Язык ответа (ru/kz/en)", "required": False},
        ],
        "template": (
            "Кратко суммаризируй следующий текст в 3–5 предложениях "
            "на языке: {language}.\n\nТекст:\n{text}"
        ),
    },
    "translate": {
        "name": "translate",
        "description": "Перевести текст",
        "arguments": [
            {"name": "text", "description": "Текст для перевода", "required": True},
            {"name": "target_lang", "description": "Целевой язык", "required": True},
        ],
        "template": "Переведи следующий текст на {target_lang}:\n\n{text}",
    },
    "explain_code": {
        "name": "explain_code",
        "description": "Объяснить код простым языком",
        "arguments": [
            {"name": "code", "description": "Код для объяснения", "required": True},
            {"name": "language", "description": "Язык программирования", "required": False},
        ],
        "template": (
            "Объясни следующий код на {language} простым языком для начинающего. "
            "Опиши, что делает каждая часть:\n\n```{language}\n{code}\n```"
        ),
    },
    "write_tests": {
        "name": "write_tests",
        "description": "Написать тесты для функции",
        "arguments": [
            {"name": "function_code", "description": "Код функции", "required": True},
            {"name": "framework", "description": "Фреймворк тестирования (pytest/unittest)", "required": False},
        ],
        "template": (
            "Напиши unit-тесты для следующей функции, используя {framework}. "
            "Покрой граничные случаи и ошибки:\n\n```python\n{function_code}\n```"
        ),
    },
    "review_code": {
        "name": "review_code",
        "description": "Code review с рекомендациями",
        "arguments": [
            {"name": "code", "description": "Код для ревью", "required": True},
        ],
        "template": (
            "Выполни code review следующего кода. Укажи:\n"
            "1. Потенциальные баги\n2. Проблемы производительности\n"
            "3. Нарушения best practices\n4. Предложения по улучшению\n\n"
            "```\n{code}\n```"
        ),
    },
}


async def main() -> None:
    server = Server("resources-prompts-mcp-server")

    # ── Resources ──────────────────────────────────────────────────────────────
    @server.list_resources()
    async def handle_list_resources() -> list[types.Resource]:
        return [
            types.Resource(
                uri=f"template://{key}",
                name=key.replace("_", " ").title(),
                description=f"Шаблон документа: {key}",
                mimeType="text/markdown",
            )
            for key in DOCUMENT_TEMPLATES
        ]

    @server.read_resource()
    async def handle_read_resource(uri: types.AnyUrl) -> str:
        uri_str = str(uri)
        key = uri_str.removeprefix("template://")
        if key not in DOCUMENT_TEMPLATES:
            raise ValueError(f"Resource not found: {uri_str}")
        return DOCUMENT_TEMPLATES[key]

    # ── Prompts ────────────────────────────────────────────────────────────────
    @server.list_prompts()
    async def handle_list_prompts() -> list[types.Prompt]:
        return [
            types.Prompt(
                name=p["name"],
                description=p["description"],
                arguments=[
                    types.PromptArgument(
                        name=arg["name"],
                        description=arg["description"],
                        required=arg.get("required", False),
                    )
                    for arg in p["arguments"]
                ],
            )
            for p in PRESET_PROMPTS.values()
        ]

    @server.get_prompt()
    async def handle_get_prompt(
        name: str, arguments: dict[str, str] | None
    ) -> types.GetPromptResult:
        if name not in PRESET_PROMPTS:
            raise ValueError(f"Prompt not found: {name}")

        prompt_def = PRESET_PROMPTS[name]
        args = arguments or {}

        # Fill template with provided arguments, use placeholder for missing
        filled = prompt_def["template"]
        for key, val in args.items():
            filled = filled.replace(f"{{{key}}}", val)

        return types.GetPromptResult(
            description=prompt_def["description"],
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text=filled),
                )
            ],
        )

    # ── Tools (also expose template listing as a tool) ─────────────────────────
    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="list_templates",
                description="Список доступных шаблонов документов",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            types.Tool(
                name="get_template",
                description="Получить шаблон документа по имени",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "template_name": {
                            "type": "string",
                            "description": "Имя шаблона",
                            "enum": list(DOCUMENT_TEMPLATES.keys()),
                        }
                    },
                    "required": ["template_name"],
                },
            ),
        ]

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict[str, Any]
    ) -> list[types.TextContent]:
        if name == "list_templates":
            data = {
                "templates": [
                    {"name": k, "description": k.replace("_", " ").title()}
                    for k in DOCUMENT_TEMPLATES
                ]
            }
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "get_template":
            key = arguments.get("template_name", "")
            if key not in DOCUMENT_TEMPLATES:
                return [types.TextContent(type="text", text=f"Шаблон не найден: {key}")]
            return [types.TextContent(type="text", text=DOCUMENT_TEMPLATES[key])]

        return [types.TextContent(type="text", text="Unknown tool")]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
