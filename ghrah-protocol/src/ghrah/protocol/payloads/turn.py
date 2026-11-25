# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""轮次事件与命令载荷。"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TurnAbortPayload(BaseModel):
    """中止目标 Agent 的指定轮次或当前轮次。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    turn_id: str | None = Field(default=None, min_length=1)
    session_id: str | None = Field(default=None, min_length=1)
    reason: str = ""


class TurnRollbackPayload(BaseModel):
    """从终态轮次的起点前或指定轮内节点创建分支。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    target: Literal["base", "node"] = "base"
    node_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def check_target(self) -> Self:
        """仅在选择轮内节点时接受 node_id。"""
        if (self.target == "node") != (self.node_id is not None):
            raise ValueError("node_id is required exactly when target is node")
        return self


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
