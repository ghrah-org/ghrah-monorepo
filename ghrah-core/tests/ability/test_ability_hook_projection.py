# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""内置树的 hook 投影边界与 ``require_hitl`` 物化回归。

两条约束来自 Hook 管辖权裁定：

1. 挂载面只携带声明式安全语义，不携带 hook 实质或 hook 引用；因此内置树
   投影出的 ``AbilityManifest.hooks`` 必须为空。
2. ``require_hitl`` 这类声明必须在物化时真正生效——不能只写在 manifest 里
   却没有对应的 hook 被挂上。
"""

from __future__ import annotations

from _helpers import make_call, make_services

from ghrah.ability.builtin.core.operations.conversation import (
    ConversationAbility,
    ConversationDoneHook,
)
from ghrah.ability.builtin.core.operations.end_task import EndTaskAbility, EndTaskDoneHook
from ghrah.ability.execution.ability_build import instantiate_resolved_abilities
from ghrah.ability.execution.hooks import Hook, HookPoint
from ghrah.ability.loading.ability_catalog import load_all_builtin_manifests
from ghrah.ability.permissions.fs_permissions import AccessApprovalHook
from ghrah.manifest.ability import AbilityHooks
from ghrah.manifest.resolver import ResolvedAbility
from ghrah.manifest.types import ImplementationDef, PermissionFlags
from ghrah.types.results import ActionOutcome


def _resolved(handler: str) -> ResolvedAbility:
    """构造一个只指定内置 handler 的已解析能力。"""
    return ResolvedAbility(
        ability_name=f"ghrah.core.{handler}",
        tool_schema=None,
        permissions=PermissionFlags(),
        implementation=ImplementationDef(type="builtin", handler=handler),
        hooks=AbilityHooks(),
    )


def test_builtin_tree_projects_no_hook_declarations() -> None:
    """内置树的每个成员都不携带 hook 引用。"""
    manifests = load_all_builtin_manifests()

    assert manifests
    for full_name, manifest in manifests.items():
        assert manifest.hooks.pre_execute == [], full_name
        assert manifest.hooks.post_execute == [], full_name
        assert manifest.hooks.on_error == [], full_name


def test_require_hitl_materializes_fs_approval_hook() -> None:
    """``require_hitl`` 声明经物化变成真实的 FS 审批 hook。"""
    resolved = ResolvedAbility(
        ability_name="ghrah.fs.write_file",
        tool_schema=None,
        permissions=PermissionFlags(require_hitl=True),
        implementation=ImplementationDef(type="builtin", handler="write_file"),
        hooks=AbilityHooks(),
    )

    abilities = instantiate_resolved_abilities([resolved], workspace_root=None)

    assert len(abilities) == 1
    hooks = abilities[0].get_hooks()
    assert len(hooks) == 1
    assert isinstance(hooks[0], AccessApprovalHook)


def test_core_done_hooks_survive_materialization() -> None:
    """conversation / end_task 的终止 hook 来自构造函数，不依赖 manifest 声明。"""
    abilities = instantiate_resolved_abilities(
        [_resolved("conversation"), _resolved("end_task")], workspace_root=None
    )

    conversation, end_task = abilities
    assert type(conversation) is ConversationAbility
    assert type(end_task) is EndTaskAbility
    assert end_task.mode == "toolcall"

    conversation_hooks = conversation.get_hooks()
    assert len(conversation_hooks) == 1
    assert isinstance(conversation_hooks[0], ConversationDoneHook)
    assert conversation_hooks[0].hook_point is HookPoint.AFTER_ACTION
    assert isinstance(conversation_hooks[0], Hook)

    end_task_hooks = end_task.get_hooks()
    assert len(end_task_hooks) == 1
    assert isinstance(end_task_hooks[0], EndTaskDoneHook)
    assert end_task_hooks[0].hook_point is HookPoint.AFTER_ACTION
    assert isinstance(end_task_hooks[0], Hook)


async def test_done_hooks_stop_only_their_own_ability() -> None:
    """两个终止 hook 各自只响应自己的成员，并返回停止指令。"""
    conversation_call = make_call(member="conversation")
    conversation_services = make_services(loop={"llm_response": "hello"})
    end_task_call = make_call(member="end_task", arguments={"summary": "done"})
    end_task_services = make_services()

    conversation_hook = ConversationDoneHook()
    assert await conversation_hook.should_trigger(conversation_call, conversation_services) is True
    assert await conversation_hook.should_trigger(end_task_call, end_task_services) is False
    assert (
        await conversation_hook.execute(conversation_call, conversation_services, None)
    ).should_continue is False

    end_task_hook = EndTaskDoneHook()
    assert await end_task_hook.should_trigger(end_task_call, end_task_services) is True
    assert await end_task_hook.should_trigger(conversation_call, conversation_services) is False
    assert (
        await end_task_hook.execute(end_task_call, end_task_services, None)
    ).should_continue is False


async def test_done_hooks_stop_through_legacy_runner() -> None:
    """旧 HookStore / HookRunner 能选出并执行新内置成员挂载的终止 hook。"""
    from ghrah.ability.execution.hook_runner import HookRunner
    from ghrah.ability.execution.hook_store import HookStore

    store = HookStore()
    store.add_hooks("conversation", ConversationAbility().get_hooks())
    store.add_hooks("end_task", EndTaskAbility(mode="toolcall").get_hooks())
    runner = HookRunner(store)

    for name in ("conversation", "end_task"):
        result = await runner.run(HookPoint.AFTER_ACTION, make_call(member=name), make_services())
        assert result is not None
        assert result.should_continue is False


async def test_core_abilities_execute_after_declaration_removal() -> None:
    """移除声明后，两个核心成员仍返回正常的成功结果。"""
    conversation = ConversationAbility()
    conversation_result = await conversation.execute(
        make_call(member="conversation"),
        make_services(loop={"llm_response": "hello"}),
    )
    assert conversation_result.outcome is ActionOutcome.SUCCESS
    assert conversation_result.data == {"response": "hello"}

    end_task = EndTaskAbility(mode="toolcall")
    end_task_result = await end_task.execute(
        make_call(member="end_task", arguments={"summary": "bye"}),
        make_services(),
    )
    assert end_task_result.outcome is ActionOutcome.SUCCESS
    assert end_task_result.data == {"response": "bye"}
