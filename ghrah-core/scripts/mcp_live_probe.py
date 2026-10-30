# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""一把梭：验证 MCP 密钥能从 agentconf 加载、并且真能调通一次。

不用命令行参数、不用交互——在 PyCharm 里改掉下面的 ``MCP_URL``，直接 Run，
屏幕上按顺序给出四步结果和一句结论：

    [1/4] 密钥加载   从 agentconf 按「地址」取密钥，打印来源与掩码
    [2/4] 建立连接   open_client 握手，打印协议与耗时
    [3/4] 工具发现   tools/list
    [4/4] 真实调用   McpRuntime 走完整链路（授权 → 白名单 → 调用）

退出码：0 成功 / 2 配置不全 / 3 连接或发现失败 / 4 调用失败。
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- 配置
# 只需要改这一个：要验证的 MCP 端点。密钥由 agentconf 按地址提供。
MCP_URL = "https://llm-u2idziy1damxsc74.cn-beijing.maas.aliyuncs.com/api/v1/mcps/WebFetch/mcp"

AGENTCONF_DB = ""  # 空 = ~/.agentconf/config.db
SKILL_NAME = "debug/mcp-live-probe"  # 声明这台服务器的技能名
AGENT_SCOPE = "mcp-live-probe"  # 运行时作用域
AUTH_HEADER = "Authorization"  # 记录里没有明文头时，占位符放在这个头里
TIMEOUT_S = 120.0  # 读超时（秒）
TOOL_NAME = ""  # 空 = 自动挑（优先 fetch / read / get / query 这类）
TOOL_ARGUMENTS: dict[str, Any] = {}  # 空 = 按工具 schema 自动造
SAMPLE_ARGUMENT = "https://example.com"  # 自动造参数用的示例值
LOG_TO_FILE = True  # 同时落一份日志，方便回头翻
SHOW_RAW_LIMIT = 1200  # 结果预览的最大字符数

WIDTH = 78


# --------------------------------------------------------------------------- 输出


class _Tee:
    """把写往 stdout 的内容同时抄一份到文件，避免 PyCharm 控制台不好复制。"""

    def __init__(self, stream: Any, handle: Any) -> None:
        self._stream = stream
        self._handle = handle

    def write(self, text: str) -> int:
        with suppress(Exception):
            self._handle.write(text)
            self._handle.flush()
        return self._stream.write(text)

    def flush(self) -> None:
        with suppress(Exception):
            self._handle.flush()
        with suppress(Exception):
            self._stream.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def start_tee() -> str:
    """挑一个能写的日志文件；一个都写不了就只输出到控制台。"""
    if not LOG_TO_FILE:
        return ""
    for path in _log_candidates():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a", encoding="utf-8", buffering=1)
        except OSError:
            continue
        sys.stdout = _Tee(sys.stdout, handle)
        return str(path)
    return ""


def _log_candidates() -> list[Path]:
    here = Path(__file__).resolve().parent
    paths = [here / "mcp_live_probe.log", Path.cwd() / "mcp_live_probe.log"]
    with suppress(OSError):
        paths.insert(0, Path(tempfile.gettempdir()) / "mcp_live_probe.log")
    with suppress(OSError):
        paths.append(Path.home() / "mcp_live_probe.log")
    unique: list[Path] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique


def line(text: str = "") -> None:
    print(text)


def step(number: int, total: int, title: str) -> None:
    print()
    print(f"[{number}/{total}] {title} " + "-" * max(0, WIDTH - 8 - len(title) - len(str(number))))


def ok(label: str, detail: str = "") -> None:
    print(f"      [OK] {label}" + (f" — {detail}" if detail else ""))


def fail(label: str, detail: str = "") -> None:
    print(f"      [!!] {label}" + (f" — {detail}" if detail else ""))


def mask(secret: str | None) -> str:
    """密钥只露头尾，日志里也不会出现明文。"""
    if not secret:
        return "（无）"
    if len(secret) <= 8:
        return "***"
    return f"{secret[:4]}…{secret[-4:]}（{len(secret)} 字符）"


def snippet(text: str, limit: int = SHOW_RAW_LIMIT) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else f"{text[:limit]}…（共 {len(text)} 字符）"


def unwrap(exc: BaseException) -> BaseException:
    """剥掉 anyio TaskGroup 的 ExceptionGroup，拿到真正的错误。"""
    current: BaseException = exc
    while True:
        nested = getattr(current, "exceptions", None)
        if not nested:
            return current
        current = nested[0]


def report_error(exc: BaseException) -> None:
    inner = unwrap(exc)
    status = getattr(inner, "http_status", None)
    detail = f"HTTP {status}" if status else type(inner).__name__
    fail(f"{detail}: {inner}", "")

    body = getattr(inner, "body_snippet", None)
    if body:
        print(f"           响应片段：{snippet(body, 300)}")
    advice = _advice_for(inner, status)
    if advice:
        print(f"           建议：{advice}")


def _advice_for(exc: BaseException, status: int | None) -> str:
    from ghrah.mcp import McpCredentialMissingError, McpToolMissingError

    if isinstance(exc, McpCredentialMissingError):
        return f"agentconf 里没有这个地址的密钥：检查 providers.base_url 是否等于 {MCP_URL}"
    if isinstance(exc, McpToolMissingError):
        return "白名单里没有这个工具，检查 TOOL_NAME 或远端 tools/list 结果。"
    if status in (401, 403):
        return "鉴权被拒：密钥与端点可能不属于同一平台，或凭据没写进请求头。"
    if status == 404:
        return "地址不存在：核对 MCP 端点路径（常见形态 .../api/v1/mcps/<服务名>/mcp）。"
    if status:
        return "把状态码与上面的响应片段一起对照网关文档排查。"
    return ""


# ------------------------------------------------------------------- 工具装配


def to_tool_spec(tool: Any) -> Any:
    """把 SDK 的 Tool 投影成本模块的授权条目（白名单要求描述非空）。"""
    from ghrah.mcp import McpToolSpec, read_only_hint, truncate_description

    description = " ".join((tool.description or "").split()) or f"调用服务器上的 {tool.name}"
    return McpToolSpec(
        name=tool.name,
        description=truncate_description(description),
        input_schema=dict(tool.input_schema or {}),
        read_only=read_only_hint(tool),
    )


async def discover_tools(client: Any) -> list[Any]:
    """跟游标取尽工具清单。"""
    found: list[Any] = []
    cursor: str | None = None
    while True:
        page = await client.list_tools(cursor=cursor)
        found.extend(page.tools)
        cursor = page.next_cursor
        if not cursor:
            return found


_PREFERRED = ("fetch", "read", "get", "query", "search", "open", "parse")
_ARG_KEYS = ("url", "uri", "link", "target_url", "query", "q", "text", "prompt", "keyword")


def pick_tool(tools: list[Any]) -> tuple[Any, str]:
    """没有指定工具名时，挑一个像「读」的工具。"""
    for keyword in _PREFERRED:
        for tool in tools:
            if keyword in tool.name.lower():
                return tool, f"名字含 {keyword!r}"
    return tools[0], "清单第一个"


def build_arguments(schema: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """按 schema 造一份参数，返回 (参数, 依据)。"""
    properties = dict((schema or {}).get("properties") or {})
    if not properties:
        return {}, "该工具无参数"

    for key in _ARG_KEYS:
        if key in properties:
            return {key: SAMPLE_ARGUMENT}, f"schema 属性 {key!r}"

    required = [
        key
        for key in (schema.get("required") or [])
        if (properties.get(key) or {}).get("type") == "string"
    ]
    if len(required) == 1:
        return {required[0]: SAMPLE_ARGUMENT}, f"唯一必填字符串参数 {required[0]!r}"

    strings = [key for key, value in properties.items() if (value or {}).get("type") == "string"]
    if len(strings) == 1:
        return {strings[0]: SAMPLE_ARGUMENT}, f"唯一字符串参数 {strings[0]!r}"

    return {}, f"无法推断参数（可选属性：{', '.join(properties) or '无'}）"


# --------------------------------------------------------------------- 主流程


def load_credentialed_spec() -> tuple[Any, str | None, str]:
    """按地址从 agentconf 装配 spec 与密钥，返回 (spec, 密钥, 来源说明)。"""
    from ghrah.mcp import AgentconfMcpSource

    source = AgentconfMcpSource(db_path=AGENTCONF_DB or None, auth_header=AUTH_HEADER)
    try:
        registered = source.server_name_for_url(MCP_URL)
        spec, credential = source.spec_for_url(MCP_URL, skill_full_name=SKILL_NAME)
    finally:
        source.close()  # spec 与密钥已经取出来了，连接用不着了

    if registered:
        origin = f"agentconf 记录 {registered!r}"
    elif credential:
        origin = "agentconf providers（按 base_url 匹配到，未登记 mcp_servers 记录）"
    else:
        origin = "agentconf 里没有这个地址的任何配置"
    return spec, credential, origin


async def main() -> int:
    from ghrah.mcp import McpRuntime, open_client

    log_path = start_tee()
    print("=" * WIDTH)
    print("ghrah.mcp 一把梭验证：密钥加载 + 真实调用")
    print(f"url       : {MCP_URL}")
    print(f"agentconf : {AGENTCONF_DB or '~/.agentconf/config.db'}")
    print(f"解释器    : {sys.executable}")
    if log_path:
        print(f"日志      : {log_path}")
    print("=" * WIDTH)

    # [1/4] 密钥加载 -------------------------------------------------------
    step(1, 4, "密钥加载")
    try:
        spec, credential, origin = load_credentialed_spec()
    except Exception as exc:  # noqa: BLE001 - 脚本就是要把失败摊开来看
        report_error(exc)
        print("\n结论: [失败] 没能从 agentconf 装配出连接声明")
        return 2

    ok("来源", origin)
    ok("server / 协议", f"{spec.server} / {spec.protocol.value}")
    ok("请求头", json.dumps(dict(spec.headers), ensure_ascii=False))
    if spec.requires_credential:
        if credential:
            ok("密钥", mask(credential) + "  明文不在代码里，只存在于内存")
        else:
            fail("密钥", "spec 需要凭据，但 agentconf 没提供")
            print("\n结论: [失败] 密钥没加载到，先在 agentconf 登记这个地址")
            return 2
    else:
        ok("密钥", "这个端点不要求凭据")

    # [2/4] 建立连接 -------------------------------------------------------
    step(2, 4, "建立连接（握手）")
    started = time.perf_counter()
    try:
        client_ctx = open_client(spec, credential=credential, read_timeout_s=TIMEOUT_S)
        client = await client_ctx.__aenter__()
    except Exception as exc:  # noqa: BLE001
        report_error(exc)
        print("\n结论: [失败] 连接阶段就失败了")
        return 3
    handshake_ms = int((time.perf_counter() - started) * 1000)
    ok("握手完成", f"{handshake_ms} ms")

    # [3/4] 工具发现 -------------------------------------------------------
    step(3, 4, "工具发现（tools/list）")
    try:
        tools = await discover_tools(client)
    except Exception as exc:  # noqa: BLE001
        await client_ctx.__aexit__(None, None, None)
        report_error(exc)
        print("\n结论: [失败] 连上了但取不到工具清单")
        return 3
    await client_ctx.__aexit__(None, None, None)

    if not tools:
        fail("工具清单", "空")
        print("\n结论: [失败] 服务器没有暴露任何工具")
        return 3

    ok("工具数量", str(len(tools)))
    for tool in tools:
        description = snippet(tool.description, 70)
        print(f"           - {tool.name}" + (f"  {description}" if description else ""))

    if TOOL_NAME:
        picked = next((tool for tool in tools if tool.name == TOOL_NAME), None)
        if picked is None:
            fail("指定工具", f"清单里没有 {TOOL_NAME!r}")
            print("\n结论: [失败] TOOL_NAME 写错了")
            return 3
        chosen, why = picked, "脚本里指定的 TOOL_NAME"
    else:
        chosen, why = pick_tool(tools)
    ok("本次调用", f"{chosen.name}（{why}）")

    # [4/4] 真实调用 -------------------------------------------------------
    step(4, 4, "真实调用（授权 → 白名单 → 调用）")
    tool_specs = tuple(to_tool_spec(tool) for tool in tools)
    spec = replace(spec, tools=tool_specs)  # frozen dataclass：白名单靠 replace 补上

    if TOOL_ARGUMENTS:
        arguments, reason = dict(TOOL_ARGUMENTS), "脚本里写死的 TOOL_ARGUMENTS"
    else:
        arguments, reason = build_arguments(dict(chosen.input_schema or {}))
    ok("参数", f"{json.dumps(arguments, ensure_ascii=False)}（{reason}）")
    ok("白名单", f"{len(tool_specs)} 个工具")

    def credentials_for(target: str) -> str | None:
        return credential if target == MCP_URL else None

    runtime = McpRuntime(credentials=credentials_for)
    started = time.perf_counter()
    try:
        await runtime.activate(spec, agent_scope=AGENT_SCOPE)
        activated = runtime.activated_servers(agent_scope=AGENT_SCOPE)
        outcome = await runtime.call(
            agent_scope=AGENT_SCOPE,
            server=spec.server,
            tool=chosen.name,
            arguments=arguments,
        )
    except Exception as exc:  # noqa: BLE001
        report_error(exc)
        print("\n结论: [失败] 握手成功，但调用没成功")
        return 4
    finally:
        await runtime.close()

    elapsed = int((time.perf_counter() - started) * 1000)
    if outcome.is_error:
        fail("调用返回 ERROR", f"{elapsed} ms")
    else:
        ok("调用成功", f"{elapsed} ms，已激活 {activated}")

    print()
    print("      ---- 返回内容 ----")
    for row in (outcome.text or "（空）").splitlines()[:12]:
        print(f"      {row[: WIDTH - 6]}")
    if outcome.structured is not None:
        print("      ---- structured ----")
        print(f"      {snippet(json.dumps(outcome.structured, ensure_ascii=False), 600)}")
    for item in outcome.diagnostics:
        print(f"      [diagnostic] {item}")

    # 一句话结论 -----------------------------------------------------------
    print()
    print("=" * WIDTH)
    if outcome.is_error:
        print("结论: [失败] 链路是通的，但工具自己返回了错误（见上面的返回内容）")
    else:
        print("结论: [成功] 密钥已从 agentconf 按 URL 加载，MCP 调用已调通")
    print("=" * WIDTH)
    return 0 if not outcome.is_error else 4


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
