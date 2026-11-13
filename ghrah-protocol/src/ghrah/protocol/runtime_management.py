"""Runtime 资源管理扩展；复用能力域作者模型，不引入第二份授权事实。"""

from typing import Literal

from pydantic import Field

from ghrah.protocol.ability_control import (
    AbilityBoundToolView,
    AbilityChangeView,
    AbilityGrantView,
    AbilityPackageVersionRow,
    AbilitySurfaceView,
)
from ghrah.protocol.ability_tree import MemberAxes, SkillReadScope, TreeId, TreeKey
from ghrah.protocol.runtime import RuntimeRequest, RuntimeResponse
from ghrah.protocol.runtime_agents import RuntimeAssemblyInput
from ghrah.protocol.runtime_resources import RuntimeAgentId


class RuntimeAgentPatch(RuntimeRequest):
    """停止态按 If-Match 原子替换完整配置，外部业务键保持不变。"""

    config: RuntimeAssemblyInput


class RuntimeAbilityCatalog(RuntimeResponse):
    """当前宿主可登记的内置包目录，不隐式安装第三方包。"""

    packages: list[AbilityPackageVersionRow]


class RuntimeAbilityInstanceCreate(RuntimeRequest):
    """显式登记内置包快照；登记实例本身不授予权限。"""

    package: str = Field(min_length=1)
    instance_id: TreeId | None = None
    allowed_roots: list[str] = Field(default_factory=list)


class RuntimeAbilityInstance(RuntimeResponse):
    """内容寻址版本对应的项目内实例。"""

    instance_id: TreeId
    package: str
    version_digest: str
    revision: int = Field(ge=1)
    operations: list[str]
    skills: list[str]


class RuntimeGrantRequest(RuntimeRequest):
    """授予替换请求；关系修订由 If-Match 提供，不接受自报操作员身份。"""

    alias: TreeKey
    instance_id: TreeId
    members: list[TreeKey] = Field(default_factory=list)
    allowed_roots: list[str] = Field(default_factory=list)
    skill: SkillReadScope | None = None
    skills: list[SkillReadScope] = Field(default_factory=list)
    whole_container: bool = False
    axes: list[MemberAxes] = Field(default_factory=list)


class RuntimeToolRequest(RuntimeRequest):
    """只能从已授予成员中选择工具，不能借此新增权限。"""

    alias: TreeKey
    member: TreeKey
    tool_name: TreeKey


class RuntimeAbilitiesView(RuntimeResponse):
    """权威授权与工具选择，以及当前运行快照的派生比较。"""

    agent_id: RuntimeAgentId
    revision: int = Field(ge=0)
    state: Literal["unassembled", "running", "stopped"]
    run_id: str | None
    requires_respawn: bool
    grants: list[AbilityGrantView]
    bound_tools: list[AbilityBoundToolView]
    surface: AbilitySurfaceView


class RuntimeAbilityChanges(RuntimeResponse):
    """有界、可读通的能力变更审计；授权重建依赖当前事实而非事件写回。"""

    changes: list[AbilityChangeView]
    next_after_seq: int = Field(ge=0)
    has_more: bool
