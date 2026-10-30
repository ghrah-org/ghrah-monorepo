# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""一次真实的 MCP 调用，全程走 ``ghrah.mcp``。

这是 ``scripts/aliyun_mcp_run.py`` 的移植版。那份脚本直接用了 mcp SDK 的
``ClientSession``；这里把同一件事换成我们自己的模块来做，用来验证这条链路真的通：

    连接 → tools/list 发现工具 → 投影成授权白名单 → 激活 runtime → 调用 → 结果投影

与 ``aliyun_mcp_run.py`` 唯一的差别：那边让 qwen-max 决定调哪个工具、传什么参数；
这里省掉 LLM 这一层，直接用下面的内置查询文本调一次（每次结果一样，便于排查）。

在 PyCharm 里直接右键 Run 即可。也可以用 ``--protocol legacy`` 临时换握手方式。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

try:
    from ghrah.mcp import (
        McpConfigError,
        McpConnectionError,
        McpCredentialMissingError,
        McpError,
        McpRequestError,
        McpRuntime,
        McpServerSpec,
        McpToolSpec,
        ProtocolPreference,
        StaticCredentials,
        ToolOutcome,
        no_credentials,
        open_client,
        read_only_hint,
        truncate_description,
    )
except ModuleNotFoundError as exc:  # 让「没装依赖」这件事自己说清楚
    if (exc.name or "").split(".")[0] in {"mcp", "mcp_types"}:
        print("[预检失败] 当前解释器里没有 mcp SDK：")
        print(f"    {sys.executable}")
        print("    安装：uv pip install --python .venv\\Scripts\\python.exe mcp")
        raise SystemExit(2) from exc
    raise


# ============================================================================
# 配置区：只改这里
# ============================================================================

# 阿里云百炼「联网搜索」MCP 端点（aliyun_mcp_run.py 第 23 行原样）
URL = "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp"

# 百炼标准 API Key（sk- 开头 + 32 位 hex），从环境变量读取，不入库。
# 注意：Token Plan 的 key（sk-sp-…）在这个端点上会 401，不要混用。
API_KEY = os.getenv("DASHSCOPE_API_KEY", "")

# 内置查询文本：取自 aliyun_mcp_run.py 第 49 行的 user message
QUERY = "上古卷轴5mod的最新进展"

# 凭据占位符：{{auth}} 会被替换成 API_KEY，其余请求头原样发送
AUTH_HEADER = "Authorization"
AUTH_VALUE = "Bearer {{auth}}"

SERVER_NAME = "dashscope_websearch"
SKILL_NAME = "debug/websearch-once"
AGENT_SCOPE = "mcp-websearch-once"

# modern-first（先试 2026-07-28，不成自动回退 initialize）/ legacy / modern
PROTOCOL = "modern-first"
TIMEOUT_S = 120.0

# 把控制台输出同时写一份到文件（PyCharm 的 Run 窗口我读不到，只能靠日志）
LOG_TO_FILE = True
LOG_FILE = ""  # 留空则写到系统临时目录

_PROTOCOLS = {
    "modern-first": ProtocolPreference.MODERN_FIRST,
    "legacy": ProtocolPreference.LEGACY,
    "modern": ProtocolPreference.MODERN,
}


# ============================================================================
# 日志：把 stdout 复制一份到文件
# ============================================================================


class _Tee:
    """把写入同时转发给多个流；其余属性走第一个流。"""

    def __init__(self, *streams: Any) -> None:
        self._streams = streams

    def write(self, data: str) -> int:
        for stream in self._streams:
            try:
                stream.write(data)
            except Exception:  # noqa: BLE001 - 日志写入不该影响主流程
                pass
        return len(data)

    def flush(self) -> None:
        for stream in self._streams:
            try:
                stream.flush()
            except Exception:  # noqa: BLE001
                pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self._streams[0], name)


def candidate_log_paths() -> list[Path]:
    """按优先级给出候选日志位置。

    临时目录不一定存在（``tempfile.gettempdir()`` 在系统 Temp 被删掉时会直接抛
    ``FileNotFoundError``），所以这里只把它当成**候选之一**，逐个试。
    """
    override = os.environ.get("MCP_PROBE_LOG") or LOG_FILE
    if override:
        return [Path(override).expanduser()]

    name = "mcp_websearch_once.log"
    paths: list[Path] = []
    try:
        paths.append(Path(tempfile.gettempdir()) / name)
    except OSError:  # 含 FileNotFoundError：系统没有可用临时目录
        pass
    # 退化时写在脚本旁边最方便找；再不行才是当前目录、家目录。
    paths.append(Path(__file__).resolve().parent / name)
    paths.append(Path.cwd() / name)
    try:
        paths.append(Path.home() / name)
    except (OSError, RuntimeError):
        pass

    unique: list[Path] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique


def start_tee() -> Path | None:
    """把 stdout/stderr 复制一份到日志文件；一个都写不了就安静放弃。"""
    if not LOG_TO_FILE:
        return None
    for path in candidate_log_paths():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("w", encoding="utf-8", buffering=1)
        except OSError as exc:
            print(f"[提示] 日志路径不可用（{path}）：{exc}")
            continue
        sys.stdout = _Tee(sys.stdout, handle)  # type: ignore[assignment]
        sys.stderr = _Tee(sys.stderr, handle)  # type: ignore[assignment]
        return path
    print("[提示] 没有可写的日志位置，本次只输出到控制台。")
    return None


# ============================================================================
# 小工具
# ============================================================================


def mask(secret: str) -> str:
    if not secret:
        return "(未配置)"
    if len(secret) <= 10:
        return "*" * len(secret)
    return f"{secret[:4]}…{secret[-4:]} (len={len(secret)})"


def make_spec(tools: tuple[McpToolSpec, ...] = ()) -> McpServerSpec:
    """造 spec。headers 里带 ``{{auth}}`` 就意味着「必须有凭据」。"""
    headers = {AUTH_HEADER: AUTH_VALUE} if API_KEY else {}
    return McpServerSpec(
        skill_full_name=SKILL_NAME,
        server=SERVER_NAME,
        url=URL,
        headers=headers,
        tools=tools,
        protocol=_PROTOCOLS[PROTOCOL],
        execution_timeout_ms=int(TIMEOUT_S * 1000),
    )


async def discover_tools(client: Any) -> list[Any]:
    """按游标取尽工具清单，返回 SDK 的 Tool 对象（要的是带 inputSchema 的那种）。"""
    found: list[Any] = []
    cursor: str | None = None
    while True:
        page = await client.list_tools(cursor=cursor)
        found.extend(page.tools)
        cursor = page.next_cursor
        if not cursor:
            return found


def to_tool_spec(tool: Any) -> McpToolSpec:
    """把服务器工具投影成本模块的授权条目（McpToolSpec 要求描述非空）。"""
    description = " ".join((tool.description or "").split()) or f"调用服务器上的 {tool.name}"
    return McpToolSpec(
        name=tool.name,
        description=truncate_description(description),
        input_schema=dict(tool.input_schema or {}),
        read_only=read_only_hint(tool),
    )


_ARG_KEYS = ("query", "q", "search_query", "keyword", "keywords", "text", "prompt", "question")


def build_arguments(schema: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """把内置查询文本塞进 schema 期望的参数里；返回 (参数, 依据说明)。"""
    props = dict((schema or {}).get("properties") or {})
    if not props:
        return {}, "该工具无参数"

    for key in _ARG_KEYS:
        if key in props:
            return {key: QUERY}, f"schema 属性 {key!r}"

    required = [
        key
        for key in (schema.get("required") or [])
        if (props.get(key) or {}).get("type") == "string"
    ]
    if len(required) == 1:
        return {required[0]: QUERY}, f"唯一必填字符串属性 {required[0]!r}"

    strings = [key for key, spec in props.items() if (spec or {}).get("type") == "string"]
    if len(strings) == 1:
        return {strings[0]: QUERY}, f"唯一字符串属性 {strings[0]!r}"

    raise ValueError(
        f"看不懂这个工具的 schema，请手工指定参数：{json.dumps(schema, ensure_ascii=False)}"
    )


def unwrap(exc: BaseException) -> BaseException:
    """剥开 anyio 的 ExceptionGroup，拿到真正的原因。"""
    seen: set[int] = set()
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions and id(exc) not in seen:
        seen.add(id(exc))
        exc = exc.exceptions[0]
    return exc


def print_error(stage: str, exc: BaseException) -> None:
    inner = unwrap(exc)
    print(f"\n[失败] {stage}")
    print(f"       {type(inner).__name__}: {inner}")
    if isinstance(inner, McpRequestError):
        print(f"       kind={inner.kind}  code={inner.code}  uncertain={inner.uncertain}")
    if isinstance(inner, McpCredentialMissingError):
        print("       提示：headers 里有 {{auth}} 却没有 API_KEY。")
    if isinstance(inner, McpConnectionError):
        print("       提示：网络或 URL 不通，先确认这个端点能访问。")
    text = str(inner)
    if "401" in text or "InvalidApiKey" in text or "Unauthorized" in text:
        print("       提示：401 是 key 的问题。MCP 网关在 dashscope.aliyuncs.com，")
        print("             只认百炼标准 key（sk- + 32 hex）；Token Plan 的 key 不行。")


def print_catalog(tools: list[Any]) -> None:
    print(f"       发现 {len(tools)} 个工具：")
    for tool in tools:
        flag = " [只读]" if read_only_hint(tool) else ""
        summary = " ".join((tool.description or "").split())
        print(f"         - {tool.name}{flag}  {truncate_description(summary, 70)}")
        props = dict((tool.input_schema or {}).get("properties") or {})
        if props:
            print(f"           参数：{', '.join(props)}")


def print_outcome(outcome: ToolOutcome, elapsed_ms: float) -> None:
    flag = "ERROR" if outcome.is_error else "OK"
    print(f"\n[结果 {flag}] 耗时 {elapsed_ms:.0f} ms")
    print("-" * 70)
    text = outcome.text or "(空)"
    print(text)
    if outcome.structured:
        print("\n[structured]")
        print(json.dumps(outcome.structured, ensure_ascii=False, indent=2)[:4000])
    for item in outcome.diagnostics:
        print(f"[diagnostic] {item}")


# ============================================================================
# 主流程
# ============================================================================


async def main() -> int:
    log_path = start_tee()
    print("=" * 70)
    print("MCP 真实调用（全程走 ghrah.mcp）")
    print("=" * 70)
    print(f"  url        {URL}")
    print(f"  server     {SERVER_NAME}")
    print(f"  skill      {SKILL_NAME}")
    print(f"  protocol   {PROTOCOL}")
    print(f"  credential {mask(API_KEY)}")
    print(f"  query      {QUERY}")
    if log_path is not None:
        print(f"  log        {log_path}")
    print("-" * 70)

    if not API_KEY:
        print("[配置不全] 请先设置环境变量 DASHSCOPE_API_KEY。")
        return 2

    # ---- 阶段 1：连接 + 发现工具（走 open_client，即本模块的真实建连路径）----
    print("[1/4] 连接服务器并拉取工具清单 …")
    started = time.perf_counter()
    try:
        async with open_client(make_spec(), credential=API_KEY, read_timeout_s=TIMEOUT_S) as client:
            tools = await discover_tools(client)
    except Exception as exc:  # noqa: BLE001 - 调试脚本要看全貌
        print_error("连接或 tools/list 失败", exc)
        return 3
    print(f"       握手完成，耗时 {(time.perf_counter() - started) * 1000:.0f} ms")
    print_catalog(tools)
    if not tools:
        print("[失败] 服务器没有返回任何工具。")
        return 3

    # ---- 阶段 2：选工具，把发现结果投影成授权白名单 ----
    chosen = next((t for t in tools if "search" in t.name.lower()), tools[0])
    try:
        tool_specs = tuple(to_tool_spec(t) for t in tools)
        spec = make_spec(tool_specs)
    except McpConfigError as exc:
        print(f"\n[配置不过关] {exc}")
        return 2
    schema = dict(chosen.input_schema or {})
    try:
        arguments, basis = build_arguments(schema)
    except ValueError as exc:
        print(f"\n[参数构造失败] {exc}")
        return 2
    print(f"\n[2/4] 选定工具 {chosen.name!r}，白名单 {len(tool_specs)} 项")
    print(f"       参数 {json.dumps(arguments, ensure_ascii=False)}（依据：{basis}）")

    # ---- 阶段 3+4：激活 runtime 并调用（走完整链路：授权 → 凭据 → 会话 → 调用）----
    credentials = StaticCredentials({URL: API_KEY}) if API_KEY else no_credentials
    runtime = McpRuntime(credentials=credentials)
    try:
        print("[3/4] 激活 runtime（纯内存，不联网）…")
        await runtime.activate(spec, agent_scope=AGENT_SCOPE)
        print(f"       已激活：{runtime.activated_servers(agent_scope=AGENT_SCOPE)}")
        print("[4/4] 发起调用 …")
        started = time.perf_counter()
        outcome = await runtime.call(
            agent_scope=AGENT_SCOPE,
            server=SERVER_NAME,
            tool=chosen.name,
            arguments=arguments,
        )
        elapsed = (time.perf_counter() - started) * 1000
    except McpError as exc:
        print_error("调用失败", exc)
        return 4
    except Exception as exc:  # noqa: BLE001
        print_error("调用失败（非 McpError）", exc)
        return 4
    finally:
        await runtime.close()

    print_outcome(outcome, elapsed)
    print("-" * 70)
    print("[完成] 链路打通：" if not outcome.is_error else "[完成] 调用返回了工具级错误：")
    print("       open_client → tools/list → McpToolSpec 白名单 → McpRuntime.call")
    if log_path is not None:
        print(f"       日志：{log_path}")
    return 0 if not outcome.is_error else 4


def parse_args(argv: list[str] | None = None) -> Any:
    """只提供最小开关：协议可以临时换，其余配置改文件即可。"""
    import argparse

    parser = argparse.ArgumentParser(description="用 ghrah.mcp 做一次真实的 MCP 调用")
    parser.add_argument("--protocol", choices=sorted(_PROTOCOLS), default=PROTOCOL)
    parser.add_argument("--timeout", type=float, default=TIMEOUT_S)
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    if args.protocol != PROTOCOL:
        PROTOCOL = args.protocol  # type: ignore[misc]
    if args.timeout != TIMEOUT_S:
        TIMEOUT_S = args.timeout  # type: ignore[misc]
    raise SystemExit(asyncio.run(main()))
