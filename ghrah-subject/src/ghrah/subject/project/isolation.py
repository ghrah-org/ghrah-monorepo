# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Project 隔离上下文与 workspace root 解析。

MVP 范围：定义 ``IsolationContext`` 数据类作 Foxtrail Stage 3b 注入点（仅记录，
不施加 policy——``PolicyConfig``/``ActionClass`` 尚未存在）；经
``ProviderRegistry`` + ``WorkspaceCaps.FILESYSTEM_BACKED`` 解析 ``file://``
locator 为本地路径构成 sandbox cwd 允许集合；前置 path_grants / locator
非重叠校验逻辑供未来 manager复用。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ghrah.subject.project.models import (
    AgentSpec,
    IsolationSpec,
    PathGrant,
    ProjectRecord,
    WorkspaceMount,
)
from ghrah.subject.workspace.locator import locator_to_path
from ghrah.subject.workspace.models import WorkspaceRecord
from ghrah.subject.workspace.providers.base import WorkspaceCaps
from ghrah.subject.workspace.registry import ProviderRegistry

__all__ = [
    "IsolationContext",
    "apply_isolation",
    "resolve_project_roots",
    "validate_path_grants_non_overlapping",
    "validate_workspace_locators_non_nested",
]


class IsolationContext(BaseModel):
    """Project 隔离快照（Foxtrail Stage 3b 注入点，MVP 仅记录）。

    TODO Foxtrail Stage 3b: 注入到能力调用对象的受信元数据（isolation）中,
    由 effect 系统按 ``policy_config`` / ``agent_path_grants`` 裁决写操作。

    Attributes:
        project_id: 所属 project 主键。
        workspace_roots: 已解析为本地路径的 FILESYSTEM_BACKED workspace 根目录
            列表（构成 sandbox cwd 允许集合）。
        agent_path_grants: agent_name → 其 PathGrant 列表（runtime 写操作前
            校验目标路径落在该 agent grants 或私有目录内）。
        isolation: 原始 IsolationSpec（effect_allowlist / hitl_override 等）。
    """

    model_config = ConfigDict(frozen=True)

    project_id: str
    workspace_roots: list[str]
    agent_path_grants: dict[str, list[PathGrant]]
    isolation: IsolationSpec


def resolve_project_roots(
    workspaces: list[WorkspaceMount],
    registry: ProviderRegistry,
    workspace_records: dict[str, WorkspaceRecord],
) -> list[str]:
    """解析 FILESYSTEM_BACKED workspace 的 ``file://`` locator 为本地路径。

    遍历 workspaces，经 ``workspace_records[workspace_id]`` 取 locator，经
    registry 取 provider，判定 ``WorkspaceCaps.FILESYSTEM_BACKED`` capability
    后用 ``locator_to_path`` 解析。非文件系统后端（未来 nfs:// 等）跳过，
    不参与 sandbox cwd 限制。结果去重。

    Args:
        workspaces: project 挂载列表。
        registry: Workspace provider 注册表。
        workspace_records: workspace_id → WorkspaceRecord（由调用方经
            ``WorkspaceManager.get_record`` 汇集）。

    Returns:
        本地路径列表（去重，保留挂载顺序）。
    """
    roots: list[str] = []
    seen: set[str] = set()
    for mount in workspaces:
        record = workspace_records.get(mount.workspace_id)
        if record is None:
            continue
        try:
            provider = registry.get(record.provider_type)
        except Exception:  # noqa: BLE001 — 未知 provider_type 跳过，不阻断
            continue
        if WorkspaceCaps.FILESYSTEM_BACKED not in provider.capabilities:
            continue
        try:
            path = locator_to_path(record.locator)
        except ValueError:
            continue
        if path and path not in seen:
            seen.add(path)
            roots.append(path)
    return roots


async def apply_isolation(
    project: ProjectRecord,
    registry: ProviderRegistry,
    workspace_records: dict[str, WorkspaceRecord],
) -> IsolationContext:
    """构造 project-scoped IsolationContext 快照。

    MVP 仅返回快照，不施加 policy（``PolicyConfig`` 不存在）。workspace_roots
    经 ``resolve_project_roots`` 解析。agent_path_grants 取自
    ``project.isolation.agent_path_grants``。

    TODO Foxtrail Stage 3b: 此处构造 ``PolicyConfig``（基于全局 + effect_allowlist
    收紧 + hitl_override，``exec_foreign_unwaivable`` 永远 True）并注入
    调用对象的受信元数据（isolation）中。
    """
    roots = resolve_project_roots(project.workspaces, registry, workspace_records)
    return IsolationContext(
        project_id=project.project_id,
        workspace_roots=roots,
        agent_path_grants=dict(project.isolation.agent_path_grants),
        isolation=project.isolation,
    )


def validate_path_grants_non_overlapping(
    agent: AgentSpec, workspaces: list[WorkspaceMount]
) -> None:
    """校验 agent.path_grants 的 workspace_id 均在 project 内且 subpath 不重叠。

    - grant.workspace_id 必须在 workspaces 内（否则 ValueError）。
    - subpath ``"."`` 与任何其他 subpath 冲突（整个根 vs 子路径）。
    - 两 subpath 一方为另一方前缀则冲突（规范化后比较，去尾斜杠）。

    Raises:
        ValueError: workspace_id 不在 project 内，或 subpath 重叠。
    """
    ws_ids = {m.workspace_id for m in workspaces}
    for grant in agent.path_grants:
        if grant.workspace_id not in ws_ids:
            raise ValueError(
                f"Agent {agent.name!r} path_grant references unknown "
                f"workspace_id {grant.workspace_id!r}"
            )

    # 按 workspace_id 分组后组内判重叠
    by_ws: dict[str, list[str]] = {}
    for grant in agent.path_grants:
        norm = _normalize_subpath(grant.subpath)
        by_ws.setdefault(grant.workspace_id, []).append(norm)

    for ws_id, subpaths in by_ws.items():
        # "." 与任意非 "." 冲突
        if "." in subpaths:
            others = [s for s in subpaths if s != "."]
            if others:
                raise ValueError(
                    f"Agent {agent.name!r} subpath '.' overlaps with {others} "
                    f"in workspace {ws_id!r}"
                )
            continue
        # 两两前缀判嵌套
        sorted_paths = sorted(subpaths)
        for i in range(len(sorted_paths)):
            for j in range(i + 1, len(sorted_paths)):
                a, b = sorted_paths[i], sorted_paths[j]
                if b == a or b.startswith(a + "/"):
                    raise ValueError(
                        f"Agent {agent.name!r} subpaths overlap in workspace "
                        f"{ws_id!r}: {a!r} vs {b!r}"
                    )


def validate_workspace_locators_non_nested(locators: list[str]) -> None:
    """校验 locator 路径互不嵌套。

    经 ``locator_to_path`` 解析为本地路径后，两两判定一方为另一方前缀
    （``os.path.commonpath`` 不可靠，用规范化 + 前缀比较）。非 file:// locator
    跳过（不参与文件系统嵌套校验）。

    Raises:
        ValueError: 任两 locator 路径嵌套。
    """
    pairs: list[tuple[str, str]] = []
    for loc in locators:
        try:
            p = locator_to_path(loc)
        except ValueError:
            continue
        pairs.append((_norm(p), loc))
    for i in range(len(pairs)):
        for j in range(i + 1, len(pairs)):
            a_norm, a_loc = pairs[i]
            b_norm, b_loc = pairs[j]
            if (
                a_norm == b_norm
                or b_norm.startswith(a_norm + "/")
                or a_norm.startswith(b_norm + "/")
            ):
                relation = "duplicate" if a_norm == b_norm else "nested"
                raise ValueError(
                    f"Workspace locators nest/duplicate ({relation}): "
                    f"{a_loc!r} vs {b_loc!r}. "
                    "Choose a different, non-overlapping workspace folder."
                )


def _normalize_subpath(subpath: str) -> str:
    """规范化 subpath：去首尾 ``/`` 与 ``.``，``"."`` 保留为根标记。"""
    s = subpath.strip("/")
    if s == "" or s == ".":
        return "."
    return s


def _norm(path: str) -> str:
    """规范化本地路径：统一分隔符并去尾斜杠（保留根）。"""
    normalized = path.replace("\\", "/")
    if len(normalized) > 1 and normalized.endswith("/"):
        return normalized.rstrip("/")
    return normalized
