# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Skill 脚本运行器：七条路径都在起进程之前或之后给出确定结果。"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

import pytest
from _helpers import symlink_or_skip
from ghrah.protocol.ability_tree import GrantAbilityRequest, MemberAxes, SkillReadScope

from ghrah.ability.access import AbilityAccessStore, AccessIdentity
from ghrah.ability.package.generate import build
from ghrah.ability.package.installer import AbilityInstaller
from ghrah.ability.package.provider import InstalledAbilityProvider
from ghrah.ability.scripts import execute_run_script, process
from ghrah.ability.source import BuiltinAbilitySource
from ghrah.ability.versions import AbilityVersion, capture_ability_version
from ghrah.types.results import ActionOutcome

ADMIN = AccessIdentity("project", "admin", "control", True)
ALICE = AccessIdentity("project", "alice", "run-1")
ADDRESS = "skills/guide/scripts/export"

SUCCESS = """\
import json, os, sys
from pathlib import Path
req = json.load(sys.stdin)
Path("marker.txt").write_text("ok", encoding="utf-8")
sys.stderr.write("diagnostic\\n")
json.dump({
    "ok": True,
    "data": {
        "format": req["arguments"]["format"],
        "protocol": req["protocol"],
        "has_home": "HOME" in os.environ,
        "pythonpath": "PYTHONPATH" in os.environ,
        "utf8": os.environ.get("PYTHONUTF8"),
    },
}, sys.stdout)
sys.stdout.flush()
"""

FAILURE = """\
import json, sys
json.load(sys.stdin)
json.dump({
    "ok": False,
    "error": "template not found",
    "kind": "not_found",
    "effect_unknown": False,
}, sys.stdout)
sys.stdout.flush()
raise SystemExit(1)
"""

SLEEP = """\
import json, sys, time
json.load(sys.stdin)
time.sleep(30)
"""

SKILL = """\
---
name: guide
description: 脚本测试技能。
---

# Guide
"""


def _header(name: str) -> str:
    return (
        "format: ghrah.ability-definition\n"
        "schema_version: '1'\n"
        f"identity: {{namespace: acme, name: {name}, version: '1.0.0'}}\n"
        "title: Tools\n"
        "description: Script package.\n"
        "members: {}\n"
    )


def _install_python(
    tmp_path: Path,
    script: str,
    *,
    name: str = "tools",
    install_root: Path | None = None,
) -> AbilityVersion:
    """生成并安装一个只含 ``export.py`` 的包，返回已安装副本的版本。"""
    source = tmp_path / f"source-{name}"
    (source / "skills" / "guide" / "scripts").mkdir(parents=True)
    (source / "ability.yaml").write_text(_header(name), encoding="utf-8", newline="\n")
    (source / "skills" / "guide" / "SKILL.md").write_text(SKILL, encoding="utf-8", newline="\n")
    (source / "skills" / "guide" / "scripts" / "export.py").write_text(
        script, encoding="utf-8", newline="\n"
    )
    build(source)
    root = install_root or (tmp_path / "installed")
    AbilityInstaller(root).install(source)
    return InstalledAbilityProvider(root).capture(f"acme.{name}")


def _install_shell(tmp_path: Path) -> AbilityVersion:
    """安装器接受手写索引里的非 Python 脚本；生成器会拒绝，所以这里不走 build。"""
    source = tmp_path / "source-shell"
    scripts = source / "skills" / "guide" / "scripts"
    scripts.mkdir(parents=True)
    (source / "skills" / "guide" / "SKILL.md").write_text(SKILL, encoding="utf-8", newline="\n")
    (scripts / "export.sh").write_text("echo no\n", encoding="utf-8", newline="\n")
    (source / "ability.yaml").write_text(
        "format: ghrah.ability-definition\n"
        "schema_version: '1'\n"
        "identity: {namespace: acme, name: shell, version: '1.0.0'}\n"
        "title: Shell\n"
        "description: Non-python script package.\n"
        "members:\n"
        "  operations: {}\n"
        "  skills:\n"
        "    guide:\n"
        "      title: Guide\n"
        "      description: Guide.\n"
        "      body:\n"
        "        path: skills/guide/SKILL.md\n"
        "        media_type: text/markdown\n"
        "      scripts:\n"
        "        export:\n"
        "          path: skills/guide/scripts/export.sh\n"
        "          media_type: text/x-shellscript\n",
        encoding="utf-8",
        newline="\n",
    )
    root = tmp_path / "installed"
    AbilityInstaller(root).install(source)
    installed = InstalledAbilityProvider(root).store.resolve("acme.shell", "1.0.0")
    assert installed is not None
    return capture_ability_version(
        (installed.root / "ability.yaml").read_text(encoding="utf-8"), root=installed.root
    )


async def _store(tmp_path: Path, *versions: tuple[str, AbilityVersion]) -> AbilityAccessStore:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = await AbilityAccessStore.open(
        tmp_path / "project.db",
        "project",
        operators=frozenset({"admin"}),
        runs={"alice": "run-1"}.get,
    )
    for instance, version in versions:
        await db.commit_version(ADMIN, version)
        await db.select_instance(ADMIN, instance, version.digest, workspace)
    await db.register_agent(ADMIN, "alice")
    return db


async def _grant(
    db: AbilityAccessStore,
    *,
    alias: str,
    instance: str,
    available: bool,
    expected_revision: int,
) -> int:
    axes = [MemberAxes(member="skill/guide/scripts/export", available=True)] if available else []
    view = await db.grant(
        ADMIN,
        "alice",
        GrantAbilityRequest(
            alias=alias,
            instance_id=instance,
            skill=SkillReadScope(key="guide"),
            axes=axes,
            expected_revision=expected_revision,
        ),
    )
    return view.revision


def _source(tmp_path: Path) -> BuiltinAbilitySource:
    return BuiltinAbilitySource(install_root=tmp_path / "installed")


def _forbid(calls: list[Any]):
    async def spawn(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        raise AssertionError("拒绝路径不应启动解释器")

    return spawn


def _counting(calls: list[Any]):
    async def spawn(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return await asyncio.create_subprocess_exec(*args, **kwargs)

    return spawn


async def _run(
    db: AbilityAccessStore,
    tmp_path: Path,
    *,
    script: str = ADDRESS,
    arguments: Any | None = None,
    spawner: Any = None,
    deadline_ms: int = 5_000,
    grace_s: float = 2.0,
    cancel: asyncio.Event | None = None,
    tool_call_id: str = "call-1",
):
    return await execute_run_script(
        store=db,
        actor=ALICE,
        agent="alice",
        source=_source(tmp_path),
        script=script,
        arguments={} if arguments is None else arguments,
        tool_call_id=tool_call_id,
        deadline_ms=deadline_ms,
        spawner=spawner,
        cancel=cancel,
        grace_s=grace_s,
    )


def _assert_audit(result, *, ok: bool, kind: str | None = None) -> None:
    assert result.data["action"] == "script.call"
    assert result.data["ok"] is ok
    assert "stderr" not in result.data
    assert "command" not in result.data
    assert "stdout" not in result.data
    if ok:
        assert result.outcome is ActionOutcome.SUCCESS
        assert result.data["effect_unknown"] is False
    else:
        assert result.outcome is ActionOutcome.FAILURE
        assert result.data["kind"] == kind


async def test_local_script_success_returns_data_and_hides_stderr(tmp_path, caplog) -> None:
    """成功信封回灌给调用方；stderr 只进日志。

    POSIX 让子进程自组进程组（``start_new_session``）以便整组终止；Windows
    用 ``CREATE_NO_WINDOW`` 抑制控制台窗口，且没有 ``start_new_session``。
    """
    version = _install_python(tmp_path, SUCCESS)
    db = await _store(tmp_path, ("pkg", version))
    await _grant(db, alias="tools", instance="pkg", available=True, expected_revision=0)
    calls: list[Any] = []
    caplog.set_level(logging.INFO)
    try:
        result = await _run(db, tmp_path, arguments={"format": "csv"}, spawner=_counting(calls))
    finally:
        await db.close()
    _assert_audit(result, ok=True)
    assert result.data["data"] == {
        "format": "csv",
        "protocol": "ghrah.script/1",
        "has_home": False,
        "pythonpath": False,
        "utf8": "1",
    }
    assert (tmp_path / "workspace" / "marker.txt").read_text(encoding="utf-8") == "ok"
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == sys.executable
    assert args[1].endswith("export.py")
    assert kwargs["cwd"] == str(tmp_path / "workspace")
    if os.name == "nt":
        assert kwargs["creationflags"] == process.CREATE_NO_WINDOW
        assert "start_new_session" not in kwargs
    else:
        assert kwargs["start_new_session"] is True
    assert "diagnostic" in caplog.text


async def test_script_failure_envelope_is_returned(tmp_path) -> None:
    """非 0 退出加合法失败信封时，采用脚本自己的 kind。"""
    version = _install_python(tmp_path, FAILURE)
    db = await _store(tmp_path, ("pkg", version))
    await _grant(db, alias="tools", instance="pkg", available=True, expected_revision=0)
    calls: list[Any] = []
    try:
        result = await _run(db, tmp_path, spawner=_counting(calls))
    finally:
        await db.close()
    _assert_audit(result, ok=False, kind="not_found")
    assert result.data["error"] == "template not found"
    assert result.data["effect_unknown"] is False
    assert len(calls) == 1


async def test_timeout_kills_the_process_during_grace(tmp_path) -> None:
    """超时先发 SIGTERM；宽限内退出时效果不是未知。

    Windows 没有 POSIX 宽限：终止走 ``taskkill /F /T`` 树杀，无法确认子进程
    是否已产生效果，因此 ``effect_unknown`` 为真。
    """
    version = _install_python(tmp_path, SLEEP)
    db = await _store(tmp_path, ("pkg", version))
    await _grant(db, alias="tools", instance="pkg", available=True, expected_revision=0)
    calls: list[Any] = []
    try:
        result = await _run(
            db,
            tmp_path,
            spawner=_counting(calls),
            deadline_ms=200,
            grace_s=1,
        )
    finally:
        await db.close()
    _assert_audit(result, ok=False, kind="timeout")
    expected_unknown = os.name == "nt"  # Windows 树杀无法确认效果。
    assert result.data["effect_unknown"] is expected_unknown
    assert len(calls) == 1
    assert calls[0][0][0] == sys.executable


async def test_invalid_arguments_do_not_spawn(tmp_path) -> None:
    """参数不是对象时，在查找脚本和起进程之前拒绝。"""
    version = _install_python(tmp_path, SUCCESS)
    db = await _store(tmp_path, ("pkg", version))
    await _grant(db, alias="tools", instance="pkg", available=True, expected_revision=0)
    calls: list[Any] = []
    try:
        result = await _run(db, tmp_path, arguments=["csv"], spawner=_forbid(calls))
    finally:
        await db.close()
    _assert_audit(result, ok=False, kind="invalid_input")
    assert calls == []


async def test_script_outside_package_root_is_denied_before_spawn(tmp_path) -> None:
    """登记文件被换成指向包外的链接时拒绝，并且不起进程。"""
    version = _install_python(tmp_path, SUCCESS)
    db = await _store(tmp_path, ("pkg", version))
    await _grant(db, alias="tools", instance="pkg", available=True, expected_revision=0)
    installed = InstalledAbilityProvider(tmp_path / "installed").store.resolve(
        "acme.tools", "1.0.0"
    )
    assert installed is not None
    script = installed.root / "skills" / "guide" / "scripts" / "export.py"
    outside = tmp_path / "outside.py"
    outside.write_text(SUCCESS, encoding="utf-8")
    script.unlink()
    symlink_or_skip(script, outside)
    calls: list[Any] = []
    try:
        linked = await _run(db, tmp_path, spawner=_forbid(calls))
        escaped = await _run(
            db,
            tmp_path,
            script="skills/../../etc/scripts/passwd",
            spawner=_forbid(calls),
        )
    finally:
        await db.close()
    _assert_audit(linked, ok=False, kind="permission_denied")
    _assert_audit(escaped, ok=False, kind="permission_denied")
    assert calls == []


async def test_visible_script_without_exec_grant_is_denied_before_spawn(tmp_path) -> None:
    """整包授予只给脚本可见；没有 available 就不能起进程。"""
    version = _install_python(tmp_path, SUCCESS)
    db = await _store(tmp_path, ("pkg", version))
    await _grant(db, alias="tools", instance="pkg", available=False, expected_revision=0)
    calls: list[Any] = []
    try:
        result = await _run(db, tmp_path, spawner=_forbid(calls))
    finally:
        await db.close()
    _assert_audit(result, ok=False, kind="permission_denied")
    assert "script_exec" in result.data["error"]
    assert calls == []


async def test_non_python_script_is_rejected_before_spawn(tmp_path) -> None:
    """登记路径不是 .py 时返回 unsupported_language，不起进程。"""
    version = _install_shell(tmp_path)
    db = await _store(tmp_path, ("pkg", version))
    await _grant(db, alias="shell", instance="pkg", available=True, expected_revision=0)
    calls: list[Any] = []
    try:
        result = await _run(db, tmp_path, spawner=_forbid(calls))
    finally:
        await db.close()
    _assert_audit(result, ok=False, kind="unsupported_language")
    assert calls == []


async def test_cancel_uses_the_same_termination_path(tmp_path) -> None:
    """取消终止进程树。POSIX 宽限内退出时不把效果标成未知。

    取消与超时共用 :func:`process._terminate`：Windows 上同样走
    ``taskkill /F /T``，``effect_unknown`` 为真。
    """
    version = _install_python(tmp_path, SLEEP)
    db = await _store(tmp_path, ("pkg", version))
    await _grant(db, alias="tools", instance="pkg", available=True, expected_revision=0)
    cancel = asyncio.Event()

    async def trip() -> None:
        await asyncio.sleep(0.05)
        cancel.set()

    calls: list[Any] = []
    trip_task = asyncio.create_task(trip())
    try:
        result = await _run(
            db,
            tmp_path,
            spawner=_counting(calls),
            deadline_ms=30_000,
            grace_s=1,
            cancel=cancel,
        )
    finally:
        await trip_task
        await db.close()
    _assert_audit(result, ok=False, kind="cancelled")
    expected_unknown = os.name == "nt"  # Windows 树杀无法确认效果。
    assert result.data["effect_unknown"] is expected_unknown
    assert len(calls) == 1


async def test_cancelling_the_caller_kills_the_child(tmp_path) -> None:
    """调用任务被取消时先树杀，不把子进程留在后面。"""
    version = _install_python(tmp_path, SLEEP)
    db = await _store(tmp_path, ("pkg", version))
    await _grant(db, alias="tools", instance="pkg", available=True, expected_revision=0)
    seen: list[Any] = []

    async def spy(*args: Any, **kwargs: Any) -> Any:
        proc = await asyncio.create_subprocess_exec(*args, **kwargs)
        seen.append(proc)
        return proc

    task = asyncio.create_task(_run(db, tmp_path, spawner=spy, deadline_ms=30_000, grace_s=1))
    try:
        for _ in range(50):
            if seen:
                break
            await asyncio.sleep(0.02)
        assert seen, "脚本进程没有启动"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(seen[0].wait(), 2)
        assert seen[0].returncode is not None
    finally:
        if not task.done():
            task.cancel()
        await db.close()


async def test_ambiguous_script_address_does_not_spawn(tmp_path) -> None:
    """两个已获授包登记了同一个脚本地址时拒绝，不起进程。"""
    root = tmp_path / "installed"
    first = _install_python(tmp_path, SUCCESS, name="alpha", install_root=root)
    second = _install_python(tmp_path, SUCCESS, name="beta", install_root=root)
    db = await _store(tmp_path, ("pkg-a", first), ("pkg-b", second))
    revision = await _grant(
        db, alias="alpha", instance="pkg-a", available=True, expected_revision=0
    )
    await _grant(db, alias="beta", instance="pkg-b", available=True, expected_revision=revision)
    calls: list[Any] = []
    try:
        result = await _run(db, tmp_path, spawner=_forbid(calls))
    finally:
        await db.close()
    _assert_audit(result, ok=False, kind="invalid_input")
    assert "重名" in result.data["error"]
    assert calls == []


def test_bom_stdout_is_internal() -> None:
    """带 BOM 的 stdout 按内部错误处理，并提示不要用 utf-8-sig。"""
    outcome = process._interpret(b"\xef\xbb\xbf{}", 0, b"")
    assert outcome.ok is False
    assert outcome.kind == "internal"
    assert outcome.effect_unknown is False
    assert "utf-8-sig" in (outcome.error or "")


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX 专有路径：直接测 _terminate_posix 并注入 os.killpg；"
    "Windows 树杀由 test_nt_timeout_and_cancel_use_taskkill 覆盖",
)
async def test_sigkill_after_grace_marks_effect_unknown(monkeypatch) -> None:
    """宽限后仍活着的进程被 SIGKILL，effect_unknown 为真。"""
    signals: list[int] = []

    class Proc:
        def __init__(self) -> None:
            self.pid = 99
            self.returncode: int | None = None
            self.hits = 0

        async def wait(self) -> int | None:
            if self.hits < 2:
                await asyncio.sleep(10)
            self.returncode = -9
            return self.returncode

    proc = Proc()

    def killpg(pid: int, sig: int) -> None:
        assert pid == 99
        signals.append(sig)
        proc.hits += 1
        if sig == signal.SIGKILL:
            proc.returncode = -9

    monkeypatch.setattr(process.os, "killpg", killpg)
    assert await process._terminate_posix(proc, 0.05) is True
    assert signals == [signal.SIGTERM, signal.SIGKILL]


async def test_nt_timeout_and_cancel_use_taskkill(monkeypatch) -> None:
    """模拟 Windows：超时和取消走 taskkill /F /T，效果标为未知，不解析其输出。"""
    target = Path("/tmp/export.py")
    cwd = target.parent
    monkeypatch.setattr(process.os, "name", "nt")
    invoked: list[list[str]] = []

    class Stream:
        def __init__(self) -> None:
            self.released = asyncio.Event()

        def write(self, data: bytes) -> None:
            self.buf = data

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

        async def read(self, _size: int) -> bytes:
            await self.released.wait()
            return b""

    class Proc:
        def __init__(self) -> None:
            self.pid = 4242
            self.returncode: int | None = None
            self.stdin = Stream()
            self.stdout = Stream()
            self.stderr = Stream()
            self.args = ["python", "export.py"]
            self._exit = asyncio.Event()

        def terminate(self) -> None:
            self._finish()

        def _finish(self) -> None:
            self.returncode = 1
            self.stdout.released.set()
            self.stderr.released.set()
            self._exit.set()

        async def wait(self) -> int | None:
            await self._exit.wait()
            return self.returncode

    proc = Proc()

    async def spawn(*_args: Any, **_kwargs: Any) -> Proc:
        return proc

    async def taskkill(argv: list[str]) -> int:
        invoked.append(argv)
        proc._finish()
        return 0

    monkeypatch.setattr(process, "_invoke_taskkill", taskkill)
    timed = await process.run_script_process(
        script_path=target,
        cwd=cwd,
        env={"PYTHONUTF8": "1"},
        envelope=b"{}",
        deadline_s=0.05,
        spawner=spawn,
    )
    assert timed.kind == "timeout"
    assert timed.effect_unknown is True
    assert invoked[0][1:] == ["/F", "/T", "/PID", "4242"]
    assert "已终止" not in (timed.error or "")

    proc2 = Proc()

    async def spawn_again(*_args: Any, **_kwargs: Any) -> Proc:
        return proc2

    async def taskkill_again(argv: list[str]) -> int:
        invoked.append(argv)
        proc2._finish()
        return 0

    monkeypatch.setattr(process, "_invoke_taskkill", taskkill_again)
    cancel = asyncio.Event()
    cancel.set()
    cancelled = await process.run_script_process(
        script_path=target,
        cwd=cwd,
        env={"PYTHONUTF8": "1"},
        envelope=b"{}",
        deadline_s=30,
        spawner=spawn_again,
        cancel=cancel,
    )
    assert cancelled.kind == "cancelled"
    assert cancelled.effect_unknown is True
    assert invoked[-1][1:] == ["/F", "/T", "/PID", "4242"]


async def test_taskkill_discards_localized_output(monkeypatch) -> None:
    """taskkill 的本地化输出不进入结果，只保留退出码。"""
    seen: dict[str, Any] = {}

    class Killer:
        async def wait(self) -> int:
            return 0

    async def spawn(*args: str, **kwargs: Any) -> Killer:
        seen["args"] = args
        seen["stdout"] = kwargs.get("stdout")
        return Killer()

    monkeypatch.setattr(process.asyncio, "create_subprocess_exec", spawn)
    code = await process._invoke_taskkill(["taskkill", "/F", "/T", "/PID", "7"])
    assert code == 0
    assert seen["args"] == ("taskkill", "/F", "/T", "/PID", "7")
    assert seen["stdout"] is asyncio.subprocess.DEVNULL


async def test_windows_selector_loop_fails_at_startup(monkeypatch) -> None:
    """Windows 上的 Selector 循环在调用入口直接失败。"""
    monkeypatch.setattr(process.os, "name", "nt")

    class Proactor:
        pass

    monkeypatch.setattr(process.asyncio, "ProactorEventLoop", Proactor, raising=False)
    with pytest.raises(RuntimeError, match="ProactorEventLoop"):
        process.ensure_script_runtime()
