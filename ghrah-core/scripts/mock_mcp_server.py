# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""本地 MCP 测试端点：用 Streamable HTTP 提供一组行为可预测的工具。

只用于开发和回归，不随发布包分发，也不代表生产 MCP 服务器的能力。它解决两个具体问题：

1. **真实传输有没有接通**：客户端走的是真正的 HTTP 请求、MCP 握手与工具调用，而不是
   进程内假对象，因此建连、请求头、结果投影、超时与取消这些传输层行为都能被覆盖。
2. **凭据有没有按配置送到对端**：``/stats`` 记录每次 MCP 请求的鉴权指纹（只记散列
   前缀，不回显密钥），测试据此确认密钥确实经由传输到达服务端，而没有进入模型可见
   的工具结果。

使用方式（默认只监听回环地址，端口 8765）::

    uv run python ghrah-core/scripts/mock_mcp_server.py --token local-test-token

端口写 ``0`` 时由系统分配，启动后向 stdout 打印一行 JSON，自动化测试据此取得实际地址
与统计端点。可用的工具：``echo``、``add``、``query``、``auth_probe``、``fail``、
``slow``、``write_marker``、``large``。

边界：单进程、回环地址、无持久化状态；工具列表与行为固定；不实现 MCP resources、
prompts 或真实鉴权，也不模拟生产服务器的错误码空间。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

import uvicorn
from mcp import types
from mcp.server import MCPServer

# ToolError 是 SDK 里“有意声明的工具级失败”，其消息会原样成为 is_error 结果的文本；
# 普通异常会被 SDK 折叠成一条不含内部细节的 UnexpectedToolError。
from mcp.server.mcpserver.exceptions import ToolError
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

__all__ = ["build_server", "main"]

_PUBLIC_PATHS = frozenset({"/healthz", "/stats"})

_STATE: dict[str, Any] = {"calls": [], "authorizations": [], "sessions": []}
"""进程内观测记录：调用顺序、鉴权指纹与新会话标识。"""


def _fingerprint(value: str) -> str:
    """只返回散列前缀，避免把密钥写进日志或统计响应。"""
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def _read_only(value: bool = True) -> types.ToolAnnotations:
    return types.ToolAnnotations(readOnlyHint=value)


def _build_tools(server: MCPServer, state_dir: Path) -> None:
    """注册固定工具集；参数校验与 schema 由 SDK 按函数签名生成。"""

    @server.tool(description="回显输入文本，用于确认调用链与结果投影。", annotations=_read_only())
    def echo(text: str) -> str:
        _STATE["calls"].append("echo")
        return f"echo: {text}"

    @server.tool(description="两数相加，用于确认结构化参数与返回值。", annotations=_read_only())
    def add(a: float, b: float) -> str:
        _STATE["calls"].append("add")
        return f"sum: {a + b:g}"

    @server.tool(
        description="按参数返回结果；能力接线的回归用它与受信成员做映射。",
        annotations=_read_only(),
    )
    def query(server: str = "") -> str:
        _STATE["calls"].append("query")
        return f"query result for {server}"

    @server.tool(
        description="报告最近一次鉴权指纹与调用次数；不返回任何密钥原文。",
        annotations=_read_only(),
    )
    def auth_probe() -> str:
        _STATE["calls"].append("auth_probe")
        last = _STATE["authorizations"][-1] if _STATE["authorizations"] else ""
        return json.dumps(
            {
                "auth_fingerprint": last,
                "requests": len(_STATE["authorizations"]),
                "calls": len(_STATE["calls"]),
                "sessions": len(_STATE["sessions"]),
            }
        )

    @server.tool(
        description="返回工具级错误，用于确认失败是结果而不是异常。", annotations=_read_only()
    )
    def fail(reason: str = "mock failure") -> str:
        _STATE["calls"].append("fail")
        raise ToolError(f"mock tool failure: {reason}")

    @server.tool(description="等待指定秒数，用于超时与取消回归。", annotations=_read_only())
    async def slow(seconds: float = 30.0) -> str:
        _STATE["calls"].append("slow")
        await asyncio.sleep(seconds)
        return f"slept {seconds:g}s"

    @server.tool(
        description="写入一个标记文件，用于只读/可写语义回归。", annotations=_read_only(False)
    )
    def write_marker(name: str = "marker.txt") -> str:
        _STATE["calls"].append("write_marker")
        path = state_dir / Path(name).name
        path.write_text("written by the local mock MCP server\n", encoding="utf-8", newline="\n")
        return f"wrote {path.name}"

    @server.tool(description="返回指定长度的文本，用于结果体量回归。", annotations=_read_only())
    def large(size: int = 1024) -> str:
        _STATE["calls"].append("large")
        return "x" * max(0, min(size, 1_000_000))


class _BearerGate:
    """ASGI 中间件：按需校验 Bearer，并记录鉴权指纹与新会话。

    直接实现 ASGI 而不是包装响应，避免影响 Streamable HTTP 的流式响应。
    ``/healthz`` 与 ``/stats`` 不鉴权，供测试读取观测结果。
    """

    def __init__(self, app: Any, *, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in _PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        session_id = headers.get("mcp-session-id")
        if session_id and session_id not in _STATE["sessions"]:
            _STATE["sessions"].append(session_id)
        if self.token:
            authorization = headers.get("authorization", "")
            _STATE["authorizations"].append(_fingerprint(authorization))
            if authorization != f"Bearer {self.token}":
                await Response("unauthorized", status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_server(*, token: str, state_dir: Path) -> MCPServer:
    """构建带固定工具与观测路由的服务器；不启动监听。"""
    server = MCPServer("ghrah-local-mock", description="ghrah 本地 MCP 测试端点")

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(_request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    @server.custom_route("/stats", methods=["GET"])
    async def stats(_request: Request) -> Response:
        return JSONResponse(
            {
                "calls": list(_STATE["calls"]),
                "authorizations": list(_STATE["authorizations"]),
                "sessions": list(_STATE["sessions"]),
                "auth_required": bool(token),
            }
        )

    _build_tools(server, state_dir)
    return server


async def _serve(args: argparse.Namespace) -> None:
    state_dir = (
        Path(args.state_dir) if args.state_dir else Path(tempfile.mkdtemp(prefix="ghrah-mock-"))
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    server = build_server(token=args.token, state_dir=state_dir)
    app = server.streamable_http_app(host=args.host)
    app.add_middleware(_BearerGate, token=args.token)

    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        access_log=False,
    )
    uvicorn_server = uvicorn.Server(config)
    serve_task = asyncio.create_task(uvicorn_server.serve())
    while not uvicorn_server.started:
        if serve_task.done():
            await serve_task
            raise SystemExit("mock MCP server exited during startup")
        await asyncio.sleep(0.02)
    socket = uvicorn_server.servers[0].sockets[0]
    port = socket.getsockname()[1]
    base = f"http://{args.host}:{port}"
    print(
        json.dumps({"listening": f"{base}/mcp", "stats": f"{base}/stats", "port": port}),
        flush=True,
    )
    await serve_task


def main() -> int:
    """解析参数并启动服务；被终止或中断时正常退出。"""
    parser = argparse.ArgumentParser(description="本地 MCP 测试端点（Streamable HTTP）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认只监听回环")
    parser.add_argument("--port", type=int, default=8765, help="端口；0 表示由系统分配")
    parser.add_argument("--token", default="", help="非空时要求 Authorization: Bearer <token>")
    parser.add_argument("--state-dir", default="", help="写工具的落盘目录；默认临时目录")
    parser.add_argument("--log-level", default="warning", help="uvicorn 日志级别")
    args = parser.parse_args()
    try:
        asyncio.run(_serve(args))
    except KeyboardInterrupt:  # pragma: no cover - 交互式中断
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
