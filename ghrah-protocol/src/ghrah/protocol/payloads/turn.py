# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""执行轮次事件的传输载荷。"""

from pydantic import BaseModel


class _TurnPayload(BaseModel):
    project_id: str = ""
    agent_id: str = ""
    agent_name: str
    turn_id: str
    session_id: str


class TurnOpenedPayload(_TurnPayload):
    """通知轮次已开启。"""

    status: str = "open"
    opener_delivery_id: str | None = None


class TurnWaitingHitlPayload(_TurnPayload):
    """通知轮次进入人工审批等待。"""

    promise_id: str


class TurnClosedPayload(_TurnPayload):
    """通知读侧刷新轮次的终态与锚点。"""

    status: str
    closing_kind: str
    end_node_id: str | None = None
