# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""能力接纳拒绝文案：区分「未授予 / 版本已变 / 工具失效 / 参数越界」。

此前 ``access_denied`` 用同一句话覆盖四类原因，排障时无法分辨「权限被撤」
与「路径越界」；这里锁定按进程内 reason 细分的文案，并要求不泄露内部修订号。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _helpers import make_call, make_services
from ghrah.protocol.ability_tree import RevokeAbilityRequest

from ghrah.ability.access import (
    AbilityAccessStore,
    AccessIdentity,
    GrantPolicy,
    StartupGrantMaterializer,
)
from ghrah.ability.access.errors import AccessError
from ghrah.ability.access.startup import StartupToolBinding, _denial
from ghrah.ability.assembly import AbilityAssembly
from ghrah.manifest.parser import parse_agent_manifest

AGENT_YAML = """\
manifest: agent
version: "2"
metadata:
  namespace: w1
  name: consumer
  description: denial messages
model:
  endpoint_name: default
system_prompt: Use the selected tools.
max_iterations: 5
abilities:
  - type: conversation
  - ref: ghrah.fs.read_file
"""

PROJECT = "project"
AGENT = "a" * 32
DEPLOY = AccessIdentity(PROJECT, "deployment", "control", True, "deployment")


@pytest.fixture
async def store(tmp_path: Path):
    db = await AbilityAccessStore.open(
        tmp_path / "project.db",
        PROJECT,
        operators=frozenset({"deployment"}),
        runs=lambda agent: f"{agent}-run",
    )
    try:
        yield db
    finally:
        await db.close()


def _resolved(text: str):
    manifest = parse_agent_manifest(text)
    assembly = AbilityAssembly.select("builtin")
    return assembly, assembly.source.resolver().resolve(manifest)


def _materializer(db, assembly, workspace: Path, **kwargs):
    class RegisteredMaterializer(StartupGrantMaterializer):
        async def materialize(self, **kwargs):
            result = await super().materialize(**kwargs)
            await self.bind_run(kwargs["agent"], result)
            return result

    return RegisteredMaterializer(
        store=db,
        source=assembly.source,
        actor=DEPLOY,
        policy=GrantPolicy(),
        workspace_root=str(workspace),
        **kwargs,
    )


def _binding() -> StartupToolBinding:
    return StartupToolBinding("fs", "read_file", "read_file")


def test_denial_messages_are_distinct_and_do_not_leak_revisions() -> None:
    """四类 reason 文案互不相同，且都不出现修订号等内部事实。"""
    binding = _binding()
    messages = {
        reason: _denial(binding, "access_denied", reason)
        for reason in (
            "version_changed",
            "tool_revoked",
            "grant_revoked",
            "arguments_out_of_scope",
        )
    }
    assert len(set(messages.values())) == 3
    # 工具行缺失与授予行缺失同属「失效」一类，文案一致；其余三类互不相同。
    assert messages["tool_revoked"] == messages["grant_revoked"]
    fallback = _denial(binding, "access_denied")
    assert fallback not in messages.values()
    for text in (*messages.values(), fallback):
        assert "read_file" in text
        assert "revision" not in text.lower()


async def test_revoked_grant_denial_names_invalidation(store, tmp_path):
    """整条授予被撤销后，文案应说「已被撤销或失效」而不是笼统的越界。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "note.txt"
    target.write_text("x", encoding="utf-8")
    assembly, resolved = _resolved(AGENT_YAML)
    result = await _materializer(store, assembly, workspace).materialize(
        agent=AGENT, resolved=resolved
    )
    read_file = next(item for item in result.abilities if item.name == "read_file")
    revision = await store.agent_revision(DEPLOY, AGENT)
    await store.revoke(DEPLOY, AGENT, RevokeAbilityRequest(alias="fs", expected_revision=revision))

    outcome = await read_file.execute(
        make_call(arguments={"file_path": str(target)}), make_services()
    )

    assert outcome.outcome.value == "failure"
    assert "revoked or invalidated" in outcome.data["error"]


async def test_out_of_scope_argument_denial_names_roots(store, tmp_path):
    """路径越界的文案应指向授予根，而不是让人误以为权限被撤。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    assembly, resolved = _resolved(AGENT_YAML)
    result = await _materializer(store, assembly, workspace).materialize(
        agent=AGENT, resolved=resolved
    )
    read_file = next(item for item in result.abilities if item.name == "read_file")

    outcome = await read_file.execute(
        make_call(arguments={"file_path": str(outside)}), make_services()
    )

    assert outcome.outcome.value == "failure"
    assert "outside its granted roots" in outcome.data["error"]


async def test_stale_version_denial_names_restart(store, tmp_path, monkeypatch):
    """接纳依据的版本摘要漂移时，文案应提示重启 Agent 重新物化工具。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "note.txt"
    target.write_text("x", encoding="utf-8")
    assembly, resolved = _resolved(AGENT_YAML)
    result = await _materializer(store, assembly, workspace).materialize(
        agent=AGENT, resolved=resolved
    )
    read_file = next(item for item in result.abilities if item.name == "read_file")

    async def stale_admit(*args, **kwargs):
        return "sha256:another-version:1:1"

    monkeypatch.setattr(store, "admit_tool", stale_admit)

    outcome = await read_file.execute(
        make_call(arguments={"file_path": str(target)}), make_services()
    )

    assert outcome.outcome.value == "failure"
    assert "earlier version" in outcome.data["error"]


def test_access_error_reason_is_optional() -> None:
    """新增的 reason 不改变协议错误码，也不影响现有调用方。"""
    plain = AccessError("access_denied")
    assert plain.code == "access_denied"
    assert plain.reason is None
    tagged = AccessError("access_denied", "tool_revoked")
    assert tagged.code == "access_denied"
    assert tagged.reason == "tool_revoked"
