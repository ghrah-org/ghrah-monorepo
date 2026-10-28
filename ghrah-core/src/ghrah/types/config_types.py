# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""配置纯数据类型 — 零内部依赖。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_WINDOW_MAX_TOKENS = 1_000_000


DEFAULT_COMPACT_METHOD = "ghrah.builtin"
DEFAULT_COMPACT_KEEP_RECENT = 2


@dataclass
class WindowConfig:
    """窗口管理策略配置。

    控制如何将对话历史压缩到 LLM 的 token 预算内。

    Attributes:
        max_tokens: LLM 上下文窗口大小（token 预算）；None 表示未声明，
            未声明时优先使用 agentconf 解析后的 max_context_length；
            未携带能力信息的客户端才按模型名查内置窗口表
            （``ghrah.context.model_windows``），查表未命中回落
            DEFAULT_WINDOW_MAX_TOKENS（1M，即 1000000 tokens，接入默认预算）。
            显式声明是运营者声明合约：引擎永不推断目标模型的真实窗口，
            须按目标模型真实窗口 − 输出余量设定；厂商上下文超限错误是
            唯一运行时真相信号（触发预算回填与紧急压缩阶梯）
        strategies: 策略名称列表，按执行顺序排列
            可选值: "tool_call_fold", "sliding_window", "truncation", "llm_summary"
        tool_call_max_length: ToolCallFoldStrategy 的最大 content 长度
        sliding_window_size: SlidingWindowStrategy 的窗口大小
        compact_threshold: 链上 compact 触发阈值（0~1 相对预算比率）；
            None 表示不启用链上压缩，仅走现状窗口管道
        compact_keep_recent: compact 保留窗节点数（按节点边界），最小为 2
        compact_method: compact 综合方法名，当前唯一合法值为 "ghrah.builtin"
    """

    max_tokens: int | None = None
    strategies: list[str] = field(default_factory=lambda: ["tool_call_fold", "truncation"])
    tool_call_max_length: int = 500
    sliding_window_size: int = 20
    compact_threshold: float | None = None
    compact_keep_recent: int = DEFAULT_COMPACT_KEEP_RECENT
    compact_method: str = DEFAULT_COMPACT_METHOD

    def __post_init__(self) -> None:
        if self.compact_threshold is not None and not 0 < self.compact_threshold <= 1:
            raise ValueError(
                f"compact_threshold must be in (0, 1] when set, got {self.compact_threshold}"
            )
        if self.compact_keep_recent < 2:
            raise ValueError(
                f"compact_keep_recent must be >= 2 (node-pair integrity), "
                f"got {self.compact_keep_recent}"
            )
        if self.compact_method != DEFAULT_COMPACT_METHOD:
            raise ValueError(
                f"compact_method must be {DEFAULT_COMPACT_METHOD!r}, got {self.compact_method!r}"
            )


@dataclass
class ModelOverrides:
    """Manifest 模型配置覆盖值。

    这些值来自 AgentManifest.model，优先级高于 agentconf 解析结果。
    在 _ensure_llm() 中创建 ChatFormat 后覆盖对应属性。
    """

    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    top_k: int | None = None


@dataclass
class ContextConfig:
    """ContextManager 配置。

    控制 ActorAgent 的上下文管理行为，包括持久化后端的选择。

    持久化后端通过 persistence_type 选择：
    - "json_file": JsonFileBackend（基于 JSON 文件，支持 gzip 压缩）
    - "memory": InMemoryBackend（纯内存，不持久化到磁盘）
    - "sqlite": SqliteBackend（基于 SQLite 数据库，WAL 模式支持并发读）
    - None: 不启用持久化

    Attributes:
        snapshot_interval: 消息快照间隔（每 N 次迭代存储一次完整快照），默认 5
        auto_persist: 是否在每次 commit/rollback 后自动持久化节点，默认 False
        persistence_type: 持久化后端类型，None 表示不启用持久化
        persistence_root_dir: 持久化存储根目录路径（json_file/sqlite 后端使用）
        persistence_compress: 是否启用 gzip 压缩持久化文件（json_file 后端），默认 True
        persistence_run_id: 持久化运行 ID，None 表示自动生成（格式：run_{ISO8601}）
    """

    snapshot_interval: int = 5
    auto_persist: bool = False
    persistence_type: str | None = None
    persistence_root_dir: str | None = None
    persistence_compress: bool = True
    persistence_run_id: str | None = None


@dataclass
class AgentConfig:
    """Agent 框架层配置。

    LLM 配置（provider、model、api_key 等）通过 agentconf SDK 管理，
    ActorAgent 初始化时会调用 `AgentsConfig().resolve_agent(effective_agent_config_name)`
    获取完整 LLM 配置。

    当多个运行时 Agent 共享同一份 LLM 配置时（如 worker 池场景），
    可通过 agent_config_name 指定 agentconf 中的配置名称，
    而 name 仅作为运行时唯一标识。

    Agent 的行为完全由注册的 Ability 组合决定（组合优于继承），
    不再通过 agent_type 隐式关联默认 Ability。

    Attributes:
        agent_id: durable Agent 的稳定标识；空值时兼容回退到 name。
        name: CoreUnit 内运行时唯一显示名（用于 Actor 注册、消息路由）
        agent_config_name: agentconf 中的配置名称，None 时回退到 name（向后兼容）
        description: Agent 能力描述（用于 Agent 发现和路由）
        system_prompt: 系统提示词
        max_iterations: 最大推理迭代次数（生命周期语义，防止死循环的安全阀）
        communication_timeout: Agent 间通信超时时间（秒），-1 表示无限等待
        resources: 资源配置，如 {"CPU": 2, "GPU": 1}
        window: 窗口管理配置，None 表示不启用窗口管理
        context: ContextManager 配置，None 表示使用默认值
        model_overrides: Manifest 模型配置覆盖值
        workspace_root: 工作区根目录，用于将相对路径解析到沙盒内（None 表示不限制）
        cluster_context_injection: 集群身份注入开关——True 时 AgentBuilder
            在 system_prompt 头部拼接 [Cluster Context] 段（cluster_id +
            成员清单）。默认 False（零隐式：prompt 内容不因挂进集群而变化）
        environment_context_injection: 环境信息注入开关——True 且
            environment_info 提供时，AgentBuilder 在 system_prompt 头部拼接
            [Environment] 段（授权根/工作区/部署形态/HITL 参数）。默认 False
        environment_info: 环境快照（CoreUnit spawn 流程组装：deployment/
            workspace_root/allowed_roots/hitl_timeout 等）；仅注入用，
            不参与任何运行时判定
    """

    name: str
    agent_id: str = ""
    agent_config_name: str | None = None
    description: str = ""
    system_prompt: str = ""
    max_iterations: int = 10
    communication_timeout: float = 300.0
    resources: dict[str, Any] = field(default_factory=dict)
    window: WindowConfig | None = None
    context: ContextConfig | None = None
    model_overrides: ModelOverrides | None = None
    workspace_root: str | None = None
    cluster_context_injection: bool = False
    environment_context_injection: bool = False
    environment_info: dict[str, Any] | None = None

    @property
    def effective_agent_config_name(self) -> str:
        """获取有效的 agentconf 查找名称。

        优先使用 agent_config_name，未指定时回退到 name（向后兼容）。
        """
        return self.agent_config_name or self.name

    @property
    def effective_agent_id(self) -> str:
        """稳定 Agent ID；旧调用方未提供时兼容回退到 name。"""
        return self.agent_id or self.name
