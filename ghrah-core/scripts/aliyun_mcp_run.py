# 使用 OpenAI SDK + MCP SDK 调用阿里云百炼联网搜索（WebSearch）MCP 服务
import asyncio
import json
import os

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client  # 2.x 名称带下划线
from openai import OpenAI


def input_schema_of(tool):
    """mcp 2.x 字段名是 input_schema，inputSchema 只是序列化别名；1.x 才是 inputSchema。"""
    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
    return schema or {"type": "object", "properties": {}}


async def main():
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        print("错误：请设置环境变量 DASHSCOPE_API_KEY")
        return
    mcp_url = "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp"
    headers = {"Authorization": f"Bearer {api_key}"}
    # 1. 连接 MCP Server，获取可用工具列表
    # 2.x 的 streamable_http_client 不再接收 headers=，需自行构造 httpx2.AsyncClient
    async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(60.0)) as http_client:
        # 2.x 只 yield (read, write) 二元组，没有第三位的 get_session_id
        async with streamable_http_client(mcp_url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                # 转换为 OpenAI function calling 格式
                openai_tools = []
                for tool in tools_result.tools:
                    openai_tools.append(
                        {
                            "type": "function",
                            "function": {
                                "name": tool.name,
                                "description": tool.description or "",
                                "parameters": input_schema_of(tool),
                            },
                        }
                    )
                # 2. 调用 DashScope（OpenAI 兼容接口）
                client = OpenAI(
                    api_key=api_key,
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                )
                messages = [{"role": "user", "content": "搜索一下上古卷轴5mod的最新进展"}]
                print("正在联网搜索...")
                print("=" * 50)
                # 3. 多轮工具调用循环
                while True:
                    response = client.chat.completions.create(
                        model="qwen-max",
                        messages=messages,
                        tools=openai_tools or None,
                    )
                    choice = response.choices[0]
                    msg = choice.message
                    if not msg.tool_calls:
                        print(msg.content)
                        break
                    messages.append(msg)
                    for tc in msg.tool_calls:
                        args = json.loads(tc.function.arguments)
                        result = await session.call_tool(tc.function.name, args)
                        tool_content = ""
                        for block in getattr(result, "content", None) or []:
                            if hasattr(block, "text"):
                                tool_content += block.text
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": tool_content,
                            }
                        )


if __name__ == "__main__":
    asyncio.run(main())
