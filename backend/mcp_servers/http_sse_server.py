"""
Задача 12 — HTTP/SSE транспорт для MCP с Bearer-токеном.
Запуск: python http_sse_server.py
Клиент подключается через SSE: GET /sse, отправляет через POST /messages.
"""
from __future__ import annotations

import json
import os
import uuid
from collections import defaultdict
from typing import AsyncGenerator

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

MCP_BEARER_TOKEN = os.getenv("MCP_BEARER_TOKEN", "changeme-secret-token")
MCP_PORT = int(os.getenv("MCP_HTTP_PORT", "8001"))

app = FastAPI(title="MCP HTTP+SSE Server", version="1.0.0")
security = HTTPBearer()

# In-memory SSE queues per client
_queues: dict[str, list] = defaultdict(list)

TOOLS = [
    {
        "name": "get_weather",
        "description": "Получить погоду для города",
        "inputSchema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
    {
        "name": "get_crypto_price",
        "description": "Получить курс криптовалюты",
        "inputSchema": {
            "type": "object",
            "properties": {"coin": {"type": "string"}},
            "required": [],
        },
    },
]


def _verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    if credentials.credentials != MCP_BEARER_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


@app.get("/sse")
async def sse_endpoint(
    request: Request,
    token: str = Depends(_verify_token),
) -> StreamingResponse:
    """SSE endpoint: client connects here to receive MCP messages."""
    client_id = str(uuid.uuid4())

    async def event_generator() -> AsyncGenerator[str, None]:
        # Send session init
        init_msg = json.dumps({
            "jsonrpc": "2.0",
            "method": "session/init",
            "params": {"sessionId": client_id, "endpoint": f"/messages?session={client_id}"},
        })
        yield f"data: {init_msg}\n\n"

        while True:
            if await request.is_disconnected():
                break
            # Drain messages for this client
            while _queues[client_id]:
                msg = _queues[client_id].pop(0)
                yield f"data: {json.dumps(msg)}\n\n"
            # Heartbeat
            yield ": heartbeat\n\n"
            import asyncio
            await asyncio.sleep(1)

        _queues.pop(client_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/messages")
async def messages_endpoint(
    request: Request,
    session: str,
    token: str = Depends(_verify_token),
) -> dict:
    """Handle MCP JSON-RPC messages from client."""
    body = await request.json()
    method = body.get("method")
    req_id = body.get("id")
    params = body.get("params", {})

    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "http-sse-mcp-server", "version": "1.0.0"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})
        result = await _handle_tool(name, args)
    else:
        result = {}

    response = {"jsonrpc": "2.0", "id": req_id, "result": result}
    _queues[session].append(response)
    return {"status": "queued"}


async def _handle_tool(name: str, args: dict) -> dict:
    import httpx
    if name == "get_weather":
        city = args.get("city", "Almaty")
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"https://wttr.in/{city}?format=3")
                return {"content": [{"type": "text", "text": r.text.strip()}]}
        except Exception as exc:
            return {"content": [{"type": "text", "text": f"Ошибка: {exc}"}]}
    elif name == "get_crypto_price":
        coin = args.get("coin", "bitcoin")
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(
                    f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd"
                )
                data = r.json()
                price = data.get(coin, {}).get("usd", "N/A")
                return {"content": [{"type": "text", "text": f"{coin}: ${price}"}]}
        except Exception as exc:
            return {"content": [{"type": "text", "text": f"Ошибка: {exc}"}]}
    return {"content": [{"type": "text", "text": "Unknown tool"}]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT)
