# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""LoadSkillAbility：按 full_name 加载某一个 skill 的 SKILL.md 原文（skill-ability）。

技能目录摘要（每轮注入上下文）只承载「名字 + 一句话描述」，**正文不在注入物里**
——需要正文时用本能力按需拉取：入参就是给模型的 ``full_name``（与目录摘要里的
名字逐字一致），返回 SKILL.md 原文与它在磁盘上的路径。

定位口径完全走 ``SkillAbilityProvider``（同一份清单索引）：不自己拼路径、不自己
解析 frontmatter、不缓存——因此目录热插拔后立刻生效，且不会绕过扫描校验去读一个
"不在清单里"的文件。

接线语义（零隐式）：provider 经装配期注入
（``AbilityExecutionContext.skill_provider``）；未接线时明确 FAILURE 说明缺席，
**不**静默当成"技能不存在"（那是两件不同的事）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ghrah.mcp.errors import McpConfigError
from ghrah.scheme.execution.base import Ability
from ghrah.scheme.loading.skill_errors import SkillNotFoundError, SkillParseError
from ghrah.scheme.loading.skill_mcp import (
    load_mcp_specs,
    render_mcp_catalog,
    render_mcp_schema_block,
)
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.scheme.execution.context import AbilityExecutionContext
    from ghrah.scheme.execution.hooks import Hook


# ---------------------------------------------------------------------------
# scheme 运行时声明（随实现搬迁；定义文件 ability.yaml 不承载这些事实）
#   来源：ghrah/abilities/builtin/load_skill.py
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
    "Load the full SKILL.md document of one skill (skill-ability) by its exact full name, "
    "returning the document text and its path. Skills are instruction documents, not "
    "invocable abilities."
)
TAGS = [
    "core",
    "skill",
]
# --- catalogue 声明结束

__all__ = ["LoadSkillAbility", "LoadSkillInput"]

logger = logging.getLogger(__name__)


class LoadSkillInput(BaseModel):
    """load_skill 工具入参契约（extra=forbid）。"""

    model_config = {"extra": "forbid"}

    full_name: str = Field(
        description=(
            "The skill's full name exactly as shown in the available-skills "
            "catalog (e.g. 'research'). Use list_abilities to see the names."
        ),
    )
    server: str = Field(
        default="",
        description=(
            "Optional. Name of an MCP server declared by this skill. When set, the "
            "returned data also includes that server's tool input schemas."
        ),
    )
    tool: str = Field(
        default="",
        description=("Optional. Narrow the schema lookup to a single tool. Requires 'server'."),
    )


class LoadSkillAbility(Ability):
    """加载单个 skill 的正文原文（只读）。

    输出经 ``data`` 返回：

    - SUCCESS：``{"full_name", "body_path", "skill_dir", "path", "content", "note"}``
      ——``content`` 是正文完整原文，逐字返回；``body_path`` 是定义面声明的相对
      路径（本版 ``skills/SKILL.md``），``path`` 是它在磁盘上的绝对路径；
    - FAILURE：``{"error"}``——入参缺失 / provider 未接线 / 技能不存在 /
      文档读不出来，四种情形文案各自区分。
    """

    @property
    def name(self) -> str:
        return "load_skill"

    def bind_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "load_skill",
                "description": (
                    "Load the full SKILL.md document of one skill from the "
                    "available-skills catalog, by its exact full name. Returns "
                    "the document text and its path. If the skill declares MCP "
                    "servers, the result also lists its available MCP tools. Read "
                    "one when a task matches its description."
                ),
                "parameters": LoadSkillInput.model_json_schema(),
            },
        }

    def to_prompt_description(self) -> str:
        return (
            "load_skill(full_name: str, server?: str, tool?: str) -> dict: load one "
            "skill's SKILL.md content, its MCP tool catalog, and (when asked) a "
            "tool's input schema"
        )

    def get_hooks(self) -> list[Hook]:
        return []

    async def execute(self, context: AbilityExecutionContext) -> ActionResult:
        tool_args = context.tool_args or {}
        full_name = str(tool_args.get("full_name", "") or "").strip()
        server = str(tool_args.get("server", "") or "").strip()
        tool = str(tool_args.get("tool", "") or "").strip()
        if not full_name:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": "full_name is required"},
            )
        if tool and not server:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": "'tool' requires 'server'"},
            )

        provider = context.skill_provider
        if provider is None:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={
                    "error": (
                        "SkillAbilityProvider not wired — skill loading is "
                        "unavailable in this deployment (assembly-time "
                        "injection, not implicit)"
                    )
                },
            )

        try:
            document = provider.read_skill(full_name)
        except SkillNotFoundError:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={
                    "error": f"Skill not found: '{full_name}'",
                    "note": ("Use list_abilities to see the available skill full names."),
                },
            )
        except SkillParseError as exc:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Failed to read skill document: {exc}"},
            )
        except Exception as exc:
            logger.warning("skill document read failed for %r", full_name, exc_info=True)
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Failed to read skill document: {exc}"},
            )

        payload: dict[str, Any] = {
            "full_name": document.full_name,
            "body_path": document.body_path,
            "skill_dir": str(document.skill_dir) if document.skill_dir is not None else "",
            "path": str(document.path) if document.path is not None else "",
            "content": document.content,
            "note": "skill-ability document — instructions to apply",
        }

        if document.skill_dir is None:
            # 容器在包资源里（不在文件系统上）：没有可寻址的目录，MCP 声明也无处读取。
            if server:
                return ActionResult(
                    outcome=ActionOutcome.FAILURE,
                    data={
                        "error": (
                            f"Skill '{full_name}' is not on the filesystem, "
                            f"so MCP server '{server}' cannot be resolved"
                        )
                    },
                )
            return ActionResult(outcome=ActionOutcome.SUCCESS, data=payload)

        try:
            specs = load_mcp_specs(document.skill_dir, skill_full_name=document.full_name)
        except McpConfigError as exc:
            # 配置非法就明确失败：半加载的声明会让模型看到不完整的能力清单。
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Invalid MCP config in skill '{full_name}': {exc}"},
            )

        if specs:
            runtime = context.mcp_runtime
            if runtime is None:
                return ActionResult(
                    outcome=ActionOutcome.FAILURE,
                    data={
                        "error": (
                            "McpRuntime not wired — this skill declares MCP servers "
                            "but none of them can be activated in this deployment "
                            "(assembly-time injection, not implicit)"
                        )
                    },
                )
            try:
                for spec in specs:
                    await runtime.activate(spec, agent_scope=context.agent_name)
            except McpConfigError as exc:
                return ActionResult(
                    outcome=ActionOutcome.FAILURE,
                    data={"error": f"Failed to activate MCP servers for '{full_name}': {exc}"},
                )
            payload["mcp_catalog"] = render_mcp_catalog(specs)
            if server:
                try:
                    payload["mcp_schema"] = render_mcp_schema_block(
                        specs, server=server, tool=tool or None
                    )
                except McpConfigError as exc:
                    return ActionResult(
                        outcome=ActionOutcome.FAILURE,
                        data={"error": str(exc)},
                    )
        elif server:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={
                    "error": (
                        f"Skill '{full_name}' declares no MCP servers, "
                        f"so '{server}' cannot be resolved"
                    )
                },
            )

        return ActionResult(outcome=ActionOutcome.SUCCESS, data=payload)
