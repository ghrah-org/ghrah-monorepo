# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""凭据解析：从「MCP 服务器地址」到「密钥明文」。

**密钥存在 agentconf 里，不存在本模块、也不存在技能的 yaml 里。**

关联方式是**地址**：在 agentconf 中登记一条 provider 记录，把 MCP 服务器的 url 填进
``base_url``、把密钥填进 ``api_key``。本模块按地址把它们对上：

```
agentconf.providers                      MCP 模块
┌──────────────────────────────┐        ┌────────────────────────┐
│ name: sentry-mcp             │        │ spec.url               │
│ base_url: https://…/mcp   ←──┼───────→│ spec.headers 里的       │
│ api_key:  sntrys_xxx         │  地址   │ {{auth}} 占位符         │
└──────────────────────────────┘  相等   └────────────────────────┘
```

这样做的三个结果：

* **技能包里没有密钥**。yaml 只有 ``{{auth}}`` 占位符，技能可以被自由分发与提交。
* **凭据轮换不动技能**。改 agentconf 即可，所有引用该地址的技能立即生效。
* **地址即边界**。一个技能想拿到某个密钥，必须声明与它完全相同的目的地址——而请求
  也正是发往那个地址，所以密钥不可能被送到别处。

代价是：如果两个不同技能指向**同一个** MCP 地址，它们会共用同一条凭据。这是把地址
作为关联键的直接后果，在"一台服务器一套凭据"的常规场景下没有问题。
"""

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

    供测试与嵌入场景使用。生产环境应使用 :class:`AgentconfCredentialResolver`，
    让密钥只存在于 agentconf 中。
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
    """从 agentconf 的 provider 记录里按地址取密钥。

    匹配规则：provider 的 ``base_url`` 与 MCP 服务器的 url 相等（忽略尾部斜杠）。

    首次取值时才打开 agentconf 数据库，因此构造本对象没有副作用；随后的取值复用同一个
    连接。用完请调用 :meth:`close`，或把它当作上下文管理器使用。

    读取失败（数据库损坏、schema 不符）只记 warning 并当作「没配」——凭据读取不该让
    整个 agent 起不来，而缺失会在建连时以明确错误暴露。
    """

    def __init__(
        self,
        config: AgentsConfig | None = None,
        *,
        db_path: str | Path | None = None,
    ) -> None:
        self._config = config
        self._db_path = db_path
        self._owns_config = config is None

    def _ensure_config(self) -> AgentsConfig | None:
        if self._config is not None:
            return self._config
        try:
            from agentconf import AgentsConfig

            self._config = AgentsConfig(self._db_path) if self._db_path else AgentsConfig()
        except Exception:  # noqa: BLE001 - 读取失败按「没配」处理
            logger.warning("cannot open agentconf database for MCP credentials", exc_info=True)
            return None
        return self._config

    def __call__(self, url: str) -> str | None:
        config = self._ensure_config()
        if config is None:
            return None

        wanted = _normalize_url(url)
        try:
            providers = config.list_providers()
        except Exception:  # noqa: BLE001
            logger.warning("cannot list agentconf providers", exc_info=True)
            return None

        for provider in providers:
            # base_url / api_key 都是 SecretStr：必须解包后再比较。
            base_url = _secret_str(getattr(provider, "base_url", None))
            if base_url is None or _normalize_url(base_url) != wanted:
                continue
            secret = _secret_str(getattr(provider, "api_key", None))
            if secret:
                return secret
        return None

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
