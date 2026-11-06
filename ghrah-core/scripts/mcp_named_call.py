# SPDX-License-Identifier: Apache-2.0
"""按明确的 agentconf Server 与工具做人工烟测；不发现工具、不猜测凭据。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ghrah.mcp import AgentconfMcpSource, McpRuntime, McpToolSpec
from ghrah.mcp.errors import McpError, McpRequestError


async def run(args: argparse.Namespace) -> int:
    """完成一次受信人工调用，不向日志打印配置或密钥。"""
    arguments = json.loads(Path(args.arguments_file).read_text()) if args.arguments_file else {}
    schema = json.loads(Path(args.schema_file).read_text()) if args.schema_file else {}
    if not isinstance(arguments, dict) or not isinstance(schema, dict):
        raise ValueError("参数和 schema 必须为 JSON 对象")
    with AgentconfMcpSource(db_path=args.db_path) as source:
        spec = source.connection_spec(
            args.server,
            skill_full_name="diagnostic/explicit-call",
            tools=(McpToolSpec(args.tool, "用户明确指定的人工烟测工具", schema, args.read_only),),
        )
        runtime = McpRuntime(
            credentials_for_spec=source.credential_for_spec,
            headers_for_spec=source.headers_for_spec,
        )
        try:
            await runtime.activate(spec, agent_scope="diagnostic/explicit-call")
            result = await runtime.call(
                agent_scope="diagnostic/explicit-call",
                server=spec.server,
                tool=args.tool,
                arguments=arguments,
            )
            print(result.render_for_model())
            return 1 if result.is_error else 0
        finally:
            await runtime.close()


def main() -> int:
    """解析明确的服务、工具与可选参数文件。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("server", help="agentconf 的 MCP Server 名称")
    parser.add_argument("tool", help="远端工具的准确名称")
    parser.add_argument("--db-path")
    parser.add_argument("--arguments-file")
    parser.add_argument("--schema-file")
    parser.add_argument("--read-only", action="store_true", help="使用者明确确认工具只读")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except McpRequestError as error:
        print("MCP 请求失败；效果不确定，请先核验外部状态" if error.uncertain else "MCP 请求失败")
        return 2
    except McpError, ValueError, OSError:
        print("MCP 配置或输入不可用，请检查服务、认证与参数文件")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
