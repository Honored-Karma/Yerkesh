"""
Инициализация MCP-агрегатора (Задача 10).
Вынесено из main.py для соблюдения лимита в 30 строк.
"""
from pathlib import Path

from handlers.mcp_handler import MCPClientStdio, mcp_aggregator

# Абсолютный путь к mcp_servers/ — работает из любого CWD
_MCP_DIR = Path(__file__).resolve().parent.parent / "mcp_servers"


async def setup_mcp() -> None:
    mcp_aggregator.register("fs", MCPClientStdio(["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]))
    mcp_aggregator.register("pg", MCPClientStdio(["python", str(_MCP_DIR / "postgres_server.py")]))
    mcp_aggregator.register("api", MCPClientStdio(["python", str(_MCP_DIR / "resources_prompts_server.py")]))
    await mcp_aggregator.start_all()
