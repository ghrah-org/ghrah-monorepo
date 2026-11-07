# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""本地获授内容的快照读取、授权变更、运行隔离与 Context 继承边界。"""

from __future__ import annotations

import base64
import shutil
from pathlib import Path

import pytest
from _helpers import make_call as _make_call
from _helpers import make_services
from ghrah.protocol.ability_tree import GrantAbilityRequest, RevokeAbilityRequest, SkillReadScope

from ghrah.ability.access import AbilityAccessStore, AccessError, AccessIdentity
from ghrah.ability.builtin.core.operations.list_abilities import ListAbilitiesAbility
from ghrah.ability.builtin.core.operations.load_skill import LoadSkillAbility
from ghrah.ability.content import ContentReadRequest
from ghrah.ability.local_content import LocalGrantedContentProvider
from ghrah.ability.versions import capture_ability_version
from ghrah.chat.factory import ChatMessageFactory
from ghrah.context.manager import ContextManager
from ghrah.context.rebase import create_rebased_context
from ghrah.types.results import ActionOutcome

ALICE = "a" * 32
BOB = "b" * 32
ADMIN = AccessIdentity("project", "deployment", "control", True, "deployment")
EXAMPLE = Path(__file__).resolve().parents[2] / "examples/ability_content/reporting"


def make_call(**kwargs):
    return _make_call(**{"project_id": "project", "agent": ALICE, "run": "alice-run", **kwargs})


@pytest.fixture
async def content(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(EXAMPLE, source)
    runs = {ALICE: "alice-run", BOB: "bob-run"}
    store = await AbilityAccessStore.open(
        tmp_path / "project.db", "project", operators=frozenset({"deployment"}), runs=runs.get
    )
    version = capture_ability_version((source / "ability.yaml").read_text(), root=source)
    try:
        await store.commit_version(ADMIN, version)
        await store.select_instance(ADMIN, "reporting", version.digest, tmp_path)
        for agent in runs:
            await store.register_agent(ADMIN, agent)
            await store.grant(
                ADMIN,
                agent,
                GrantAbilityRequest(
                    alias="reports",
                    instance_id="reporting",
                    whole_container=True,
                    expected_revision=0,
                ),
            )
        provider = LocalGrantedContentProvider(store, ALICE)
        provider.bind_run(runs[ALICE])
        cm = ContextManager(ALICE, message_factory=ChatMessageFactory(), ability_content=provider)
        yield store, provider, cm, source, runs
    finally:
        await store.close()


async def test_catalog_document_attachment_and_context_use_one_grant(content):
    store, provider, cm, _, _ = content
    messages = await cm.get_llm_messages()
    assert "reports/guide" in messages[-1].text
    assert "# 报告编写" not in messages[-1].text
    before = await store.view(ADMIN, ALICE)
    loaded = await provider.load_skill("reports/guide")
    assert "# 报告编写" in loaded["content"]
    assert loaded["uri"] == "ability:reports/skill/guide"
    assert not Path(loaded["path"]).is_absolute()
    attachment = await provider.load_skill("reports/guide", path="references/style.md")
    assert "短段落" in attachment["content"]
    await provider.load_into_context(cm, "reports", "skill/guide")
    await provider.load_into_context(cm, "reports", "skill/guide/references/style")
    messages = await cm.get_llm_messages()
    assert any("# 报告编写" in item.text for item in messages)
    assert any("短段落" in item.text for item in messages)
    assert await store.view(ADMIN, ALICE) == before  # 读取不授予、不绑定工具。


async def test_source_changes_and_deletion_do_not_change_committed_bytes(content):
    _, provider, _, source, _ = content
    original = await provider.load_skill("reports/guide")
    (source / "skills/guide/SKILL.md").write_text("changed", encoding="utf-8")
    assert await provider.load_skill("reports/guide") == original
    shutil.rmtree(source)
    assert await provider.load_skill("reports/guide") == original
    assert (
        "短段落"
        in (await provider.read_relative("reports", "guide", "references/style.md")).content
    )


async def test_attachment_scope_and_discovery_are_current(content):
    store, provider, _, _, _ = content
    old = await store.resolve(provider._identity, "reports", "skill/guide/references/style")
    revision = await store.agent_revision(ADMIN, ALICE)
    await store.grant(
        ADMIN,
        ALICE,
        GrantAbilityRequest(
            alias="reports",
            instance_id="reporting",
            skill=SkillReadScope(key="guide", assets=["template"], references=[], scripts=[]),
            expected_revision=revision,
        ),
    )
    services = make_services(ability_content=provider)
    listed = await ListAbilitiesAbility().execute(make_call(), services)
    assert listed.outcome == ActionOutcome.SUCCESS
    assert listed.data["abilities"] == []
    assert [row["full_name"] for row in listed.data["skills"]] == ["reports/guide"]
    detail = await ListAbilitiesAbility().execute(
        make_call(arguments={"full_name": "reports/guide"}), services
    )
    assert detail.data["references"] == []
    assert detail.data["scripts"] == []
    assert [entry["key"] for entry in detail.data["assets"]] == ["template"]
    with pytest.raises(AccessError):
        await provider.read_relative("reports", "guide", "references/style.md")
    with pytest.raises(AccessError):
        await store.read_content(provider._identity, old, ContentReadRequest())
    denied = await LoadSkillAbility().execute(
        make_call(arguments={"full_name": "reports/guide", "path": "references/style.md"}), services
    )
    assert denied.outcome == ActionOutcome.FAILURE
    hidden = await ListAbilitiesAbility().execute(
        make_call(arguments={"full_name": "ghrah.fs.read_file"}), services
    )
    assert hidden.outcome == ActionOutcome.FAILURE


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "/etc/passwd",
        "references/../style.md",
        "x\\y",
        "file:x",
        "",
        "references/missing.md",
    ],
)
async def test_relative_paths_cannot_escape_granted_members(content, path):
    _, provider, _, _, _ = content
    with pytest.raises((ValueError, AccessError)):
        await provider.load_skill("reports/guide", path=path)


async def test_binary_is_explicit_and_not_inserted_as_text(content):
    _, provider, cm, _, _ = content
    result = await provider.read("reports", "skill/guide/assets/mark")
    assert result.encoding == "base64"
    assert base64.b64decode(result.content) == bytes([0, 255, 16, 128])
    before = list(cm.message_store.current_messages)
    with pytest.raises(ValueError, match="有界文本"):
        await provider.load_into_context(cm, "reports", "skill/guide/assets/mark")
    assert cm.message_store.current_messages == before


async def test_full_text_crosses_utf8_chunk_boundaries_and_enforces_total_limit(content):
    store, provider, cm, _, _ = content
    expected = await provider.load_skill("reports/guide")
    store.read_limit = 3
    assert await provider.load_skill("reports/guide") == expected
    with pytest.raises(ValueError, match="有界文本"):
        await provider.load_into_context(cm, "reports", "skill/guide", max_bytes=1)


async def test_revocation_during_multichunk_read_never_inserts_partial_text(content, monkeypatch):
    store, provider, cm, _, _ = content
    original = store.read_chunk
    store.read_limit = 3
    calls = 0

    async def read_then_revoke(*args, **kwargs):
        nonlocal calls
        chunk = await original(*args, **kwargs)
        calls += 1
        if calls == 1:
            await store.revoke(
                ADMIN,
                ALICE,
                RevokeAbilityRequest(
                    alias="reports", expected_revision=await store.agent_revision(ADMIN, ALICE)
                ),
            )
        return chunk

    monkeypatch.setattr(store, "read_chunk", read_then_revoke)
    before = list(cm.message_store.current_messages)
    with pytest.raises(AccessError):
        await provider.load_into_context(cm, "reports", "skill/guide")
    assert cm.message_store.current_messages == before
    assert "reports/guide" not in await provider.catalog_text()


async def test_run_change_permanently_invalidates_old_service_and_context(content):
    store, provider, cm, _, runs = content
    runs[ALICE] = "new-run"
    with pytest.raises(AccessError):
        await provider.load_skill("reports/guide")
    with pytest.raises(ValueError):
        await cm.get_llm_messages()
    with pytest.raises(ValueError):
        provider.bind_run("new-run")
    replacement = LocalGrantedContentProvider(store, ALICE)
    replacement.bind_run("new-run")
    assert "# 报告编写" in (await replacement.load_skill("reports/guide"))["content"]
    runs.pop(ALICE)
    with pytest.raises(AccessError):
        await replacement.load_skill("reports/guide")


async def test_version_switch_invalidates_old_fence(content):
    store, provider, _, source, _ = content
    old = await store.resolve(provider._identity, "reports", "skill/guide")
    body = source / "skills/guide/SKILL.md"
    body.write_text(body.read_text() + "\nnew version", encoding="utf-8")
    version = capture_ability_version((source / "ability.yaml").read_text(), root=source)
    await store.commit_version(ADMIN, version)
    instance = await store.instance("reporting")
    await store.select_instance(
        ADMIN, "reporting", version.digest, source.parent, expected_revision=instance["revision"]
    )
    with pytest.raises(AccessError):
        await store.read_content(provider._identity, old, ContentReadRequest())
    with pytest.raises(AccessError):
        await provider.load_skill("reports/guide")
    await store.grant(
        ADMIN,
        ALICE,
        GrantAbilityRequest(
            alias="reports",
            instance_id="reporting",
            whole_container=True,
            expected_revision=await store.agent_revision(ADMIN, ALICE),
        ),
    )
    assert "new version" in (await provider.load_skill("reports/guide"))["content"]
    with pytest.raises(AccessError):
        await store.read_content(provider._identity, old, ContentReadRequest())


async def test_rebase_requires_explicit_service_for_target_identity_and_run(content):
    store, provider, cm, _, runs = content
    child = create_rebased_context(cm, agent_name=BOB)
    assert child.ability_content is None
    with pytest.raises(ValueError):
        create_rebased_context(cm, agent_name=BOB, ability_content=provider)
    with pytest.raises(ValueError):
        ContextManager(BOB, ability_content=provider)
    own = LocalGrantedContentProvider(store, BOB)
    own.bind_run(runs[BOB])
    child = create_rebased_context(cm, agent_name=BOB, ability_content=own)
    assert child.ability_content is own
    same = create_rebased_context(cm, agent_name=ALICE, ability_content=provider)
    assert same.ability_content is provider
    runs[ALICE] = "new-run"
    with pytest.raises(ValueError):
        create_rebased_context(cm, agent_name=ALICE, ability_content=provider)


@pytest.mark.parametrize("operation", [LoadSkillAbility, ListAbilitiesAbility])
async def test_operations_require_granted_content_provider(operation):
    result = await operation().execute(
        make_call(arguments={"full_name": "reports/guide"}), make_services()
    )
    assert result.outcome == ActionOutcome.FAILURE
    assert "not wired" in result.data["error"]


@pytest.mark.parametrize(
    "identity", [{"project_id": "another"}, {"agent": BOB}, {"run": "new-run"}]
)
@pytest.mark.parametrize("operation", [LoadSkillAbility, ListAbilitiesAbility])
async def test_wrong_project_agent_or_run_cannot_use_content_service(content, identity, operation):
    _, provider, _, _, _ = content
    result = await operation().execute(
        make_call(arguments={"full_name": "reports/guide"}, **identity),
        make_services(ability_content=provider),
    )
    assert result.outcome == ActionOutcome.FAILURE
