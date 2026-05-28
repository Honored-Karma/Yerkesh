"""
GET  /api/tools          — список всех зарегистрированных MCP-инструментов
POST /api/tools/call     — вызов конкретного инструмента
GET  /api/tools/servers  — статус MCP-серверов
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from handlers.mcp_handler import mcp_aggregator
from utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


class ToolCallRequest(BaseModel):
    server: str           # "fs" | "pg" | "api"
    tool: str             # имя инструмента
    arguments: Dict[str, Any] = {}


class ToolCallResponse(BaseModel):
    result: Any
    server: str
    tool: str


@router.get("")
async def list_tools():
    """Список всех доступных инструментов по серверам."""
    result = {}
    for name, client in mcp_aggregator._servers.items():
        try:
            tools = await client.list_tools()
            result[name] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.inputSchema,
                }
                for t in (tools.tools if hasattr(tools, "tools") else tools)
            ]
        except Exception as exc:
            result[name] = {"error": str(exc)}
    return result


@router.get("/servers")
async def list_servers():
    """Статус каждого MCP-сервера."""
    statuses = {}
    for name, client in mcp_aggregator._servers.items():
        try:
            tools = await client.list_tools()
            count = len(tools.tools if hasattr(tools, "tools") else tools)
            statuses[name] = {"status": "ok", "tools_count": count}
        except Exception as exc:
            statuses[name] = {"status": "error", "detail": str(exc)}
    return statuses


@router.post("/call", response_model=ToolCallResponse)
async def call_tool(req: ToolCallRequest):
    """Вызов MCP-инструмента напрямую."""
    client = mcp_aggregator._servers.get(req.server)
    if not client:
        raise HTTPException(
            status_code=404,
            detail=f"MCP server '{req.server}' not found. "
                   f"Available: {list(mcp_aggregator._servers.keys())}",
        )
    try:
        result = await client.call_tool(req.tool, req.arguments)
        content = result.content if hasattr(result, "content") else result
        # Сериализуем content в читаемый вид
        if isinstance(content, list):
            text = "\n".join(
                c.text if hasattr(c, "text") else str(c) for c in content
            )
        else:
            text = str(content)
        logger.info("tool_called", server=req.server, tool=req.tool)
        return ToolCallResponse(result=text, server=req.server, tool=req.tool)
    except Exception as exc:
        logger.exception("tool_call_error", server=req.server, tool=req.tool)
        raise HTTPException(status_code=500, detail=str(exc))
