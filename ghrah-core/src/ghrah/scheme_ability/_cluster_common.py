# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""集群通信 Ability 共享常量与 supervisor 可用性判定（两档可诊断错误）。"""

from __future__ import annotations

from typing import Any

_NO_SUPERVISOR_ERROR = (
    "No supervisor configured (standalone mode is a normal state). "
    "Cluster abilities require a SupervisorActor to be injected "
    "via AbilityExecutionContext.supervisor at spawn time."
)

_SUPERVISOR_NOT_CLUSTER_ERROR = (
    "Supervisor is wired but not cluster-capable (assembly defect): "
    "get_cluster_context is missing or returns an empty cluster_id. "
    "Check the supervisor wiring at assembly time."
)


def cluster_supervisor_error(supervisor: Any) -> str | None:
    """判定 supervisor 是否可支撑集群能力。

    两档可诊断错误（可区分"单体模式正常态"与"装配缺陷"）：
    1. ``supervisor is None`` → 未接线（standalone 正常态，
       提示在 spawn 时注入）；
    2. 已注入但 ``get_cluster_context`` 缺失 / 调用失败 /
       cluster_id 为空 → 装配缺陷。

    Args:
        supervisor: AbilityExecutionContext.supervisor（duck-typed）

    Returns:
        错误文案；supervisor 可用时返回 None
    """
    if supervisor is None:
        return _NO_SUPERVISOR_ERROR
    get_ctx = getattr(supervisor, "get_cluster_context", None)
    if not callable(get_ctx):
        return _SUPERVISOR_NOT_CLUSTER_ERROR
    try:
        cluster_id = (get_ctx() or {}).get("cluster_id") or ""
    except Exception:
        return _SUPERVISOR_NOT_CLUSTER_ERROR
    if not cluster_id:
        return _SUPERVISOR_NOT_CLUSTER_ERROR
    return None
