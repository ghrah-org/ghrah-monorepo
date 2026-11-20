# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""观察端轮次卡与轮次详情的只读视图。"""

from pydantic import BaseModel, ConfigDict, Field

from ghrah.protocol.views.core import NodeView, TurnView

__all__ = ["TurnCardView", "TurnDetailView", "TurnStatsView"]


class TurnStatsView(BaseModel):
    """同一轮次中已持久化的节点用量与审批等待时间。"""

    model_config = ConfigDict(extra="forbid")

    steps: int = 0
    node_count: int = 0
    token_input: int = 0
    token_output: int = 0
    token_total: int = 0
    token_cache_read: int = 0
    token_cache_write: int = 0
    cache_hit_rate: float | None = None
    duration_ms: int | None = None
    waiting_ms: int = 0


class TurnCardView(TurnView):
    """含统计与导航锚点的轮次视图。"""

    injections: int = Field(default=0, exclude=True)
    turn_index: int
    opener_message_id: str | None = None
    opener_entry_id: str | None = None
    parent_turn_id: str | None = None
    stats: TurnStatsView


class TurnDetailView(BaseModel):
    """单轮卡片及其按时间与节点 ID 排序的节点。"""

    model_config = ConfigDict(extra="forbid")

    turn: TurnCardView
    nodes: list[NodeView]
