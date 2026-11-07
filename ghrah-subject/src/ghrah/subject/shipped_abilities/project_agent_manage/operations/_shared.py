# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""project_agent_manage 共用：身份校验与宿主命令端口取用。"""

from __future__ import annotations

from typing import Any

from ghrah.ability.execution.execution_services import HOST_COMMAND_PORT, SUPERVISOR
from ghrah.types.results import ActionOutcome, ActionResult

IDENTITY_NOT_BOUND = "trusted project identity is not bound"
PORT_NOT_WIRED = (
    "host command port is not wired — project_agent_manage requires a "
    "Subject host (CoreUnit with ctx.serial) and a matching host_command_grants entry"
)


def project_id_or_failure(call: Any) -> str | ActionResult:
    """从受信身份取 project_id；缺席时返回 FAILURE。"""
    project_id = getattr(getattr(call, "identity", None), "project_id", "") or ""
    if not project_id:
        return ActionResult(
            outcome=ActionOutcome.FAILURE,
            data={"error": IDENTITY_NOT_BOUND},
        )
    return project_id


def port_or_failure(services: Any) -> Any | ActionResult:
    """取 HOST_COMMAND_PORT；未接线时返回 FAILURE（不抛异常）。"""
    port = services.get(HOST_COMMAND_PORT) if services is not None else None
    if port is None:
        return ActionResult(
            outcome=ActionOutcome.FAILURE,
            data={"error": PORT_NOT_WIRED},
        )
    return port


def default_cluster_id(services: Any) -> str:
    """从 SUPERVISOR 集群上下文取 cluster_id；缺席为空串。"""
    supervisor = services.get(SUPERVISOR) if services is not None else None
    if supervisor is None:
        return ""
    getter = getattr(supervisor, "get_cluster_context", None)
    if not callable(getter):
        return ""
    context = getter()
    if not isinstance(context, dict):
        return ""
    return str(context.get("cluster_id") or "")


def project_from_get_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    """从 project_get 回执抽出 project 字典（兼容嵌套与扁平形状）。"""
    data = receipt.get("data") or {}
    if not isinstance(data, dict):
        return {}
    project = data.get("project")
    if isinstance(project, dict):
        return project
    return data


def receipt_to_result(
    receipt: dict[str, Any], *, ok_data: dict[str, Any] | None = None
) -> ActionResult:
    """把 Subject 命令回执转成 ActionResult。"""
    if not isinstance(receipt, dict) or not receipt.get("success"):
        error = ""
        if isinstance(receipt, dict):
            error = str(receipt.get("error") or "")
        return ActionResult(
            outcome=ActionOutcome.FAILURE,
            data={"error": error or "project command failed"},
        )
    data = ok_data if ok_data is not None else (receipt.get("data") or {})
    return ActionResult(outcome=ActionOutcome.SUCCESS, data=data)
