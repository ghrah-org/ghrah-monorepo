# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""显式凭据解析工具；生产配置使用 MCP Provider 稳定身份读取。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅类型
    from agentconf import AgentsConfig

__all__ = [
    "AgentconfCredentialResolver",
    "CredentialResolver",
    "StaticCredentials",
    "no_credentials",
]

logger = logging.getLogger(__name__)

CredentialResolver = Callable[[str], "str | None"]
"""``url -> secret``。返回 ``None`` 表示该地址没有登记凭据。

实现者**不应抛异常**表达「没配」：缺少凭据与凭据错误是两件事，混在一起会让使用者
查错方向。
"""


def _normalize_url(url: str) -> str:
    """比较用的地址规范化：去掉尾部斜杠，其余逐字比较。

    只做这一层。过度规范化会让 ``http://a/mcp`` 与 ``https://a/mcp`` 这类真正不同的
    地址误配到同一条凭据。
    """
    return url.rstrip("/")


def _secret_str(value: Any) -> str | None:
    """取出 pydantic ``SecretStr`` / ``Secret`` / 普通 ``str`` 的明文。

    agentconf 的模型字段全部用 ``SecretStr`` 承载（``Provider.base_url``、
    ``Provider.api_key``、``StreamableHTTPConnection.url`` …），所以取值时**不能**
    用 ``isinstance(value, str)`` 判断——它一定是 ``False``，匹配会静默失败。本函数把
    三种形态收敛成 ``str | None``。
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        revealed = getter()
        return revealed if isinstance(revealed, str) else None
    return None


def no_credentials(url: str) -> None:  # noqa: ARG001 - 形状固定的默认实现
    """默认解析器：从不提供凭据。

    适用于纯本地或公开服务器。若配置引用了 ``{{auth}}`` 占位符，建连时会以
    「未配置凭据」明确失败，而不是发出一个注定被拒的请求。
    """
    return None


class StaticCredentials:
    """内存中的凭据表，键为**服务器地址**。

    供测试与嵌入场景使用。生产环境使用 AgentconfMcpSource 按 MCP Provider 稳定身份读取。
    """

    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self._items: dict[str, str] = {
            _normalize_url(url): secret for url, secret in (mapping or {}).items()
        }

    def set(self, url: str, secret: str) -> None:
        self._items[_normalize_url(url)] = secret

    def delete(self, url: str) -> None:
        self._items.pop(_normalize_url(url), None)

    def __call__(self, url: str) -> str | None:
        return self._items.get(_normalize_url(url))


class AgentconfCredentialResolver:
    """退役兼容占位；拒绝用 URL 从模型 Provider 推断 MCP 凭据。"""

    def __init__(
        self,
        config: AgentsConfig | None = None,
        *,
        db_path: str | Path | None = None,
    ) -> None:
        self._config = config
        self._db_path = db_path
        self._owns_config = config is None

    def __call__(self, url: str) -> str | None:
        """旧的 URL／模型 Provider 推断入口明确退役。"""
        from ghrah.mcp.errors import McpConfigError

        raise McpConfigError("URL 凭据推断已退役；请使用 MCP Provider 的确定服务引用")

    def close(self) -> None:
        """关闭本对象打开的 agentconf 连接（外部传入的 config 不关）。"""
        if self._config is not None and self._owns_config:
            closer = getattr(self._config, "close", None)
            if callable(closer):
                closer()
            self._config = None

    def __enter__(self) -> AgentconfCredentialResolver:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
