# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""旧 YAML 模板库导入脚本的测试（脚本在 ``scripts/``，按文件路径载入）。"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from ghrah.subject.template_store import TemplateStore

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "migrate_agent_templates.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migrate_agent_templates", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migrate = _load()

_V2 = """
manifest: agent
version: "2"
metadata:
  namespace: writing
  name: weekly
  title: 周报
model:
  endpoint_name: default-llm
  temperature: 0.3
system_prompt: 先列事实
max_iterations: 12
abilities:
  - type: conversation
  - type: load_skill
  - ref: demo.tools.read_file
  - ref: demo.pkg.missing_member
"""

_V1 = """
manifest: agent
version: "1"
metadata:
  namespace: writing
  name: legacy
model:
  agent_config_name: glm-5.3
system_prompt: hi
hooks:
  before_action: [demo.hook]
"""

_ABILITY = (
    """
format: ghrah.ability-definition
schema_version: '1'
identity:
  namespace: demo
  name: tools
members:
  operations:
    read_file: {}
    write_file: {}
"""
    + """
format: ghrah.ability-definition
schema_version: '1'
identity:
  namespace: ghrah
  name: core
members:
  operations:
    load_skill: {}
    conversation: {}
    end_task: {}
"""
)


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path]:
    """构造 source 目录、ability 根与目标 db 路径。"""
    agents = tmp_path / "manifests" / "agents" / "writing"
    agents.mkdir(parents=True)
    (agents / "weekly.yaml").write_text(_V2, encoding="utf-8", newline="\n")
    (agents / "legacy.yaml").write_text(_V1, encoding="utf-8", newline="\n")
    root = tmp_path / "abilities"
    (root / "demo.tools").mkdir(parents=True)
    (root / "demo.tools" / "ability.yaml").write_text(
        _ABILITY.split("format: ghrah.ability-definition")[1].join(
            ["format: ghrah.ability-definition", ""]
        ),
        encoding="utf-8",
        newline="\n",
    )
    core = root / "ghrah.core"
    core.mkdir()
    (core / "ability.yaml").write_text(
        "format: ghrah.ability-definition\n"
        "schema_version: '1'\n"
        "identity:\n  namespace: ghrah\n  name: core\n"
        "members:\n  operations:\n    load_skill: {}\n    conversation: {}\n    end_task: {}\n",
        encoding="utf-8",
        newline="\n",
    )
    return tmp_path / "manifests" / "agents", root


def test_builds_config_and_reports_gaps(workspace: tuple[Path, Path]) -> None:
    source, root = workspace
    index = migrate.load_ability_index([root])
    report = migrate.Report()
    document = migrate.yaml.safe_load((source / "writing" / "weekly.yaml").read_text("utf-8"))
    config = migrate.build_config(
        document, path=source / "writing" / "weekly.yaml", index=index, report=report
    )

    assert config.metadata.namespace == "writing"
    assert config.metadata.title == "周报"
    assert config.model.endpoint_name == "default-llm"
    assert config.model.model_overrides == {"temperature": 0.3}
    assert config.prompt.system_prompt == "先列事实"
    assert config.context_policy.max_iterations == 12
    # type=load_skill → ghrah.core 的单个操作；控制成员 conversation 不入申请
    core_members = [item for item in config.ability_request.members if item.package == "ghrah.core"]
    assert [item.key for item in core_members] == ["load_skill"]
    # ref 指向成员 → 拆成包 + 操作
    tool_members = [item for item in config.ability_request.members if item.package == "demo.tools"]
    assert [item.key for item in tool_members] == ["read_file"]
    # 解析不到的成员进报告，不静默丢弃
    assert [item["ref"] for item in report.unresolved_members] == ["demo.pkg.missing_member"]


def test_v1_endpoint_name_is_normalised(workspace: tuple[Path, Path]) -> None:
    source, root = workspace
    index = migrate.load_ability_index([root])
    report = migrate.Report()
    document = migrate.yaml.safe_load((source / "writing" / "legacy.yaml").read_text("utf-8"))
    config = migrate.build_config(document, path=source, index=index, report=report)

    assert config.model.endpoint_name == "glm-5.3"
    assert report.assumed_v1, "v1 归一必须留痕"
    assert report.dropped_hooks, "hooks 必须进报告"


def test_dry_run_writes_nothing(workspace: tuple[Path, Path], tmp_path: Path) -> None:
    source, root = workspace
    db = tmp_path / "subject.db"
    code = migrate.main(["--source", str(source), "--db", str(db), "--ability-root", str(root)])
    assert code == 0
    assert not db.exists()


def test_apply_is_idempotent_and_can_bump_revision(
    workspace: tuple[Path, Path], tmp_path: Path
) -> None:
    source, root = workspace
    db = tmp_path / "subject.db"
    args = ["--source", str(source), "--db", str(db), "--ability-root", str(root), "--apply"]

    assert migrate.main(args) == 0
    created = asyncio.run(_latest(db, "writing", "weekly"))
    assert created is not None
    assert created.revision == 1
    # 再跑一次：应为 skip，不产生新版本
    assert migrate.main(args) == 0
    assert len(asyncio.run(_versions(db, created.template_id))) == 1

    # --on-conflict=update → 追加版本
    assert migrate.main([*args, "--on-conflict=update"]) == 0
    latest = asyncio.run(_latest(db, "writing", "weekly"))
    assert latest is not None
    assert latest.revision == 2
    assert latest.latest is True


async def _latest(db: Path, namespace: str, name: str):
    """按身份取最新版本（每次独立连接，规避事件循环复用）。"""
    store = TemplateStore(db)
    await store.start()
    try:
        return await store.get_by_identity(namespace, name)
    finally:
        await store.stop()


async def _versions(db: Path, template_id: str):
    """取某模板的全部版本。"""
    store = TemplateStore(db)
    await store.start()
    try:
        return await store.versions(template_id)
    finally:
        await store.stop()


def test_strict_fails_on_unresolved(workspace: tuple[Path, Path], tmp_path: Path) -> None:
    source, root = workspace
    code = migrate.main(
        [
            "--source",
            str(source),
            "--db",
            str(tmp_path / "subject.db"),
            "--ability-root",
            str(root),
            "--strict",
        ]
    )
    assert code == migrate._EXIT_STRICT


def test_refusal_when_source_missing(tmp_path: Path) -> None:
    code = migrate.main(["--source", str(tmp_path / "missing"), "--db", str(tmp_path / "db")])
    assert code == migrate._EXIT_REFUSED


def test_report_is_json_serialisable(workspace: tuple[Path, Path], tmp_path: Path, capsys) -> None:
    source, root = workspace
    report_path = tmp_path / "report.json"
    migrate.main(
        [
            "--source",
            str(source),
            "--db",
            str(tmp_path / "subject.db"),
            "--ability-root",
            str(root),
            "--report",
            str(report_path),
        ]
    )
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["applied"] is False
    assert "writing.weekly" in payload["imported"]
    assert payload["unresolved_total"] >= 1
    assert "unresolved_total" in capsys.readouterr().out
