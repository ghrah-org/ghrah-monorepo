# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""agentconf 桥接：把 agentconf 里登记的 MCP 服务器读成可执行的 :class:`McpServerSpec`。

**为什么需要它。** 生产环境里 MCP 的密钥不写在技能 yaml 里，而是登记在 agentconf：
``mcp_servers`` 表存连接（``url`` / ``headers`` / ``connect_timeout`` /
``protocol_version_hint`` / ``credential_ref``），``providers`` 表存
``base_url + api_key``。本模块把两张表拼成一份 spec，并**把密钥从 spec 里拿出去**：
headers 中命中密钥的值被替换成 ``{{auth}}`` 占位符，明文只经过本对象这一个出口。

```
agentconf.providers             agentconf.mcp_servers        McpServerSpec
┌────────────────────┐         ┌───────────────────────┐    ┌──────────────────────┐
│ name: 阿里云MCP     │         │ name: 网页解析mcp      │    │ server: mcp           │
│ base_url: …/mcp  ──┼──地址──→│ url: …/mcp            ├───→│ url: …/mcp            │
│ api_key: sk-ws-… ──┼──密钥──→│ headers: {Authorization:│   │ headers:             │
└────────────────────┘         │   "Bearer sk-ws-…"}   │    │  {Authorization:      │
                               │ credential_ref: null  │    │   "Bearer {{auth}}"}} │
                               └───────────────────────┘    └──────────────────────┘
                                                                    │
                                    AgentconfMcpSource(url) ────────┘ 建连时注入明文
```

于是 spec 可以随便打印、写日志、跨进程传，明文只在
:func:`ghrah.mcp.client.open_client` 真正建连的那一刻出现。

**凭据来源的两条约定**（按优先级）：

1. ``credential_ref`` 非空时，视为 **agentconf provider 的名字**，取它的 ``api_key``。
   agentconf 本身刻意不解释这个字段——「凭据引用是宿主可识别的描述，本库不读环境/
   文件或凭据服务」——所以这条语义由本模块定义。
2. 否则按**地址**匹配：``provider.base_url`` 与 MCP 的 ``url`` 相等（忽略尾部斜杠）。

两条都取不到时，headers 里的字面值原样保留（例如公开服务器，或把密钥直接写在
headers 里的简单配置）；此时 spec 不含占位符，建连不需要凭据。

**不做的事。** 不协商协议、不发请求、不缓存解析结果。连接与调用仍然是
:func:`ghrah.mcp.client.open_client` 与 :class:`ghrah.mcp.runtime.McpRuntime` 的职责。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

from agentconf import (
    AgentsConfig,
    AgentsConfigError,
    GenericMCPConnection,
    ResolvedMCPServer,
    StdioConnection,
    StreamableHTTPConnection,
)

from ghrah.mcp.credentials import _normalize_url, _secret_str
from ghrah.mcp.errors import McpConfigError
from ghrah.mcp.identity import MAX_SERVER_NAME_LEN
from ghrah.mcp.spec import (
    AUTH_PLACEHOLDER,
    DEFAULT_CONNECT_TIMEOUT_MS,
    DEFAULT_EXECUTION_TIMEOUT_MS,
    DEFAULT_MAX_PAGES,
    DEFAULT_MAX_RESULT_BYTES,
    McpServerSpec,
    McpToolSpec,
    ProtocolPreference,
)

__all__ = ["AgentconfMcpSource", "load_spec"]

logger = logging.getLogger(__name__)

#: ``credential_ref`` → provider 名字；与 ``protocol_version_hint`` 的别名表。
_PROTOCOL_ALIASES: dict[str, ProtocolPreference] = {
    "auto": ProtocolPreference.MODERN_FIRST,
    "modern-first": ProtocolPreference.MODERN_FIRST,
    "modern_first": ProtocolPreference.MODERN_FIRST,
    "legacy": ProtocolPreference.LEGACY,
    "handshake": ProtocolPreference.LEGACY,
    "modern": ProtocolPreference.MODERN,
    ProtocolPreference.MODERN.value: ProtocolPreference.MODERN,
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")

#: 没有显式 ``auth_header`` 时放密钥的那个头。与 ``{{auth}}`` 的约定一致。
_DEFAULT_AUTH_HEADER = "Authorization"


def _host_of(url: str) -> str:
    """从地址里取主机名，用于在「没有登记记录」时造一个合法的 ``server`` 名。

    取主机而不是路径，是因为同一台网关上的多个端点通常只差路径，而 ``server``
    在授权、日志与 ``McpRuntime`` 索引里承担的是「哪一台服务」的语义。
    """
    host = urlsplit(url).hostname or ""
    return host or "mcp"


def _server_name_from(name: str) -> str:
    """把 agentconf 里的任意名字压成合法的 MCP 服务器名。

    agentconf 的服务器名是自由文本（``阿里云网页解析mcp``），而 spec 里的
    ``server`` 会出现在模型面前，必须满足 ``^[a-z][a-z0-9_]{0,31}$``。中文名压完
    通常为空，此时退化成 ``mcp``——它每次激活的作用域不同，不会互相冲突。
    """
    slug = _SLUG_RE.sub("_", name.strip().lower()).strip("_")
    if not slug:
        return "mcp"
    if not slug[0].isalpha():
        slug = f"mcp_{slug}"
    return slug[:MAX_SERVER_NAME_LEN].rstrip("_") or "mcp"


def _protocol_from_hint(hint: str | None) -> ProtocolPreference:
    """把 agentconf 的 ``protocol_version_hint`` 映射成协商偏好。

    agentconf 明写「协议版本提示不是协商结果」，所以未知提示一律回到默认的
    ``modern-first``，而不是当成硬性要求。
    """
    if not hint:
        return ProtocolPreference.MODERN_FIRST
    preference = _PROTOCOL_ALIASES.get(hint.strip().lower())
    if preference is None:
        logger.debug("unknown protocol_version_hint %r, fallback to modern-first", hint)
        return ProtocolPreference.MODERN_FIRST
    return preference


class AgentconfMcpSource:
    """按名字读取 agentconf 中的 MCP 服务器，产出 spec 与密钥。

    首次读取时才打开数据库，因此构造本对象没有副作用；随后的读取复用同一个连接。
    用完请调用 :meth:`close`，或把它当作上下文管理器。

    本对象同时是 :data:`~ghrah.mcp.credentials.CredentialResolver`
    （``__call__(url) -> secret``），可以直接传给
    ``McpRuntime(credentials=source)``。

    Args:
        config: 复用已有的 :class:`agentconf.AgentsConfig`（不会被本对象关闭）。
        db_path: 自建连接时的库路径，默认 agentconf 的 ``~/.agentconf/config.db``。
        auth_header: 取到密钥、但 headers 里**没有**任何头包含它时，用它承载
            ``{{auth}}`` 占位符。为 ``None``（默认）时这种情况视为配置错误。
        auth_template: 上述注入的头值模板，默认 ``"Bearer {{auth}}"``。
    """

    def __init__(
        self,
        config: AgentsConfig | None = None,
        *,
        db_path: str | Path | None = None,
        auth_header: str | None = None,
        auth_template: str = f"Bearer {AUTH_PLACEHOLDER}",
    ) -> None:
        self._config = config
        self._db_path = db_path
        self._owns_config = config is None
        self._auth_header = auth_header
        self._auth_template = auth_template

    # ---------------------------------------------------------------- 生命周期

    def _ensure_config(self) -> AgentsConfig | None:
        if self._config is not None:
            return self._config
        try:
            self._config = AgentsConfig(self._db_path) if self._db_path else AgentsConfig()
        except Exception:  # noqa: BLE001 - 打不开库按「没有配置」处理
            logger.warning("cannot open agentconf database for MCP specs", exc_info=True)
            return None
        return self._config

    def close(self) -> None:
        """关闭本对象打开的 agentconf 连接（外部传入的 config 不关）。"""
        if self._config is not None and self._owns_config:
            self._config.close()
            self._config = None

    def __enter__(self) -> AgentconfMcpSource:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    # ------------------------------------------------------------------ 发现

    def names(self) -> tuple[str, ...]:
        """agentconf 里登记的全部 MCP 服务器名（保持登记顺序）。"""
        config = self._ensure_config()
        if config is None:
            return ()
        try:
            return tuple(server.name for server in config.list_mcp_servers())
        except AgentsConfigError:
            logger.warning("cannot list agentconf mcp servers", exc_info=True)
            return ()

    def _resolve(self, name: str) -> ResolvedMCPServer:
        config = self._ensure_config()
        if config is None:
            raise McpConfigError(
                f"agentconf 数据库不可用，读不到 MCP 服务器 {name!r}"
            )
        try:
            return config.resolve_mcp_server(name)
        except AgentsConfigError as exc:
            available = ", ".join(self.names()) or "（空）"
            raise McpConfigError(
                f"agentconf 中没有可用的 MCP 服务器 {name!r}；已登记：{available}"
            ) from exc

    # ------------------------------------------------------------------ 凭据

    def credential_for_url(self, url: str) -> str | None:
        """按地址在 agentconf 的 provider 里找密钥，找不到返回 ``None``。"""
        config = self._ensure_config()
        if config is None:
            return None
        wanted = _normalize_url(url)
        try:
            providers = config.list_providers()
        except AgentsConfigError:
            logger.warning("cannot list agentconf providers", exc_info=True)
            return None
        for provider in providers:
            base_url = _secret_str(provider.base_url)
            if base_url is None or _normalize_url(base_url) != wanted:
                continue
            secret = _secret_str(provider.api_key)
            if secret:
                return secret
        return None

    def credential_for(self, name: str) -> str | None:
        """取某台 MCP 服务器的密钥：``credential_ref`` 优先，其次按地址匹配。"""
        return self._credential_for_record(self._resolve(name))

    def _credential_for_record(self, record: ResolvedMCPServer) -> str | None:
        connection = record.connection
        if not isinstance(connection, StreamableHTTPConnection):
            return None

        reference = _secret_str(connection.credential_ref)
        if reference:
            config = self._ensure_config()
            if config is not None:
                try:
                    secret = _secret_str(config.get_provider(reference).api_key)
                except AgentsConfigError:
                    logger.debug(
                        "credential_ref %r 不是已登记的 provider 名，回退到按地址匹配",
                        reference,
                    )
                else:
                    if secret:
                        return secret

        url = _secret_str(connection.url)
        if not url:
            return None
        return self.credential_for_url(url)

    def __call__(self, url: str) -> str | None:
        """作为 :data:`~ghrah.mcp.credentials.CredentialResolver` 使用。"""
        return self.credential_for_url(url)

    # -------------------------------------------------------------------- spec

    def spec(
        self,
        name: str,
        *,
        skill_full_name: str,
        server: str | None = None,
        tools: Sequence[McpToolSpec] = (),
        protocol: ProtocolPreference | None = None,
        execution_timeout_ms: int = DEFAULT_EXECUTION_TIMEOUT_MS,
        max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
        max_pages: int = DEFAULT_MAX_PAGES,
        credential: str | None = None,
    ) -> McpServerSpec:
        """把一条 ``mcp_servers`` 记录翻译成 :class:`McpServerSpec`。

        密钥不进入 spec：headers 中被命中的那段被替换成 ``{{auth}}``，明文由
        :meth:`credential_for` 单独给出。

        Args:
            name: agentconf 里的服务器名。
            skill_full_name: 声明它的技能全名（激活与授权的作用域）。
            server: spec 里的服务器名；默认由 ``name`` 压成合法标识符。
            tools: 工具白名单（通常来自技能配置）。
            protocol: 覆盖 ``protocol_version_hint`` 得到的协商偏好。
            credential: 外部已经拿到的密钥；给了就不再读 agentconf。

        Raises:
            McpConfigError: 记录不存在、传输方式不支持、url 缺失，或出现了 spec
                无法表达的多凭据头。
        """
        record = self._resolve(name)
        connection = record.connection

        if isinstance(connection, StdioConnection):
            raise McpConfigError(
                f"MCP 服务器 {name!r} 使用 stdio 传输；本包只支持 streamable-http"
            )
        if isinstance(connection, GenericMCPConnection):
            raise McpConfigError(
                f"MCP 服务器 {name!r} 的 transport={record.transport!r} 没有内置适配"
            )

        url = _secret_str(connection.url)
        if not url:
            raise McpConfigError(f"MCP 服务器 {name!r} 缺少 url")

        literal = dict(connection.headers.get_secret_value() or {})
        secret = credential if credential is not None else self._credential_for_record(record)
        headers = self._templated_headers(literal, secret, name=name)

        return McpServerSpec(
            skill_full_name=skill_full_name,
            server=server or _server_name_from(name),
            url=url,
            headers=headers,
            tools=tuple(tools),
            protocol=protocol or _protocol_from_hint(connection.protocol_version_hint),
            connect_timeout_ms=_connect_timeout_ms(connection.connect_timeout),
            execution_timeout_ms=execution_timeout_ms,
            max_result_bytes=max_result_bytes,
            max_pages=max_pages,
        )

    def spec_and_credential(
        self, name: str, **kwargs: object
    ) -> tuple[McpServerSpec, str | None]:
        """一次拿到 spec 与它需要的密钥（``spec`` 已含 ``credential`` 逻辑）。"""
        credential = self.credential_for(name)
        spec = self.spec(name, credential=credential, **kwargs)  # type: ignore[arg-type]
        return spec, credential

    # -------------------------------------------------------------- 按地址装配

    def server_name_for_url(self, url: str) -> str | None:
        """找出 url 与 ``url`` 相同的已登记服务器名；没有则返回 ``None``。

        地址是运行时唯一确定的东西（技能配置、环境变量、用户输入都可能只给出
        地址），名字则只是 agentconf 里的标签。按地址反查名字，才能复用记录里
        的请求头、超时与协议提示。
        """
        target = _normalize_url(url)
        config = self._ensure_config()
        if config is None:
            return None
        try:
            servers = config.list_mcp_servers()
        except AgentsConfigError:
            logger.warning("cannot list agentconf mcp servers", exc_info=True)
            return None

        for server in servers:
            try:
                record = config.resolve_mcp_server(server.name)
            except AgentsConfigError:
                continue
            candidate = _secret_str(getattr(record.connection, "url", None))
            if candidate and _normalize_url(candidate) == target:
                return server.name
        return None

    def spec_for_url(
        self,
        url: str,
        *,
        skill_full_name: str,
        server: str | None = None,
        tools: Sequence[McpToolSpec] = (),
        protocol: ProtocolPreference | None = None,
        execution_timeout_ms: int = DEFAULT_EXECUTION_TIMEOUT_MS,
        max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> tuple[McpServerSpec, str | None]:
        """按地址装配 ``(spec, 密钥)``。

        地址上登记过服务器时，沿用记录里的请求头、``connect_timeout`` 与
        ``protocol_version_hint``；没登记过也能用——退化成「只带凭据头的最小
        spec」，密钥仍按地址从 provider 的 ``base_url`` 关联。后者是为了让「只知
        道地址」的调用方不必先去 agentconf 补一条 mcp_servers 记录。
        """
        name = self.server_name_for_url(url)
        if name is not None:
            return self.spec_and_credential(
                name,
                skill_full_name=skill_full_name,
                server=server,
                tools=tools,
                protocol=protocol,
                execution_timeout_ms=execution_timeout_ms,
                max_result_bytes=max_result_bytes,
                max_pages=max_pages,
            )

        credential = self.credential_for_url(url)
        headers: dict[str, str] = {}
        if credential is not None:
            # 没有记录可依据，只能按约定的头名放占位符；头名由 auth_header 指定。
            headers[self._auth_header or _DEFAULT_AUTH_HEADER] = self._auth_template
        return (
            McpServerSpec(
                skill_full_name=skill_full_name,
                server=server or _server_name_from(_host_of(url)),
                url=url,
                headers=headers,
                tools=tuple(tools),
                protocol=protocol or ProtocolPreference.MODERN_FIRST,
                execution_timeout_ms=execution_timeout_ms,
                max_result_bytes=max_result_bytes,
                max_pages=max_pages,
            ),
            credential,
        )

    def _templated_headers(
        self, literal: dict[str, str], secret: str | None, *, name: str
    ) -> dict[str, str]:
        """把 headers 里出现的密钥替换成占位符；产出可直接交给 spec 的映射。

        三种情形：配置里已经写了 ``{{auth}}`` 占位符（原样保留）；配置里写着明文
        密钥（就地替换掉）；配置里没有密钥（此时靠 ``auth_header`` 指定送到哪个头，
        密钥留给运行期的 :data:`CredentialResolver` 提供）。
        """
        headers: dict[str, str] = {}
        hits: list[str] = []
        for header, value in literal.items():
            if AUTH_PLACEHOLDER in value:
                headers[header] = value
                hits.append(header)
            elif secret and secret in value:
                headers[header] = value.replace(secret, AUTH_PLACEHOLDER)
                hits.append(header)
            else:
                headers[header] = value

        if len(hits) > 1:
            raise McpConfigError(
                f"MCP 服务器 {name!r} 的多个请求头都带凭据"
                f"（{', '.join(hits)}）；spec 只允许一个 {AUTH_PLACEHOLDER} 占位符"
            )
        if not hits:
            if self._auth_header is None:
                if secret is None:
                    # 既没有密钥、也没指定头：按字面 headers 直通（免鉴权服务器）。
                    return headers
                raise McpConfigError(
                    f"MCP 服务器 {name!r} 的密钥来自 provider，但 headers 里没有任何头"
                    "包含它；请把密钥写进某个头，或构造 AgentconfMcpSource 时指定"
                    " auth_header"
                )
            headers[self._auth_header] = self._auth_template
        return headers


def _connect_timeout_ms(timeout: float | None) -> int:
    """agentconf 的秒级超时（可空）→ spec 的毫秒级（必须为正）。"""
    if not timeout:
        return DEFAULT_CONNECT_TIMEOUT_MS
    return max(1, round(timeout * 1000))


def load_spec(
    name: str,
    *,
    skill_full_name: str,
    db_path: str | Path | None = None,
    **kwargs: object,
) -> tuple[McpServerSpec, str | None]:
    """一次性读取：打开 agentconf、翻译 spec、取密钥、关闭连接。

    适合脚本与一次性调用；长期运行的对象请直接持有 :class:`AgentconfMcpSource`。
    """
    with AgentconfMcpSource(db_path=db_path) as source:
        return source.spec_and_credential(name, skill_full_name=skill_full_name, **kwargs)