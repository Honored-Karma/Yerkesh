"""
Задача 7  — Подключение официального MCP-сервера filesystem, команда /files.
Задача 8  — PostgreSQL MCP-сервер (клиентская часть).
Задача 9  — RBAC: роль передаётся через initialization parameters.
Задача 10 — MCP-агрегатор: несколько серверов + роутинг с префиксами.
Задача 11 — MCP Resources и Prompts (команды /mcp_resources, /mcp_prompts).
Задача 12 — HTTP/SSE клиент с авторезаключением при разрыве.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

# Абсолютный путь к папке mcp_servers (работает независимо от CWD)
_BACKEND_DIR = Path(__file__).resolve().parent.parent
MCP_SERVERS_DIR = _BACKEND_DIR / "mcp_servers"

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from utils.logging import get_logger

logger = get_logger(__name__)
router = Router(name="mcp")

# Windows-совместимый путь для MCP filesystem
if sys.platform == "win32":
    NPX_CMD = "npx.cmd"
    ALLOWED_DIR = str(os.path.expanduser("~/Downloads"))
else:
    NPX_CMD = "npx"
    ALLOWED_DIR = "/tmp"


# ─────────────────────────────────────────────────────────────────────────────
# Задача 7/8/9/10/11: Stdio MCP client
# ─────────────────────────────────────────────────────────────────────────────

class MCPClientStdio:
    """Async MCP client over stdio transport (JSON-RPC 2.0)."""

    def __init__(self, command: List[str], role: str = "student") -> None:
        self.command = command
        self.role = role  # Задача 9: роль передаётся при initialize
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._id = 0

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        # Задача 9: передаём роль через clientInfo в initialize
        await self._send({
            "jsonrpc": "2.0",
            "method": "initialize",
            "id": 0,
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "clientInfo": {
                    "name": "telegram-bot",
                    "version": "1.0.0",
                    "role": self.role,   # ← Задача 9
                },
            },
        })
        await self._recv()

    async def stop(self) -> None:
        if self._proc:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except Exception:
                pass

    async def _send(self, payload: dict) -> None:
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("MCP process not started")
        data = json.dumps(payload) + "\n"
        self._proc.stdin.write(data.encode())
        await self._proc.stdin.drain()

    async def _recv(self) -> dict:
        if not self._proc or not self._proc.stdout:
            raise RuntimeError("MCP process not started")
        line = await asyncio.wait_for(self._proc.stdout.readline(), timeout=15.0)
        return json.loads(line)

    async def _call(self, method: str, params: dict | None = None) -> Any:
        self._id += 1
        await self._send({
            "jsonrpc": "2.0",
            "method": method,
            "id": self._id,
            "params": params or {},
        })
        resp = await self._recv()
        if "error" in resp:
            raise RuntimeError(
                f"MCP error {resp['error'].get('code')}: {resp['error'].get('message')}"
            )
        return resp.get("result")

    async def list_tools(self) -> List[dict]:
        result = await self._call("tools/list")
        return result.get("tools", [])

    async def call_tool(self, name: str, arguments: dict) -> Any:
        return await self._call("tools/call", {"name": name, "arguments": arguments})

    # Задача 11: Resources
    async def list_resources(self) -> List[dict]:
        result = await self._call("resources/list")
        return result.get("resources", [])

    async def read_resource(self, uri: str) -> str:
        result = await self._call("resources/read", {"uri": uri})
        contents = result.get("contents", [])
        return "\n".join(c.get("text", "") for c in contents)

    # Задача 11: Prompts
    async def list_prompts(self) -> List[dict]:
        result = await self._call("prompts/list")
        return result.get("prompts", [])

    async def get_prompt(self, name: str, arguments: dict | None = None) -> str:
        result = await self._call("prompts/get", {"name": name, "arguments": arguments or {}})
        messages = result.get("messages", [])
        return "\n".join(m.get("content", {}).get("text", "") for m in messages)


# ─────────────────────────────────────────────────────────────────────────────
# Задача 10: MCP Aggregator
# ─────────────────────────────────────────────────────────────────────────────

class MCPAggregator:
    """
    Задача 10: агрегирует несколько MCP-серверов с префиксами.
    Префиксы: fs__ (filesystem), pg__ (postgres), api__ (custom).
    """

    def __init__(self) -> None:
        self._servers: Dict[str, MCPClientStdio] = {}
        self._tool_map: Dict[str, tuple[str, str]] = {}  # prefixed → (prefix, original)

    def register(self, prefix: str, client: MCPClientStdio) -> None:
        self._servers[prefix] = client

    async def start_all(self) -> None:
        for prefix, client in self._servers.items():
            try:
                await client.start()
                tools = await client.list_tools()
                for tool in tools:
                    self._tool_map[f"{prefix}__{tool['name']}"] = (prefix, tool["name"])
                logger.info("mcp_server_started", prefix=prefix, tools=len(tools))
            except Exception as exc:
                logger.warning("mcp_server_start_failed", prefix=prefix, error=str(exc))

    async def stop_all(self) -> None:
        for client in self._servers.values():
            try:
                await client.stop()
            except Exception:
                pass

    async def list_all_tools(self) -> List[dict]:
        all_tools = []
        for prefix, client in self._servers.items():
            try:
                tools = await client.list_tools()
                for t in tools:
                    all_tools.append({**t, "name": f"{prefix}__{t['name']}"})
            except Exception:
                pass
        return all_tools

    async def call_tool(self, prefixed_name: str, arguments: dict) -> Any:
        if prefixed_name not in self._tool_map:
            raise ValueError(f"Unknown tool: {prefixed_name}")
        prefix, original_name = self._tool_map[prefixed_name]
        return await self._servers[prefix].call_tool(original_name, arguments)


# ─────────────────────────────────────────────────────────────────────────────
# Задача 12: HTTP/SSE client with reconnect
# ─────────────────────────────────────────────────────────────────────────────

class MCPClientHTTP:
    """
    Задача 12: HTTP+SSE MCP client с Bearer-токеном и переподключением.
    """

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url
        self.token = token
        self._session_id: Optional[str] = None

    async def connect_and_listen(
        self, max_retries: int = 5
    ) -> AsyncGenerator[dict, None]:
        import httpx

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(
                    headers={"Authorization": f"Bearer {self.token}"},
                    timeout=None,
                ) as client:
                    async with client.stream("GET", f"{self.base_url}/sse") as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if line.startswith("data:"):
                                try:
                                    msg = json.loads(line[5:].strip())
                                    if msg.get("method") == "session/init":
                                        self._session_id = msg["params"].get("sessionId")
                                    yield msg
                                except json.JSONDecodeError:
                                    pass
                return
            except Exception as exc:
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(
                        "sse_disconnected",
                        error=str(exc),
                        retry_in=wait,
                        attempt=attempt + 1,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error("sse_max_retries_exceeded", error=str(exc))
                    raise

    async def call_tool(self, name: str, arguments: dict) -> dict:
        import httpx

        if not self._session_id:
            raise RuntimeError("Not connected to SSE server yet")

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=30.0,
        ) as client:
            resp = await client.post(
                f"{self.base_url}/messages",
                json=payload,
                params={"session": self._session_id},
            )
            resp.raise_for_status()
            return resp.json()


# Singleton aggregator
mcp_aggregator = MCPAggregator()


# ─────────────────────────────────────────────────────────────────────────────
# Telegram command handlers
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("files"))
async def cmd_files(message: Message) -> None:
    """
    Задача 7: показать содержимое директории через официальный
    @modelcontextprotocol/server-filesystem.
    """
    await message.answer("📁 Запрашиваю список файлов через MCP…")

    try:
        client = MCPClientStdio(
            [NPX_CMD, "-y", "@modelcontextprotocol/server-filesystem", ALLOWED_DIR]
        )
        await client.start()

        tools = await client.list_tools()
        tool_names = [t["name"] for t in tools]

        result = await client.call_tool("list_directory", {"path": ALLOWED_DIR})
        contents = result.get("content", [{}])
        text = contents[0].get("text", str(result)) if contents else str(result)

        await client.stop()
        await message.answer(
            f"📁 <b>Файлы в {ALLOWED_DIR}:</b>\n\n"
            + "\n".join(f"• <code>{t}</code>" for t in tool_names)
            + f"\n\n{text}",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.exception("mcp_files_error", error=str(exc))
        await message.answer(
            "❌ MCP-сервер недоступен.\n"
            "Убедитесь, что установлен Node.js и npm:\n"
            "<code>npm install -g @modelcontextprotocol/server-filesystem</code>",
            parse_mode="HTML",
        )


@router.message(Command("mcp_tools"))
async def cmd_mcp_tools(message: Message) -> None:
    """Задача 10: показать все инструменты всех MCP-серверов с префиксами."""
    tools = await mcp_aggregator.list_all_tools()
    if not tools:
        await message.answer("ℹ️ Нет подключённых MCP-серверов или инструментов.")
        return

    lines = ["<b>🔧 MCP Инструменты (все серверы):</b>"]
    for t in tools:
        lines.append(f"• <code>{t['name']}</code> — {t.get('description', '')[:60]}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("mcp_resources"))
async def cmd_mcp_resources(message: Message) -> None:
    """Задача 11: список MCP Resources из api-сервера."""
    await message.answer("📄 Запрашиваю ресурсы MCP…")
    try:
        client = MCPClientStdio(["python", str(MCP_SERVERS_DIR / "resources_prompts_server.py")])
        await client.start()
        resources = await client.list_resources()
        await client.stop()

        if not resources:
            await message.answer("ℹ️ Ресурсы не найдены.")
            return

        lines = ["<b>📄 MCP Resources:</b>"]
        for r in resources:
            lines.append(f"• <code>{r.get('uri', '')}</code> — {r.get('name', '')}")
        await message.answer("\n".join(lines), parse_mode="HTML")
    except Exception as exc:
        logger.exception("mcp_resources_error", error=str(exc))
        await message.answer(f"❌ Ошибка: {exc}")


@router.message(Command("mcp_prompts"))
async def cmd_mcp_prompts(message: Message) -> None:
    """Задача 11: список преднастроенных промптов MCP."""
    await message.answer("💬 Запрашиваю промпты MCP…")
    try:
        client = MCPClientStdio(["python", str(MCP_SERVERS_DIR / "resources_prompts_server.py")])
        await client.start()
        prompts = await client.list_prompts()
        await client.stop()

        if not prompts:
            await message.answer("ℹ️ Промпты не найдены.")
            return

        lines = ["<b>💬 MCP Prompts:</b>"]
        for p in prompts:
            lines.append(f"• <code>{p.get('name', '')}</code> — {p.get('description', '')}")
        await message.answer("\n".join(lines), parse_mode="HTML")
    except Exception as exc:
        logger.exception("mcp_prompts_error", error=str(exc))
        await message.answer(f"❌ Ошибка: {exc}")