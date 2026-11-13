"""新契约联调宿主：真实 Subject/Core/SQLite，仅显式替换模型为确定性回复。"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from pathlib import Path
from typing import Any

import uvicorn
from ghrah.chat.content import TextBlock
from ghrah.chat.format import LLMResponse
from ghrah.core.runtime import CoreRuntime, RuntimeConfig
from ghrah.subject.runtime_connection import RuntimeConnection, RuntimeConnectionConfig
from ghrah.subject.runtime_http import create_runtime_app


class MockModel:
    """不读取模型凭据、不发送网络请求；协议和持久化仍使用正式实现。"""

    model = "runtime-mock"

    def configure_tools(self, tools: Any) -> None:
        """接收 Core 生成的实际工具面。"""

    def apply_model_overrides(self, overrides: Any) -> None:
        """接受显式模型参数，保留与正式模型相同的装配接缝。"""

    async def generate(self, messages: Any, tools: Any = None) -> LLMResponse:
        """返回可识别的模拟回复，投递推进由真实 MessageManager 执行。"""
        await asyncio.sleep(0.02)
        return LLMResponse(content_blocks=[TextBlock(text="RUNTIME_MOCK_OK")])


def main() -> None:
    """使用隔离数据库启动新契约 mock；可显式指定数据库验证重启恢复。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4114)
    parser.add_argument("--scope", default="observer-dev")
    parser.add_argument("--database")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ghrah-runtime-mock-") as directory:
        database = (
            str(Path(args.database).resolve())
            if args.database
            else str(Path(directory) / "core.db")
        )
        Path(database).parent.mkdir(parents=True, exist_ok=True)
        runtime = CoreRuntime(
            RuntimeConfig(scope_ref=args.scope, database_path=database, enabled_agent_api=True),
            llm_factory=lambda _: MockModel(),
        )
        connection = RuntimeConnection(
            RuntimeConnectionConfig(enabled=True, scope_ref=args.scope), runtime
        )
        app = create_runtime_app(
            connection,
            allowed_origins=(
                "http://127.0.0.1:5174",
                "http://localhost:5174",
                "http://127.0.0.1:5181",
            ),
        )
        uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
