"""显式的新 Runtime HTTP 宿主；请求经 RuntimeClient 透传，状态仍归 Core。"""

from __future__ import annotations

import argparse
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from ghrah.subject.runtime_connection import RuntimeConnection, RuntimeConnectionConfig


def _problem(status: int, code: str | None, detail: str) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "https://ghrah.org/problems/http-host",
        "title": detail,
        "status": status,
        "detail": detail,
        "retryable": False,
    }
    if code is not None:
        body["code"] = code
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


def create_runtime_app(
    connection: RuntimeConnection,
    *,
    allowed_origins: tuple[str, ...],
    api_token: str = "",
    max_body_bytes: int = 9 * 1024 * 1024,
) -> FastAPI:
    """持有连接生命周期，并保留新契约的状态、响应头、路径和请求语义。"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        del app
        try:
            await connection.start()
            yield
        finally:
            await connection.stop()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "If-Match",
            "If-None-Match",
            "Idempotency-Key",
        ],
        expose_headers=["ETag", "Location", "Idempotency-Replayed", "Allow"],
    )

    @app.get("/health")
    async def health() -> Response:
        client = connection.client
        ready = client is not None and (await client.status()).ready
        return JSONResponse({"ready": ready}, status_code=200 if ready else 503)

    @app.api_route("/v1/{resource:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
    async def dispatch(resource: str, request: Request) -> Response:
        if api_token and not secrets.compare_digest(
            request.headers.get("authorization", ""), f"Bearer {api_token}"
        ):
            return _problem(401, "ACCESS_DENIED", "Runtime authentication required")
        client = connection.client
        if client is None:
            return _problem(503, "UNAVAILABLE", "Runtime connection is not ready")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > max_body_bytes:
                return _problem(413, None, "Request body exceeds host quota")
        body = None
        if raw:
            try:
                body = json.loads(raw)
            except ValueError, UnicodeError:
                return _problem(422, "INVALID_ARGUMENT", "A JSON object is required")
            if not isinstance(body, dict):
                return _problem(422, "INVALID_ARGUMENT", "A JSON object is required")
        path = f"/v1/{resource}"
        if request.url.query:
            path += "?" + request.url.query
        headers = {
            name: request.headers[name]
            for name in ("if-match", "if-none-match", "idempotency-key")
            if name in request.headers
        }
        result = await client.request(request.method, path, headers=headers, body=body)
        if result.body is None:
            return Response(status_code=result.status, headers=result.headers)
        return JSONResponse(result.body, status_code=result.status, headers=result.headers)

    return app


def main() -> None:
    """显式启动一个受信作用域的新契约宿主；模型由正式 agentconf 解析。"""
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4114)
    parser.add_argument("--origin", action="append", default=[])
    args = parser.parse_args()
    if not Path(args.database).is_absolute():
        parser.error("--database must be an absolute path")
    token = os.environ.get("GHRAH_RUNTIME_TOKEN", "")
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not token:
        parser.error("non-loopback hosts require GHRAH_RUNTIME_TOKEN")
    Path(args.database).parent.mkdir(parents=True, exist_ok=True)
    connection = RuntimeConnection(
        RuntimeConnectionConfig(
            enabled=True,
            scope_ref=args.scope,
            database_path=args.database,
            enabled_agent_api=True,
        )
    )
    app = create_runtime_app(connection, allowed_origins=tuple(args.origin), api_token=token)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
