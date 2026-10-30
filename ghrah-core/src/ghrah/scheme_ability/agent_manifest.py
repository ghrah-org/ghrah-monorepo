# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""复用运行时唯一的 Agent 声明模型，消费 agentconf 0.3 模型端点。

能力定义和 Agent 配置分别演进；本模块只保留原导入入口，不复制 Agent schema。
"""

from ghrah.manifest.agent import (
    AbilityRef,
    AgentHooks,
    AgentManifest,
    AgentMetadata,
    ContextOverrides,
    ModelConfig,
    PersistenceOverrides,
    WindowOverrides,
)

__all__ = [
    "AbilityRef",
    "AgentHooks",
    "AgentManifest",
    "AgentMetadata",
    "ContextOverrides",
    "ModelConfig",
    "PersistenceOverrides",
    "WindowOverrides",
]
