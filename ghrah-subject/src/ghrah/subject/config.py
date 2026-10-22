"""Subject 配置定义。

管理 Subject 的运行时配置，包括工作区路径、数据库路径、权限策略和 Core 连接。

配置拆分：把上帝配置对象拆为各 Unit 独立配置切片，`from_env()` 向后兼容。
旧 flat 字段（workspace_root/db_path/manifest_root/hitl_policy/core/log_level）保留为兼容真相，
新 slice（persistence/sandbox/manifest/hitl）由 flat 字段派生或可显式传入。
"""

from __future__ import annotations

import logging
import os
from dataclasses import InitVar, dataclass, field
from pathlib import Path

from ghrah.protocol.types import RecoveryAction

logger = logging.getLogger(__name__)


def _split_tokens(value: str) -> list[str]:
    """按逗号/分号/空白切分并去空（safe 命令扩展白名单解析）。"""
    tokens = value.replace(";", ",").replace("\t", ",").replace(" ", ",").split(",")
    return [tok for tok in tokens if tok]


def _parse_sub_commands(value: str) -> dict[str, list[str]]:
    """解析 ``base:sub1,sub2;base2:sub3`` 形状的子命令扩展白名单。

    冒号前为基础命令，逗号分隔子命令；分号（或空白）分隔多组
    （组内逗号是子命令分隔符，不在组间切割）。
    """
    result: dict[str, list[str]] = {}
    for group in [g for g in value.replace("\t", ";").replace(" ", ";").split(";") if g]:
        if ":" not in group:
            continue
        base, _, subs = group.partition(":")
        base = base.strip().lower()
        if base and subs:
            result.setdefault(base, []).extend(
                s.strip().lower() for s in subs.split(",") if s.strip()
            )
    return result


def _safe_float(value: str, default: float, name: str) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        logger.warning("Invalid float for %s=%r, using default %s", name, value, default)
        return default


def _safe_int(value: str | None, default: int | None, name: str) -> int | None:
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        logger.warning("Invalid int for %s=%r, using default %s", name, value, default)
        return default


@dataclass
class HITLPolicyConfig:
    """HITL 权限策略配置。

    运行时覆盖层，用于收紧 manifest 声明的权限：
    - auto_approve_abilities：管理员强制放行的能力（覆盖 manifest 的 require_hitl=True）
    - require_approval_by_default：对未在 manifest 中注册的能力的兜底策略
    - allowed_paths：允许访问的路径白名单（用于文件系统权限检查）
    - workspace_root：工作区根路径，此路径下的操作自动放行
    - safe_extra_commands：部署方追加的基础命令 safe 白名单（如 pnpm、make）
    - safe_extra_sub_commands：部署方追加的子命令 safe 白名单
      （{"pnpm": ("test", "type-check")} 形状；只增不减，保持 fail-closed）
    - hitl_consecutive_timeout_limit：连续超时熔断阈值（连续 N 次审批超时后
      后续等待降级为 hitl_degraded_timeout；0 = 禁用，默认零隐式）
    - hitl_degraded_timeout：熔断降级后的等待秒数
    - environment_injection：环境信息注入开关——True 时 CoreUnit spawn
      流程组装环境快照注入 [Environment] prompt 段。默认 False（零隐式）

    注意：能力的 require_hitl/fs_write/fs_read_only/shell_access 标记
    从 manifest PermissionFlags 中获取，不再在此配置中指定。
    """

    auto_approve_abilities: list[str] = field(default_factory=list)
    require_approval_by_default: bool = True
    allowed_paths: list[str] = field(default_factory=list)
    workspace_root: str | None = None
    safe_extra_commands: list[str] = field(default_factory=list)
    safe_extra_sub_commands: dict[str, list[str]] = field(default_factory=dict)
    hitl_consecutive_timeout_limit: int = 0
    hitl_degraded_timeout: float = 30.0
    environment_injection: bool = False


@dataclass
class CoreTransportConfig:
    """Core 传输层配置（原 CoreConnectionConfig，语义更准：传输层）。

    Subject 通过 WebSocket 连接 Core，接收命令并推送事件。

    Attributes:
        url: Core WebSocket URL，如 "ws://localhost:4111/ws"
        reconnect_interval: 重连间隔（秒）
        max_reconnect_attempts: 最大重连尝试次数，None 表示无限重试
        ping_interval: 心跳间隔（秒）
        command_timeout: 命令执行超时时间（秒）
        cluster_id: Subject 持有的稳定 cluster 标识（MVP 单元素，默认 "default"），
            连接/重连成功后自动发 init_cluster(cluster_id)；不复用 transport 随机 client_id
    """

    url: str = "ws://localhost:4111/ws"
    reconnect_interval: float = 5.0
    max_reconnect_attempts: int | None = None
    ping_interval: float = 30.0
    command_timeout: float = 300.0
    cluster_id: str = "default"


@dataclass
class PersistenceConfig:
    """持久化 Unit 配置切片。

    Attributes:
        db_path: SQLite 数据库文件路径（持久化 ActionChain 和上下文）
    """

    db_path: str = os.path.expanduser("~/.ghrah/subject.db")


@dataclass
class SandboxUnitConfig:
    """sandbox Unit 配置切片。

    注意：区别于 ``SandboxExecutorConfig``（executor 内部：max_output_bytes/blocked_commands
    /env_overrides）。本切片是 sandbox Unit 的装配层配置（workspace_root + default_timeout）。

    Attributes:
        workspace_root: 工作区根路径（Subject 持有的文件系统根目录）
        default_timeout: 默认命令超时（秒），现从 core.command_timeout 拆出独立字段
    """

    workspace_root: str = os.path.expanduser("~/ghrah-workspace")
    default_timeout: float = 300.0


@dataclass
class ManifestConfig:
    """manifest Unit 配置切片。

    Attributes:
        manifest_root: manifest 存储根路径
    """

    manifest_root: str = os.path.expanduser("~/.ghrah/manifests")


@dataclass
class SkillConfig:
    """skill Unit 配置切片。

    装配期由 ``SkillUnit`` 据此根目录做一次初始化：目录自举 + 扫描 + 缺失
    ``ability.lock.yaml`` 补齐。扫描结果的消费方在 Core 侧，按需自行扫盘。

    Attributes:
        root: skill 存储根路径（每个 skill 一个子目录，内含 SKILL.md）
    """

    root: str = os.path.expanduser("~/.ghrah/skill-ability")


# transport kind 配置（当前仅实现 websocket，预留 ipc/grpc）
@dataclass
class TransportKindConfig:
    """transport kind 配置（当前仅实现 websocket，预留 ipc/grpc/http）。

    Attributes:
        core: core 传输层类型，websocket | ipc | grpc
        observer: observer 传输层类型，websocket | ipc | http | grpc
    """

    core: str = "websocket"
    observer: str = "websocket"


@dataclass
class ProjectConfig:
    """project Unit 配置切片。

    Attributes:
        bootstrap_workspace_locator: default project 首启 bootstrap 用的工作区
            locator（git 类型，``file://`` 解析）。空串表示派生自
            ``sandbox.workspace_root + "/projects/default"``（由
            ``SubjectConfig.__post_init__`` 填充）。
        default_root_locator_template: 新 Project Root 模板，``{project_id}``
            占位符由 ProjectManager 替换。
    """

    bootstrap_workspace_locator: str = ""
    default_root_locator_template: str = "~/.ghrah/projects/{project_id}"


@dataclass
class RecoveryConfig:
    """recovery Unit 配置切片。

    Attributes:
        enabled: 是否在 engine.start() 末尾触发 reconcile
        subject_id: 对账归属的 subject 标识（默认 "default"）
        on_unknown_workspace: reconcile 遇到孤立 workspace 时的默认策略
        reconcile_on_start: 启动时是否自动对账（enabled 之外的独立开关）
        bootstrap_default_project: 首启自动建 default project
    """

    enabled: bool = True
    subject_id: str = "default"
    on_unknown_workspace: RecoveryAction = RecoveryAction.PAUSE
    reconcile_on_start: bool = True
    bootstrap_default_project: bool = True


@dataclass
class RoomFilterConfig:
    """Room Filter Unit 配置切片（能力结果 → RoomLog 显式 Filter）。

    白名单配置驱动（零隐式行为——非白名单/无 room 上下文的
    能力结果一律不落 Room；manifest ``chat_visible`` 元数据扩展留待后续）。

    默认白名单 = ``conversation`` + ``end_task``：两者都是"对用户可见的
    回合结果"（会话回复 / 任务终止时的最终答复），Room 页需要全部渲染。

    Attributes:
        enabled: 是否挂载 Filter Unit（False = 完全无过滤行为）
        abilities: 白名单能力名（命中且 success 且带 room 上下文才落账）
    """

    enabled: bool = True
    abilities: tuple[str, ...] = ("conversation", "end_task")


@dataclass
class PluginTrustConfig:
    """插件信任边界切片（双层权威的 Subject 层）。

    信任闸：entry_points 可被加载的 plugin_id 白名单（发现 ≠ 启用 ≠ 信任）。
    供 ``subject plugin verify`` 默认模式与装配链挂载期强制执行。

    Attributes:
        discoverable: 信任清单（plugin_id；env GHRAH_SUBJECT_PLUGIN_TRUST 逗号分隔）
    """

    discoverable: list[str] = field(default_factory=list)

    @property
    def trust_set(self) -> frozenset[str]:
        """信任集合（去重、保序无关）。"""

        return frozenset(self.discoverable)


@dataclass
class TaskStoreConfig:
    """任务归因内核切片（TaskStoreUnit 配置；**可选组件**，非系统必要组件）。

    目标装配形态是插件（经 ``ghrah.plugins`` 发现 + 信任闸 + Project 装配
    清单挂载）；当前经 builtin 链挂载属待纠正偏离。

    Attributes:
        enabled: 是否挂载 TaskStoreUnit（False 时归因命令回落 Unknown command；
            env GHRAH_SUBJECT_TASKSTORE_ENABLED）
        db_path: 内核三表 sqlite 路径（env GHRAH_SUBJECT_TASKSTORE_DB）
    """

    enabled: bool = True
    db_path: str = os.path.expanduser("~/.ghrah/taskstore.sqlite3")


@dataclass
class SubjectConfig:
    """Subject 运行时配置（各 Unit 配置切片的容器）。

    Subject 是系统的控制面/执行层，持有工作区、执行沙箱命令、
    持久化 ActionChain、裁决 HITL、组装跨 Agent 上下文。

    向后兼容：旧 flat 字段（workspace_root/db_path/manifest_root/hitl_policy/core/log_level）
    仍可被 SubjectConfig(workspace_root=...) 构造。Unit 经 ``config.<slice>`` 读自己那一片；
    ``__post_init__`` 负责在 slice 未显式传入时由 flat 字段派生。

    Slice 字段（persistence/sandbox/manifest/hitl）通过 InitVar 传入，
    读期以只读 property 暴露为非 Optional 类型（mypy strict 友好）。

    Attributes:
        workspace_root: 工作区根路径（Subject 持有的文件系统根目录）
        db_path: SQLite 数据库文件路径（持久化 ActionChain 和上下文）
        manifest_root: manifest 存储根路径
        hitl_policy: HITL 权限策略配置
        core: Core 传输层配置
        log_level: 日志级别（根 logger）
        log_levels: 定向日志级别（形如 ``ghrah=DEBUG,aiosqlite=WARNING``；只改指定
            logger，根级别不受影响——用于在端侧观察执行轨迹而不被第三方库洪泛）
        log_file: 日志文件路径（空 = 只写终端；非空 = 终端与文件双写）
        transport: transport kind 配置（websocket/ipc/grpc）
        enabled_third_party_units: 第三方 Unit allowlist
    """

    # 旧 flat 字段保留，避免破坏 scripts/start_all.py / conftest.py / 外部构造代码。
    workspace_root: str = os.path.expanduser("~/ghrah-workspace")
    db_path: str = os.path.expanduser("~/.ghrah/subject.db")
    manifest_root: str = os.path.expanduser("~/.ghrah/manifests")
    hitl_policy: HITLPolicyConfig = field(default_factory=HITLPolicyConfig)
    core: CoreTransportConfig = field(default_factory=CoreTransportConfig)
    log_level: str = "INFO"
    log_levels: str = ""
    log_file: str = ""

    # slice 显式传入入口（InitVar：不成为实例字段，仅参与 __post_init__）。
    persistence_slice: InitVar[PersistenceConfig | None] = None
    sandbox_slice: InitVar[SandboxUnitConfig | None] = None
    manifest_slice: InitVar[ManifestConfig | None] = None
    hitl_slice: InitVar[HITLPolicyConfig | None] = None
    project_slice: InitVar[ProjectConfig | None] = None
    recovery_slice: InitVar[RecoveryConfig | None] = None
    room_filter_slice: InitVar[RoomFilterConfig | None] = None
    plugin_trust_slice: InitVar[PluginTrustConfig | None] = None
    taskstore_slice: InitVar[TaskStoreConfig | None] = None
    skill_slice: InitVar[SkillConfig | None] = None

    # 非 slice 的新字段（有默认值，可直接构造）。
    transport: TransportKindConfig = field(default_factory=TransportKindConfig)
    enabled_third_party_units: list[str] = field(default_factory=list)
    # spawn 默认能力集——部署方的显式声明（fail-closed 兼容通道）：
    # Observer 无 abilities 快速 spawn 经 CoreUnitConfig.default_abilities
    # 注入此集；置空列表 = 关闭快速 spawn（必须 manifest_ref/显式 abilities）。
    default_spawn_abilities: list[str] = field(default_factory=lambda: ["conversation", "end_task"])

    # private backing（init=False，__post_init__ 后必有值；保证 property 非 Optional）。
    _persistence: PersistenceConfig = field(init=False)
    _sandbox: SandboxUnitConfig = field(init=False)
    _manifest: ManifestConfig = field(init=False)
    _hitl: HITLPolicyConfig = field(init=False)
    _project: ProjectConfig = field(init=False)
    _recovery: RecoveryConfig = field(init=False)
    _room_filter: RoomFilterConfig = field(init=False)
    _plugin_trust: PluginTrustConfig = field(init=False)
    _taskstore: TaskStoreConfig = field(init=False)
    _skill: SkillConfig = field(init=False)

    def __post_init__(
        self,
        persistence_slice: PersistenceConfig | None,
        sandbox_slice: SandboxUnitConfig | None,
        manifest_slice: ManifestConfig | None,
        hitl_slice: HITLPolicyConfig | None,
        project_slice: ProjectConfig | None,
        recovery_slice: RecoveryConfig | None,
        room_filter_slice: RoomFilterConfig | None,
        plugin_trust_slice: PluginTrustConfig | None,
        taskstore_slice: TaskStoreConfig | None,
        skill_slice: SkillConfig | None,
    ) -> None:
        self._persistence = persistence_slice or PersistenceConfig(db_path=self.db_path)
        self._sandbox = sandbox_slice or SandboxUnitConfig(
            workspace_root=self.workspace_root,
            default_timeout=self.core.command_timeout,
        )
        self._manifest = manifest_slice or ManifestConfig(manifest_root=self.manifest_root)
        self._hitl = hitl_slice or self.hitl_policy
        # project slice：bootstrap_workspace_locator 为空时派生自 sandbox.workspace_root
        # + "/projects/default"（flat 兼容真相）。
        self._project = project_slice or ProjectConfig(
            bootstrap_workspace_locator=(
                ""
                if not self._sandbox.workspace_root
                else self._sandbox.workspace_root.rstrip("/") + "/projects/default"
            ),
        )
        self._recovery = recovery_slice or RecoveryConfig()
        self._room_filter = room_filter_slice or RoomFilterConfig()
        self._plugin_trust = plugin_trust_slice or PluginTrustConfig()
        self._taskstore = taskstore_slice or TaskStoreConfig()
        self._skill = skill_slice or SkillConfig()

    # ── 只读 slice property（非 Optional，mypy strict 友好）──

    @property
    def persistence(self) -> PersistenceConfig:
        return self._persistence

    @property
    def core_db_path(self) -> str:
        """Core agent 链 sqlite 路径（聚合裁决 D-C：真相源在 Core 内建 backend）。

        与 subject 自身 db 同目录独立文件（``ghrah.db``）。registry 构造
        CoreUnit 的 persistence_factory 与 ledger 读侧投影共用本派生，
        保证读写同一文件。
        """
        return str(Path(self._persistence.db_path).parent / "ghrah.db")

    @property
    def sandbox(self) -> SandboxUnitConfig:
        return self._sandbox

    @property
    def manifest(self) -> ManifestConfig:
        return self._manifest

    @property
    def hitl(self) -> HITLPolicyConfig:
        return self._hitl

    @property
    def project(self) -> ProjectConfig:
        return self._project

    @property
    def recovery(self) -> RecoveryConfig:
        return self._recovery

    @property
    def room_filter(self) -> RoomFilterConfig:
        return self._room_filter

    @property
    def plugin_trust(self) -> PluginTrustConfig:
        return self._plugin_trust

    @property
    def taskstore(self) -> TaskStoreConfig:
        return self._taskstore

    @property
    def skill(self) -> SkillConfig:
        return self._skill

    @classmethod
    def from_env(cls) -> SubjectConfig:
        """从环境变量创建配置。

        环境变量命名规则：GHRAH_SUBJECT_<大写属性名>
        例如：GHRAH_SUBJECT_WORKSPACE_ROOT, GHRAH_SUBJECT_DB_PATH

        嵌套配置使用双下划线分隔：
        例如：GHRAH_SUBJECT_CORE_URL, GHRAH_SUBJECT_HITL_POLICY_WORKSPACE_ROOT

        - GHRAH_SUBJECT_SANDBOX_DEFAULT_TIMEOUT（回退 GHRAH_SUBJECT_CORE_COMMAND_TIMEOUT）
        - GHRAH_SUBJECT_ABILITY_HITL_TIMEOUT（回退 GHRAH_SUBJECT_CORE_COMMAND_TIMEOUT）
        - GHRAH_SUBJECT_TRANSPORT_CORE_KIND / GHRAH_SUBJECT_TRANSPORT_OBSERVER_KIND
        - GHRAH_SUBJECT_ENABLED_UNITS（逗号分隔，第三方 Unit allowlist）
        - GHRAH_SUBJECT_PROJECT_BOOTSTRAP_WORKSPACE_LOCATOR
          （空→派生自 sandbox.workspace_root + "/projects/default"）
        - GHRAH_SUBJECT_PROJECT_DEFAULT_ROOT_LOCATOR_TEMPLATE
        - GHRAH_SUBJECT_RECOVERY_ENABLED
        - GHRAH_SUBJECT_RECOVERY_SUBJECT_ID
        - GHRAH_SUBJECT_RECOVERY_ON_UNKNOWN_WORKSPACE（resume|pause|drop）
        - GHRAH_SUBJECT_RECOVERY_RECONCILE_ON_START
        - GHRAH_SUBJECT_RECOVERY_BOOTSTRAP_DEFAULT_PROJECT
        - GHRAH_SUBJECT_PLUGIN_TRUST（逗号分隔，插件信任清单 plugin_id）
        - GHRAH_SUBJECT_TASKSTORE_ENABLED（任务归因内核 unit 挂载开关）
        - GHRAH_SUBJECT_TASKSTORE_DB（任务归因内核 sqlite 路径）
        - GHRAH_SUBJECT_SKILL_ROOT（skill 存储根路径，装配期自举 + 补 lock）
        - GHRAH_SUBJECT_ROOM_FILTER_ENABLED
        - GHRAH_SUBJECT_ROOM_FILTER_ABILITIES（逗号分隔白名单能力名）
        - GHRAH_SUBJECT_SAFE_EXTRA_COMMANDS（逗号/分号/空白分隔，追加基础命令 safe 白名单）
        - GHRAH_SUBJECT_SAFE_EXTRA_SUB_COMMANDS（base:sub1,sub2;base2:sub3 形状，
          追加子命令 safe 白名单；只增不减，保持 fail-closed）
        - GHRAH_SUBJECT_HITL_CONSECUTIVE_TIMEOUT_LIMIT（连续超时熔断阈值，0=禁用）
        - GHRAH_SUBJECT_HITL_DEGRADED_TIMEOUT（熔断降级后的等待秒数，默认 30）
        - GHRAH_SUBJECT_ENVIRONMENT_INJECTION（环境信息注入开关，true/false）
        """
        hitl_policy = HITLPolicyConfig(
            auto_approve_abilities=os.environ.get(
                "GHRAH_SUBJECT_HITL_AUTO_APPROVE_ABILITIES", ""
            ).split(",")
            if os.environ.get("GHRAH_SUBJECT_HITL_AUTO_APPROVE_ABILITIES")
            else [],
            require_approval_by_default=os.environ.get(
                "GHRAH_SUBJECT_HITL_REQUIRE_APPROVAL", "true"
            ).lower()
            in ("true", "1", "yes"),
            allowed_paths=os.environ.get("GHRAH_SUBJECT_HITL_ALLOWED_PATHS", "").split(";")
            if os.environ.get("GHRAH_SUBJECT_HITL_ALLOWED_PATHS")
            else [],
            workspace_root=os.environ.get("GHRAH_SUBJECT_HITL_WORKSPACE_ROOT"),
            safe_extra_commands=_split_tokens(
                os.environ.get("GHRAH_SUBJECT_SAFE_EXTRA_COMMANDS", "")
            ),
            safe_extra_sub_commands=_parse_sub_commands(
                os.environ.get("GHRAH_SUBJECT_SAFE_EXTRA_SUB_COMMANDS", "")
            ),
            hitl_consecutive_timeout_limit=_safe_int(
                os.environ.get("GHRAH_SUBJECT_HITL_CONSECUTIVE_TIMEOUT_LIMIT", "0"),
                0,
                "GHRAH_SUBJECT_HITL_CONSECUTIVE_TIMEOUT_LIMIT",
            )
            or 0,
            hitl_degraded_timeout=_safe_float(
                os.environ.get("GHRAH_SUBJECT_HITL_DEGRADED_TIMEOUT", "30.0"),
                30.0,
                "GHRAH_SUBJECT_HITL_DEGRADED_TIMEOUT",
            ),
            environment_injection=os.environ.get(
                "GHRAH_SUBJECT_ENVIRONMENT_INJECTION", "false"
            ).lower()
            in ("true", "1", "yes"),
        )

        core = CoreTransportConfig(
            url=os.environ.get("GHRAH_SUBJECT_CORE_URL", "ws://localhost:4111/ws"),
            reconnect_interval=_safe_float(
                os.environ.get("GHRAH_SUBJECT_CORE_RECONNECT_INTERVAL", "5.0"),
                5.0,
                "GHRAH_SUBJECT_CORE_RECONNECT_INTERVAL",
            ),
            ping_interval=_safe_float(
                os.environ.get("GHRAH_SUBJECT_CORE_PING_INTERVAL", "30.0"),
                30.0,
                "GHRAH_SUBJECT_CORE_PING_INTERVAL",
            ),
            command_timeout=_safe_float(
                os.environ.get("GHRAH_SUBJECT_CORE_COMMAND_TIMEOUT", "300.0"),
                300.0,
                "GHRAH_SUBJECT_CORE_COMMAND_TIMEOUT",
            ),
            max_reconnect_attempts=_safe_int(
                os.environ.get("GHRAH_SUBJECT_CORE_MAX_RECONNECT_ATTEMPTS"),
                None,
                "GHRAH_SUBJECT_CORE_MAX_RECONNECT_ATTEMPTS",
            ),
            cluster_id=os.environ.get("GHRAH_SUBJECT_CORE_CLUSTER_ID", "default"),
        )

        # sandbox default_timeout：专用 env，未设置时回退到 core.command_timeout（向后兼容）
        sandbox_default_timeout_env = os.environ.get("GHRAH_SUBJECT_SANDBOX_DEFAULT_TIMEOUT")
        if sandbox_default_timeout_env:
            sandbox_default_timeout = _safe_float(
                sandbox_default_timeout_env,
                core.command_timeout,
                "GHRAH_SUBJECT_SANDBOX_DEFAULT_TIMEOUT",
            )
        else:
            sandbox_default_timeout = core.command_timeout

        transport = TransportKindConfig(
            core=os.environ.get("GHRAH_SUBJECT_TRANSPORT_CORE_KIND", "websocket"),
            observer=os.environ.get("GHRAH_SUBJECT_TRANSPORT_OBSERVER_KIND", "websocket"),
        )

        enabled_third_party_units = [
            u.strip()
            for u in os.environ.get("GHRAH_SUBJECT_ENABLED_UNITS", "").split(",")
            if u.strip()
        ]

        workspace_root = os.environ.get(
            "GHRAH_SUBJECT_WORKSPACE_ROOT", os.path.expanduser("~/ghrah-workspace")
        )

        # project slice：bootstrap_workspace_locator 空时由 __post_init__ 派生。
        project_bootstrap_workspace_locator = os.environ.get(
            "GHRAH_SUBJECT_PROJECT_BOOTSTRAP_WORKSPACE_LOCATOR", ""
        )
        project_default_root_locator_template = os.environ.get(
            "GHRAH_SUBJECT_PROJECT_DEFAULT_ROOT_LOCATOR_TEMPLATE",
            "~/.ghrah/projects/{project_id}",
        )
        project_slice = ProjectConfig(
            bootstrap_workspace_locator=project_bootstrap_workspace_locator,
            default_root_locator_template=project_default_root_locator_template,
        )

        # recovery slice
        _on_unknown_raw = os.environ.get(
            "GHRAH_SUBJECT_RECOVERY_ON_UNKNOWN_WORKSPACE", "pause"
        ).lower()
        try:
            on_unknown_workspace = RecoveryAction(_on_unknown_raw)
        except ValueError:
            logger.warning(
                "Invalid RecoveryAction for "
                "GHRAH_SUBJECT_RECOVERY_ON_UNKNOWN_WORKSPACE=%r, using default PAUSE",
                _on_unknown_raw,
            )
            on_unknown_workspace = RecoveryAction.PAUSE
        recovery_slice = RecoveryConfig(
            enabled=os.environ.get("GHRAH_SUBJECT_RECOVERY_ENABLED", "true").lower()
            in ("true", "1", "yes"),
            subject_id=os.environ.get("GHRAH_SUBJECT_RECOVERY_SUBJECT_ID", "default"),
            on_unknown_workspace=on_unknown_workspace,
            reconcile_on_start=os.environ.get(
                "GHRAH_SUBJECT_RECOVERY_RECONCILE_ON_START", "true"
            ).lower()
            in ("true", "1", "yes"),
            bootstrap_default_project=os.environ.get(
                "GHRAH_SUBJECT_RECOVERY_BOOTSTRAP_DEFAULT_PROJECT", "true"
            ).lower()
            in ("true", "1", "yes"),
        )

        # room_filter slice
        room_filter_slice = RoomFilterConfig(
            enabled=os.environ.get("GHRAH_SUBJECT_ROOM_FILTER_ENABLED", "true").lower()
            in ("true", "1", "yes"),
            abilities=tuple(
                a.strip()
                for a in os.environ.get(
                    "GHRAH_SUBJECT_ROOM_FILTER_ABILITIES", "conversation,end_task"
                ).split(",")
                if a.strip()
            ),
        )

        # plugin_trust slice（供 verify 默认模式与装配链挂载期强制执行）
        plugin_trust_slice = PluginTrustConfig(
            discoverable=[
                p.strip()
                for p in os.environ.get("GHRAH_SUBJECT_PLUGIN_TRUST", "").split(",")
                if p.strip()
            ],
        )

        # taskstore slice（任务归因内核）
        taskstore_slice = TaskStoreConfig(
            enabled=os.environ.get("GHRAH_SUBJECT_TASKSTORE_ENABLED", "true").lower()
            in ("true", "1", "yes"),
            db_path=os.environ.get(
                "GHRAH_SUBJECT_TASKSTORE_DB", os.path.expanduser("~/.ghrah/taskstore.sqlite3")
            ),
        )

        # skill slice（skill 根目录；装配期由 SkillUnit 做一次初始化检查）
        skill_slice = SkillConfig(
            root=os.environ.get(
                "GHRAH_SUBJECT_SKILL_ROOT", os.path.expanduser("~/.ghrah/skill-ability")
            ),
        )

        return cls(
            workspace_root=workspace_root,
            db_path=os.environ.get(
                "GHRAH_SUBJECT_DB_PATH", os.path.expanduser("~/.ghrah/subject.db")
            ),
            manifest_root=os.environ.get(
                "GHRAH_SUBJECT_MANIFEST_ROOT", os.path.expanduser("~/.ghrah/manifests")
            ),
            hitl_policy=hitl_policy,
            core=core,
            log_level=os.environ.get("GHRAH_SUBJECT_LOG_LEVEL", "INFO"),
            log_levels=os.environ.get("GHRAH_SUBJECT_LOG_LEVELS", ""),
            log_file=os.environ.get("GHRAH_SUBJECT_LOG_FILE", ""),
            # slice 显式传入（保证 env 优先级；persistence/manifest/hitl 由 flat 派生）
            sandbox_slice=SandboxUnitConfig(
                workspace_root=workspace_root,
                default_timeout=sandbox_default_timeout,
            ),
            transport=transport,
            enabled_third_party_units=enabled_third_party_units,
            project_slice=project_slice,
            recovery_slice=recovery_slice,
            room_filter_slice=room_filter_slice,
            plugin_trust_slice=plugin_trust_slice,
            taskstore_slice=taskstore_slice,
            skill_slice=skill_slice,
        )


# 别名兼容：CoreConnectionConfig → CoreTransportConfig（改名，保留旧名供外部引用）
CoreConnectionConfig = CoreTransportConfig
