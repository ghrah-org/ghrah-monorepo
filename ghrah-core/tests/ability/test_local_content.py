# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""本地获授内容的快照读取、授权变更、运行隔离与 Context 继承边界。"""

from __future__ import annotations

import base64
import shutil
from pathlib import Path

import pytest
import yaml
from _helpers import make_call as _make_call
from _helpers import make_services
from ghrah.protocol.ability_tree import GrantAbilityRequest, RevokeAbilityRequest, SkillReadScope

from ghrah.ability.access import AbilityAccessStore, AccessError, AccessIdentity
from ghrah.ability.builtin.core.operations.list_abilities import ListAbilitiesAbility
from ghrah.ability.builtin.core.operations.load_skill import LoadSkillAbility
from ghrah.ability.content import (
    ContentChunk,
    ContentDescription,
    ContentReadRequest,
    is_loadable_text,
)
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
    # 目录不再由 ContextManager 注入（改由工具面按绑定轴注入）；内容端口仍须
    # 用同一份授予给出"只有摘要、没有正文"的目录。
    catalog = await provider.catalog_text()
    assert "reports/guide" in catalog
    assert "# 报告编写" not in catalog
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


async def test_load_skill_accepts_a_bare_skill_key(content):
    """模型常把目录名里的技能键当名字：唯一命中时直接可用。"""
    _, provider, _, _, _ = content

    loaded = await provider.load_skill("guide")
    assert loaded["full_name"] == "reports/guide"
    assert "# 报告编写" in loaded["content"]

    attachment = await provider.load_skill("guide", path="references/style.md")
    assert "短段落" in attachment["content"]


async def test_unknown_skill_key_lists_available_names(content):
    _, provider, _, _, _ = content

    with pytest.raises(ValueError, match="reports/guide"):
        await provider.load_skill("ghost")


async def test_load_skill_by_catalog_address(content):
    """目录条目附带的 read 地址可直接加载，等价于 alias/skill。"""
    _, provider, _, _, _ = content
    address = "reporting/reporting/0.1.0/skills/guide/SKILL.md"

    loaded = await provider.load_skill(address)
    assert loaded["full_name"] == "reports/guide"
    assert "# 报告编写" in loaded["content"]
    assert loaded["uri"] == "ability:reports/skill/guide"

    attachment = await provider.load_skill(address, path="references/style.md")
    assert "短段落" in attachment["content"]


async def test_load_skill_by_foreign_address_is_denied(content):
    """地址反查只在已授予的实例里做：不存在/版本不符都按 not_found 拒绝。"""
    _, provider, _, _, _ = content

    with pytest.raises(AccessError):
        await provider.load_skill("reporting/reporting/9.9.9/skills/guide/SKILL.md")
    with pytest.raises(AccessError):
        await provider.load_skill("other/other/0.1.0/skills/guide/SKILL.md")


async def test_source_changes_and_deletion_do_not_change_committed_bytes(content):
    _, provider, _, source, _ = content
    original = await provider.load_skill("reports/guide")
    (source / "skills/guide/SKILL.md").write_text("changed", encoding="utf-8", newline="\n")
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


@pytest.mark.parametrize(
    ("media_type", "allowed"),
    [
        ("text/markdown", True),
        ("application/json", True),
        ("Application/JSON; charset=utf-8", True),
        ("application/yaml", True),
        ("application/octet-stream", False),
        ("image/png", False),
    ],
)
def test_is_loadable_text_allows_structured_text(media_type, allowed):
    assert is_loadable_text(media_type) is allowed


def _chunk(media_type: str, data: bytes) -> ContentChunk:
    return ContentChunk(
        description=ContentDescription(
            instance_id="reporting",
            version_digest="version",
            member="skill/guide/assets/example",
            path="skills/guide/assets/example.json",
            media_type=media_type,
            digest="sha256:example",
            total_bytes=len(data),
        ),
        offset=0,
        data=data,
        next_offset=None,
    )


def test_json_chunk_is_utf8_and_binary_stays_base64():
    text = _chunk("application/json", b'{"slot":"length"}\n')
    result = text.to_json_result()
    assert result.encoding == "utf-8"
    assert result.content == '{"slot":"length"}\n'
    binary = _chunk("application/octet-stream", bytes([0, 255]))
    encoded = binary.to_json_result()
    assert encoded.encoding == "base64"
    assert base64.b64decode(encoded.content) == bytes([0, 255])


async def test_json_asset_loads_as_text_and_binary_stays_rejected(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(EXAMPLE, source)
    payload = '{"slot":"length"}\n'
    (source / "skills/guide/assets/example.json").write_text(
        payload, encoding="utf-8", newline="\n"
    )
    definition_path = source / "ability.yaml"
    data = yaml.safe_load(definition_path.read_text(encoding="utf-8"))
    guide = next(item for item in data["members"] if item["member"].endswith(".guide"))["data"]
    assert "mark" in guide["assets"]
    guide["assets"]["example"] = {
        "path": "skills/guide/assets/example.json",
        "media_type": "application/json",
    }
    definition_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
        newline="\n",
    )
    runs = {ALICE: "alice-run"}
    store = await AbilityAccessStore.open(
        tmp_path / "project.db",
        "project",
        operators=frozenset({"deployment"}),
        runs=runs.get,
    )
    try:
        version = capture_ability_version(definition_path.read_text(encoding="utf-8"), root=source)
        await store.commit_version(ADMIN, version)
        await store.select_instance(ADMIN, "reporting", version.digest, tmp_path)
        await store.register_agent(ADMIN, ALICE)
        await store.grant(
            ADMIN,
            ALICE,
            GrantAbilityRequest(
                alias="reports",
                instance_id="reporting",
                whole_container=True,
                expected_revision=0,
            ),
        )
        provider = LocalGrantedContentProvider(store, ALICE)
        provider.bind_run(runs[ALICE])
        loaded = await provider.load_skill("reports/guide", path="assets/example.json")
        assert loaded["content"] == payload
        assert loaded["media_type"] == "application/json"
        with pytest.raises(ValueError, match="有界文本"):
            await provider.load_skill("reports/guide", path="assets/mark.bin")
    finally:
        await store.close()


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
    body.write_text(body.read_text() + "\nnew version", encoding="utf-8", newline="\n")
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
