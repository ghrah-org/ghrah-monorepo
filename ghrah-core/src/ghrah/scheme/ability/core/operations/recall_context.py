# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""RecallContextAbility：按动作链历史精确回忆消息（compact 配套召回面）。

链上 compact 把早期节点压缩为摘要后，原始消息仍完整保留在早期节点的
messages_delta / root 的 messages_snapshot 中——本能力按查询边界
（当前分支，经 fork 点含祖先链）无损召回：

- ``list`` 模式：列界内节点概要（node_id/iteration/node_kind/branch_id，
  compact 节点附 summarized_range/trigger_source，供定位原始节点）；
- ``messages`` 模式：按节点拉取消息原文（root 取 snapshot、普通节点取
  delta、compact 节点取压缩视图 snapshot 本身）。

查询边界 = 压缩边界：兄弟分支与跨 session 不纳入，界外 node_id 返回
明确边界错误（区分"存在但界外"与"不存在"）。

context_manager 经 AbilityExecutionContext.services 显式接线——
未接线 → FAILURE 指路（零隐式）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ghrah.scheme.execution.base import Ability
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.scheme.execution.context import AbilityExecutionContext
    from ghrah.scheme.execution.hooks import Hook


# ---------------------------------------------------------------------------
# scheme 运行时声明（随实现搬迁；定义文件 ability.yaml 不承载这些事实）
#   来源：ghrah/abilities/builtin/recall_context.py
#   PERMISSIONS <- 旧 manifest 的 metadata.permissions
#   HOOKS       <- 旧 manifest 的 hooks
# 切换时由 scheme 加载器合并回旧 manifest 形态；在此之前这两项不是权威。
# ---------------------------------------------------------------------------
PERMISSIONS = {}
HOOKS = {}

# --- catalogue 声明（旧 manifest 的元信息，草案格式里暂无字段承载）
#   SUMMARY <- metadata.description（目录摘要）
#   TAGS    <- metadata.tags（发现/过滤用）
# 草案补上对应字段后，这两项应迁回 ability.yaml。
SUMMARY = (
    "Recall original messages from the current-branch action-chain history, including "
    "nodes summarized away by compaction."
)
TAGS = [
    "core",
    "context",
]
# --- catalogue 声明结束

# 忽略 root（initial_messages 装配产物）之外节点数的 list 上限保护
_MAX_LIST_NODES = 500


class RecallContextInput(BaseModel):
    """recall_context 工具入参 schema。"""

    model_config = {"extra": "forbid"}

    mode: Literal["list", "messages"] = Field(
        default="list",
        description=(
            "list: summarize nodes on the current branch; "
            "messages: fetch original messages of the selected nodes"
        ),
    )
    node_id: str | None = Field(
        default=None,
        description="Target a single node on the current branch (exclusive with node_id_range)",
    )
    node_id_range: list[str] | None = Field(
        default=None,
        description="Inclusive [from_id, to_id] range of node ids on the current branch",
    )
    keyword: str | None = Field(
        default=None,
        description="Case-insensitive substring filter over message text",
    )
    role: Literal["system", "user", "ai", "tool"] | None = Field(
        default=None,
        description="Filter messages by role",
    )
    since_iteration: int | None = Field(
        default=None,
        ge=0,
        description="Only nodes with iteration >= this value",
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=_MAX_LIST_NODES,
        description="Max number of nodes returned (most recent first)",
    )
    max_output_tokens: int = Field(
        default=2000,
        ge=100,
        description="Output budget; recall stops and sets truncated=True when exceeded",
    )

    @field_validator("node_id_range")
    @classmethod
    def _range_must_be_pair(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and len(v) != 2:
            raise ValueError("node_id_range must contain exactly 2 node ids [from_id, to_id]")
        return v

    @model_validator(mode="after")
    def _node_selectors_exclusive(self) -> RecallContextInput:
        if self.node_id is not None and self.node_id_range is not None:
            raise ValueError("node_id and node_id_range are mutually exclusive")
        return self


def _node_kind(node: Any) -> str:
    """节点分类：root（链头）/ compact（压缩快照）/ plain（普通迭代）。"""
    if node.parent_id is None:
        return "root"
    if node.metadata.get("node_kind") == "compact":
        return "compact"
    return "plain"


def _node_messages(node: Any) -> list[Any]:
    """节点消息取面：root/compact 取 snapshot，普通节点取 delta。"""
    if _node_kind(node) in ("root", "compact"):
        return list(node.messages_snapshot or [])
    return list(node.messages_delta or [])


def _message_text(msg: Any) -> str:
    """消息文本视图（tool_calls 以名称+参数 JSON 串入文，供 keyword 命中）。"""
    import json

    text = getattr(msg, "text", "") or ""
    if getattr(msg, "has_tool_calls", False):
        calls = getattr(msg, "tool_calls", []) or []
        if calls:
            rendered = [
                {"name": getattr(c, "name", ""), "arguments": getattr(c, "arguments", {})}
                for c in calls
            ]
            text = f"{text} {json.dumps(rendered, ensure_ascii=False, default=str)}".strip()
    return text


def _message_matches(msg: Any, keyword: str | None, role: str | None) -> bool:
    """消息级过滤：keyword 大小写不敏感子串 + role 精确。"""
    if role is not None and getattr(msg, "role", None) != role:
        return False
    if keyword is not None:
        haystack = _message_text(msg).lower()
        needle = keyword.lower()
        if not needle or needle not in haystack:
            return False
    return True


class RecallContextAbility(Ability):
    """按动作链历史精确回忆（list 概要 / messages 原文，含过滤与输出上限）。

    输出经 ``data`` 返回：
    - list：``{"nodes": [{node_id, iteration, timestamp, ability_names,
      node_kind, branch_id, message_count, …}], "count", "truncated"}``
      （keyword/role 过滤时仅保留有命中消息的节点；compact 节点附
      summarized_range/trigger_source）；
    - messages：``{"nodes": [{node_id, iteration, node_kind, branch_id,
      "messages": [{role, source, text}]}], "count", "truncated"}``。
    """

    @property
    def name(self) -> str:
        return "recall_context"

    def bind_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "recall_context",
                "description": (
                    "Recall original context from the action-chain history "
                    "of the current branch (fork ancestors included). Use it "
                    "after compaction to recover details that were summarized "
                    "away: first list nodes (compact nodes carry "
                    "summarized_range to locate the original range), then "
                    "fetch messages by node_id or node_id_range."
                ),
                "parameters": RecallContextInput.model_json_schema(),
            },
        }

    def to_prompt_description(self) -> str:
        return (
            "recall_context(mode='list'|'messages', node_id=None, "
            "node_id_range=None, keyword=None, role=None, since_iteration=None, "
            "limit=20, max_output_tokens=2000) -> dict: Recall original "
            "messages from current-branch chain history (compaction companion)"
        )

    def get_hooks(self) -> list[Hook]:
        return []

    async def execute(self, context: AbilityExecutionContext) -> ActionResult:
        tool_args = context.tool_args or context.accumulated_data.get("tool_args", {})
        try:
            args = RecallContextInput.model_validate(
                {k: v for k, v in tool_args.items() if k in RecallContextInput.model_fields}
            )
        except Exception as e:
            return ActionResult(
                outcome=ActionOutcome.FAILURE, data={"error": f"invalid recall_context args: {e}"}
            )

        cm = context.context_manager
        if cm is None or not hasattr(cm, "get_history"):
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={
                    "error": (
                        "context_manager is not wired into this deployment — "
                        "recall_context requires the agent's ContextManager "
                        "(assemble-time injection, not implicit)"
                    )
                },
            )

        try:
            history: list[Any] = cm.get_history()
        except Exception as e:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Failed to read action-chain history: {e}"},
            )

        # ---- 选择目标节点（node_id / node_id_range / 全量过滤） ----
        try:
            selected = self._select_nodes(history, cm, args)
        except _RecallError as e:
            return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": str(e)})

        # since_iteration 过滤
        if args.since_iteration is not None:
            selected = [n for n in selected if n.iteration >= args.since_iteration]

        # limit：取最近 N 节点（history 根在前，尾部即最近）
        truncated_by_limit = len(selected) > args.limit
        if truncated_by_limit:
            selected = selected[-args.limit :]

        if args.mode == "list":
            data = self._build_list(selected, args)
        else:
            data = self._build_messages(selected, args, cm)
        data["truncated"] = data.get("truncated", False) or truncated_by_limit
        return ActionResult(outcome=ActionOutcome.SUCCESS, data=data)

    # ----------------------------------------------------------------
    # 内部实现
    # ----------------------------------------------------------------

    def _select_nodes(self, history: list[Any], cm: Any, args: RecallContextInput) -> list[Any]:
        """节点选择：单点/范围切片（含端点、边界校验）或全量。"""
        by_id = {n.id: n for n in history}

        if args.node_id is not None:
            node = by_id.get(args.node_id)
            if node is not None:
                return [node]
            self._raise_node_lookup(cm, args.node_id)

        if args.node_id_range is not None:
            from_id, to_id = args.node_id_range
            for nid in args.node_id_range:
                if nid not in by_id:
                    self._raise_node_lookup(cm, nid)
            indices = [history.index(by_id[nid]) for nid in (from_id, to_id)]
            lo, hi = min(indices), max(indices)
            return history[lo : hi + 1]

        return list(history)

    def _raise_node_lookup(self, cm: Any, node_id: str) -> None:
        """界外 node_id 的明确错误：区分兄弟分支（存在但界外）与不存在。"""
        node = None
        try:
            node = cm.get_chain_node(node_id)
        except Exception:
            node = None
        if node is not None:
            raise _RecallError(
                f"node '{node_id}' exists in this session but is outside the "
                f"current branch (sibling branch or non-ancestor); recall "
                f"boundary = current branch incl. fork ancestors"
            )
        raise _RecallError(f"node '{node_id}' not found in this session")

    def _build_list(self, nodes: list[Any], args: RecallContextInput) -> dict[str, Any]:
        """list 模式：节点概要；keyword/role 过滤时仅保留命中节点。"""
        entries: list[dict[str, Any]] = []
        filtered = args.keyword is not None or args.role is not None
        for node in nodes:
            messages = _node_messages(node)
            if filtered:
                hits = [m for m in messages if _message_matches(m, args.keyword, args.role)]
                if not hits:
                    continue
            entry: dict[str, Any] = {
                "node_id": node.id,
                "iteration": node.iteration,
                "timestamp": node.timestamp.isoformat() if node.timestamp else None,
                "ability_names": list(node.ability_names),
                "node_kind": _node_kind(node),
                "branch_id": node.created_on_branch_id,
                "message_count": len(messages),
            }
            if entry["node_kind"] == "compact":
                meta = node.metadata or {}
                for key in ("summarized_range", "trigger_source", "degraded"):
                    if key in meta:
                        entry[key] = meta[key]
            entries.append(entry)
        return {"nodes": entries, "count": len(entries)}

    def _build_messages(
        self, nodes: list[Any], args: RecallContextInput, cm: Any
    ) -> dict[str, Any]:
        """messages 模式：按节点分组的消息原文，受 max_output_tokens 约束。

        token 扣减走 context_manager 的统一估算器口径（token_estimator
        出口：WindowManager 侧实例优先，未配置 window 时回落默认实例）。
        """
        from ghrah.context.token_estimator import DEFAULT_TOKEN_ESTIMATOR

        estimator = getattr(cm, "token_estimator", None) or DEFAULT_TOKEN_ESTIMATOR
        entries: list[dict[str, Any]] = []
        budget = args.max_output_tokens
        truncated = False
        for node in nodes:
            messages = [
                m for m in _node_messages(node) if _message_matches(m, args.keyword, args.role)
            ]
            if not messages:
                continue
            rendered: list[dict[str, Any]] = []
            node_tokens = 0
            exhausted = False
            for msg in messages:
                item = {
                    "role": getattr(msg, "role", None),
                    "source": getattr(msg, "source", None),
                    "text": _message_text(msg),
                }
                cost = max(1, estimator.estimate_message(msg))
                if node_tokens + cost > budget:
                    truncated = True
                    exhausted = True
                    break
                node_tokens += cost
                budget -= cost
                rendered.append(item)
            entry = {
                "node_id": node.id,
                "iteration": node.iteration,
                "node_kind": _node_kind(node),
                "branch_id": node.created_on_branch_id,
                "messages": rendered,
            }
            entries.append(entry)
            if exhausted or budget <= 0:
                break
        return {"nodes": entries, "count": len(entries), "truncated": truncated}


class _RecallError(Exception):
    """recall 查询的领域错误（边界/not-found），经 FAILURE 回执上抛。"""
