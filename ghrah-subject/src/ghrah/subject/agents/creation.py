# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""一次性创建服务：模板 → 装配输入 → Core 创建 → ``AgentRecord``。

**授权不在这里写**。Core 的启动核定 ``StartupMaterializer.materialize``
（``ability/access/startup.py:609-659``）在 spawn 内部就 ``ensure_agent``、按
``resolved.abilities`` 登记实例并写入授权，Subject 只需给出 ``members[]``。
把授权搬到 Subject 反而会造出第二条写授权路径。

因此本服务只做四件事：
1. 按 ``template_id + revision`` 取模板快照（不回读当前版本）；
2. 渲染成 ``AgentAssemblySpec``（只含已授予/可授予成员）；
3. 调 Core 的 ``agents.create_assembly``（新建或恢复由 ``agent_id`` 决定）；
4. 组装 ``AgentRecord``（身份 + 展示快照 + 来源 + 编排状态），不存配置。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from ghrah.protocol.views.core import AgentAssemblySpec, AgentStartView

from ghrah.subject.agents.render import GrantedPackage, RenderError, render_assembly
from ghrah.subject.project.models import (
    AgentOrigin,
    AgentPresentation,
    AgentRecord,
)
from ghrah.subject.template_store.models import AgentTemplateConfig, AgentTemplateRecord

__all__ = [
    "CreateAgentRequest",
    "CreatedAgent",
    "CreationError",
    "CREATE_TIMEOUT_SECONDS",
    "TemplateSource",
    "create_agent",
]

logger = logging.getLogger(__name__)

# 创建必须**有界**：Core 的 spawn 一旦卡住，未设期限会让 HTTP 请求无限挂起，
# 还会连带 `distro stop` 收不回来。超时后把仍在跑的协程栈打出来，便于定位卡点。
CREATE_TIMEOUT_SECONDS = 45.0


def _dump_pending_tasks() -> None:
    """超时点打印所有存活协程栈（异步侧没有别的可观测入口）。"""
    tasks = [task for task in asyncio.all_tasks() if not task.done()]
    logger.error("create_agent 超时：仍有 %d 个协程在跑", len(tasks))
    for task in tasks:
        logger.error("-- task %s", task.get_name())
        for frame in task.get_stack():
            logger.error(
                "   %s:%s in %s",
                frame.f_code.co_filename,
                frame.f_lineno,
                frame.f_code.co_name,
            )


class TemplateSource(Protocol):
    """按 ``(template_id, revision)`` 读模板版本的只读口径。"""

    async def get_version(self, template_id: str, revision: int) -> AgentTemplateRecord | None:
        """取指定版本；不存在返回 ``None``。"""
        ...


@dataclass(frozen=True)
class CreateAgentRequest:
    """创建请求：模板身份 + 实例名 + 期望状态 + 初始绑定成员。"""

    template_id: str
    revision: int
    name: str
    desired_state: str = "running"
    initial_bindings: tuple[str, ...] | None = None
    """初始绑定的成员全名；``None`` 表示按模板默认轴全部绑定。"""


@dataclass(frozen=True)
class CreatedAgent:
    """创建结果：Core 的身份 + 待落库的 ``AgentRecord``。"""

    agent_id: str
    name: str
    recovery_mode: str
    record: AgentRecord


class CreationError(RuntimeError):
    """创建不可完成；``code`` 是稳定领域码，供 REST 映射。

    ``template_not_found`` / ``template_archived`` / ``assembly_not_ready`` /
    ``missing_workspace`` / ``invalid_path`` / ``invalid_binding`` /
    ``agent_create_failed``。
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _record_from_template(
    config: AgentTemplateConfig,
    *,
    template_id: str,
    revision: int,
    agent_id: str,
    name: str,
    desired_state: str,
    now: datetime,
) -> AgentRecord:
    """把模板的展示字段快照进记录；来源只留 ``template_id + revision``。"""
    metadata = config.metadata
    return AgentRecord(
        agent_id=agent_id,
        project_id="",  # 由调用方（ProjectManager）补齐，服务不越权改归属
        name=name,
        presentation=AgentPresentation(
            title=metadata.title,
            description=metadata.description,
            tags=list(metadata.tags),
            icon=metadata.icon,
            deprecated=metadata.deprecated,
        ),
        origin=AgentOrigin(template_id=template_id, revision=revision),
        desired_state=desired_state,  # type: ignore[arg-type]
        runtime_status="running",
        last_error="",
        created_at=now,
        updated_at=now,
    )


async def create_agent(
    request: CreateAgentRequest,
    *,
    templates: TemplateSource,
    workspaces: Mapping[str, str],
    available: list[GrantedPackage],
    create: Callable[[AgentAssemblySpec], Awaitable[AgentStartView]],
    now: datetime | None = None,
) -> CreatedAgent:
    """按模板创建 Agent，返回身份与待落库的记录。

    Args:
        request: 模板身份、实例名与期望状态。
        templates: 模板快照读取口径（按 revision，不取当前版本）。
        workspaces: ``workspace_role`` → 绝对根。
        available: 该项目当前可授予的包与成员事实（由端口能力视图读出）。
        create: Core 的创建入口（通常包一层 ``port.agents.create_assembly``）。
        now: 记录时间戳；缺省取当前 UTC。

    Returns:
        Core 生成的 ``agent_id`` 与组装好的 ``AgentRecord``。

    Raises:
        CreationError: 模板缺失/归档、模板不足以创建、或 Core 创建失败。
    """
    record = await templates.get_version(request.template_id, request.revision)
    if record is None:
        raise CreationError("template_not_found", request.template_id)
    if record.archived:
        raise CreationError("template_archived", request.template_id)
    config = record.config()

    try:
        rendered = render_assembly(
            config,
            agent_name=request.name,
            workspaces=workspaces,
            granted=available,
            initial_bindings=request.initial_bindings,
        )
    except RenderError as error:
        raise CreationError(error.code, error.detail) from error

    # 创建前检查：没有已授予成员时 Core 的启动核定必然失败（builtin 模式不能只靠
    # 控制成员启动）。在这里 fail-closed，比把空规格送进 Core 再报错更可诊断。
    if not rendered.spec.members:
        raise CreationError(
            "no_granted_members",
            ",".join(rendered.report.missing_packages + rendered.report.not_granted),
        )

    try:
        view = await asyncio.wait_for(create(rendered.spec), timeout=CREATE_TIMEOUT_SECONDS)
    except TimeoutError as error:
        _dump_pending_tasks()
        raise CreationError(
            "agent_create_timeout",
            f"agent assembly did not finish in {CREATE_TIMEOUT_SECONDS:.0f}s",
        ) from error
    except Exception as error:  # noqa: BLE001 — 统一折成创建失败，保留原始文案
        raise CreationError("agent_create_failed", str(error)) from error

    stamp = now or datetime.now(UTC)
    record = _record_from_template(
        config,
        template_id=request.template_id,
        revision=request.revision,
        agent_id=view.agent_id,
        name=request.name,
        desired_state=request.desired_state,
        now=stamp,
    )
    return CreatedAgent(
        agent_id=view.agent_id,
        name=view.name or request.name,
        recovery_mode=view.recovery_mode,
        record=record,
    )
