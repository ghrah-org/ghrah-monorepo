# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""一次能力调用的值对象：成员引用、受信身份与调用本身。

为什么拆成三个对象：

- **成员引用**固定“这次调用是谁、哪一版实现”。工具名可以被表面命名改写成
  别名，实例可以被重装；调用方只认装配时捕获的成员键与版本摘要，不从模型
  传来的名字反推实现。
- **受信身份**由装配者绑定（项目、Agent、run 与显示名）。它不能从工具参数
  读取，否则模型可以用一个字符串改掉自己的身份与作用域。
- **调用对象**只带成员、参数、``tool_call_id`` 与身份，不提供任何
  ``context.xxx`` 属性代理。参数只有一个来源：``arguments``。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

__all__ = [
    "InvocationIdentity",
    "MemberReference",
    "OperationCall",
]


@dataclass(frozen=True)
class MemberReference:
    """一次调用固定的成员引用。

    Attributes:
        instance_id: 已登记的执行实例标识（``ghrah-fs`` 这类稳定键）。
        alias: 能力别名（Agent 内唯一的容器名）；旧模式为空。
        member: 成员键（包内 Operation 键）；旧模式为 Ability 名。
        version_digest: 启动时固定的实现版本摘要；旧模式为空。
    """

    instance_id: str = ""
    alias: str = ""
    member: str = ""
    version_digest: str = ""


@dataclass(frozen=True)
class InvocationIdentity:
    """受信调用身份：由装配者绑定，不从工具参数读取。

    Attributes:
        project_id: 项目标识（集群/多项目隔离用）。
        agent: 稳定 Agent 标识（关系库与 run 归属键）。
        run: 当前受信 run；空串表示装配点未接线（旧模式）。
        agent_name: 显示名称（模型可见的工具表与消息里的人类可读名）。
    """

    project_id: str = ""
    agent: str = ""
    run: str = ""
    agent_name: str = ""


@dataclass(frozen=True)
class OperationCall:
    """一次调用的值对象：成员、参数、调用标识与受信身份。

    这是 Operation 与 Hook 看到的唯一调用形态：

    - 参数只在 ``arguments``；
    - 成员只在 ``member``；
    - 身份只在 ``identity``，且由装配者写入。

    它刻意不提供 ``tool_args`` / ``accumulated_data`` / ``agent_name`` 这类
    旧门面属性：一旦存在回退路径，“所有入口同一语义”就无法证明。
    """

    member: MemberReference = field(default_factory=MemberReference)
    arguments: dict[str, Any] = field(default_factory=dict)
    tool_call_id: str = ""
    identity: InvocationIdentity = field(default_factory=InvocationIdentity)

    def with_member(self, member: MemberReference) -> OperationCall:
        """返回固定为给定成员引用的调用。

        工具名是模型可见的别名；执行边界把它改写为装配时捕获的成员引用，
        之后的 Hook 与 Operation 都只认成员身份。

        Args:
            member: 装配时固定的成员引用。

        Returns:
            新的调用对象（原对象不变）。
        """
        return replace(self, member=member)
