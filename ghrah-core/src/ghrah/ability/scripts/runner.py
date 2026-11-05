# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``run_script`` 的进程外入口。

脚本不走 Operation 的成员契约，也不走 ``execute_command``。结果里的
``action`` 固定为 ``script.call``。模型工具数组何时露出这个名字，不在这里决定。
"""

from __future__ import annotations

import logging
from typing import Any

from ghrah.ability.access.errors import AccessError
from ghrah.ability.access.store import AbilityAccessStore, AccessIdentity
from ghrah.ability.scripts.process import (
    DEFAULT_DEADLINE_MS,
    POSIX_GRACE_SECONDS,
    ScriptRun,
    Spawner,
    build_envelope,
    ensure_script_runtime,
    run_script_process,
    script_environment,
)
from ghrah.ability.scripts.resolve import ScriptRejectionError, parse_model_address, prepare_script
from ghrah.ability.source import BuiltinAbilitySource
from ghrah.types.results import ActionOutcome, ActionResult

logger = logging.getLogger(__name__)

_ACCESS_KIND = {
    "not_found": ("not_found", "没有已获授的脚本"),
    "conflict": ("invalid_input", "脚本地址在多个已获授包中重名，无法确定"),
    "unavailable": ("not_found", "脚本所在的包版本不可用"),
    "access_denied": ("permission_denied", "脚本可见但未授予执行权（script_exec）"),
}


async def execute_run_script(
    *,
    store: AbilityAccessStore,
    actor: AccessIdentity,
    agent: str,
    source: BuiltinAbilitySource,
    script: str,
    arguments: Any,
    tool_call_id: str,
    deadline_ms: int = DEFAULT_DEADLINE_MS,
    spawner: Spawner | None = None,
    cancel: Any = None,
    grace_s: float = POSIX_GRACE_SECONDS,
) -> ActionResult:
    """执行一个已登记的 Skill 脚本。

    地址越界、未授权、语言不对、参数不是对象，都在创建子进程之前返回。
    工作目录取该次授予实例的根，不从 ``arguments`` 里解释路径。

    Args:
        store: 项目关系库。
        actor: 当前 Agent 的受信身份。
        agent: Agent 标识。
        source: 内置树与安装根，用来定位包目录。
        script: 模型地址 ``skills/<skill>/scripts/<key>``。
        arguments: 脚本参数。必须是 JSON 对象，语义由脚本自己解释。
        tool_call_id: 本次调用标识，写入信封并随 ``script.call`` 回灌。
        deadline_ms: 本次预算，默认 120 秒。脚本不声明自己的超时。
        spawner: 测试用进程工厂。生产路径留空。
        cancel: ``asyncio.Event``。置位后终止进程树并返回 ``cancelled``。
        grace_s: POSIX 宽限秒数。生产默认 2 秒；测试可以缩短。

    Returns:
        ``data["action"]`` 恒为 ``script.call``。只有脚本成功信封才是
        ``ActionOutcome.SUCCESS``。stderr 不在 ``data`` 里。
    """
    ensure_script_runtime()
    if not isinstance(script, str):
        script = ""
    if type(tool_call_id) is not str or not tool_call_id:
        return _result(script, _failure_run("invalid_input", "tool_call_id 必须是非空字符串"))
    if type(deadline_ms) is not int or deadline_ms <= 0:
        return _result(script, _failure_run("invalid_input", "deadline_ms 必须是正整数"))
    if type(arguments) is not dict or any(type(key) is not str for key in arguments):
        return _result(script, _failure_run("invalid_input", "arguments 必须是 JSON 对象"))
    if actor.subject_id != agent or actor.run_id != _current_run(store, agent):
        return _result(script, _failure_run("permission_denied", "当前 run 与调用身份不一致"))
    try:
        skill, key = parse_model_address(script)
        target = await prepare_script(
            store=store,
            actor=actor,
            agent=agent,
            source=source,
            model_address=script,
            skill=skill,
            key=key,
        )
        env = script_environment()
        envelope = build_envelope(
            script=script,
            arguments=arguments,
            tool_call_id=tool_call_id,
            deadline_ms=deadline_ms,
            workspace=target.workspace,
            package_root=target.package_root,
            script_path=target.script_path,
            env=env,
        )
    except ScriptRejectionError as exc:
        return _result(script, _failure_run(exc.kind, exc.message))
    except AccessError as exc:
        kind, message = _ACCESS_KIND.get(exc.code, ("permission_denied", "脚本调用被拒绝"))
        return _result(script, _failure_run(kind, message))
    except ValueError as exc:
        return _result(script, _failure_run("invalid_input", str(exc)))
    outcome = await run_script_process(
        script_path=target.script_path,
        cwd=target.workspace,
        env=env,
        envelope=envelope,
        deadline_s=deadline_ms / 1000,
        grace_s=grace_s,
        spawner=spawner,
        cancel=cancel,
    )
    return _result(script, outcome)


def _current_run(store: AbilityAccessStore, agent: str) -> str:
    """读不到当前 run 时按空串处理，调用方会拒绝而不是起进程。"""
    try:
        return store.current_run(agent)
    except AccessError:
        return ""


def _failure_run(kind: str, message: str) -> ScriptRun:
    return ScriptRun(False, kind, message, None, False)


def _result(script: str, outcome: ScriptRun) -> ActionResult:
    payload: dict[str, Any] = {
        "action": "script.call",
        "script": script,
        "ok": outcome.ok,
        "effect_unknown": outcome.effect_unknown,
    }
    if outcome.ok:
        payload["data"] = outcome.data
        logged = "ok"
    else:
        payload["kind"] = outcome.kind
        payload["error"] = outcome.error
        logged = outcome.kind
    logger.info(
        "script.call script=%s kind=%s effect_unknown=%s",
        script,
        logged,
        outcome.effect_unknown,
    )
    if outcome.stderr:
        text = outcome.stderr.decode("utf-8", errors="replace")
        logger.info("script stderr script=%s: %s", script, text)
    return ActionResult(
        ActionOutcome.SUCCESS if outcome.ok else ActionOutcome.FAILURE,
        payload,
    )
