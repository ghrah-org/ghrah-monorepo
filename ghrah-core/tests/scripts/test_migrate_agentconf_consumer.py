# SPDX-License-Identifier: Apache-2.0
"""离线转换、逐字段来源、不可覆盖原件和失败时零写入。"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/migrate_agentconf_consumer.py"
spec = importlib.util.spec_from_file_location("consumer_migration", SCRIPT)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
FIXTURES = ROOT / "ghrah-core/tests/llm/fixtures"


def exported():
    return json.loads((FIXTURES / "agent_export_v1.json").read_text())


def signed(data):
    data = copy.deepcopy(data)
    data.pop("checksum", None)
    data["checksum"] = hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    return data


def test_multiple_agents_one_endpoint_preserves_own_parameters_and_sources():
    old = {
        "project_id": "p",
        "name": "project",
        "agents": [
            {
                "name": "worker0",
                "agent_config_name": "example-concise",
                "model_overrides": {
                    "temperature": None,
                    "max_tokens": 10,
                    "top_p": 0,
                    "extra_params": {"vendor": False},
                },
            },
            {"name": "example-creative"},
        ],
    }
    before = copy.deepcopy(old)
    result, report = migration.convert_config(old, exported())
    a, b = result["agents"]
    assert old == before
    assert a["endpoint_name"] == b["endpoint_name"] == "chat"
    assert a["model_overrides"] == {
        "temperature": 0,
        "max_tokens": 10,
        "top_p": 0,
        "top_k": 0,
        "extra_params": {"stream": False, "stop": None, "vendor": False},
    }
    assert b["model_overrides"]["temperature"] == 0.8
    assert b["model_overrides"]["extra_params"] == {"stream": True}
    sources = {r["path"]: r["source"] for r in report}
    assert sources["$.agents[0].model_overrides.temperature"] == "agent_export"
    assert sources["$.agents[0].model_overrides.top_p"] == "ghrah"
    assert sources["$.agents[0].model_overrides.extra_params.vendor"] == "ghrah"
    assert sources["$.agents[1].endpoint_name"] == "agent_export:example-creative"


def test_manifest_major_two_and_explicit_zero_win():
    old = {
        "manifest": "agent",
        "version": "1.4",
        "metadata": {"namespace": "test", "name": "worker"},
        "model": {
            "agent_config_name": "example-creative",
            "temperature": 0,
            "extra_params": {"temperature": 1},
        },
        "system_prompt": "worker",
        "abilities": [{"type": "conversation"}],
    }
    result, report = migration.convert_config(old, exported())
    assert result["version"] == "2"
    assert result["model"]["temperature"] == 0
    assert "temperature" not in result["model"]["extra_params"]
    assert report


@pytest.mark.parametrize(
    "config,match",
    [
        ({"name": "missing"}, "no exported mapping"),
        (
            {"name": "worker", "agent_config_name": "example-concise", "endpoint_name": "chat"},
            "conflict",
        ),
        (
            {
                "name": "worker",
                "agent_config_name": "example-concise",
                "model_overrides": {"unknown": 1},
            },
            "unknown model",
        ),
        (
            {
                "name": "worker",
                "agent_config_name": "example-concise",
                "model_overrides": {"extra_params": {"model": "evil"}},
            },
            "identity",
        ),
    ],
)
def test_ambiguous_or_unsafe_config_is_rejected(config, match):
    with pytest.raises(ValueError, match=match):
        migration.convert_config(config, exported())


@pytest.mark.parametrize(
    "mutation", ["checksum", "duplicate", "missing_binding", "conflicting_binding", "version"]
)
def test_invalid_export_stops_conversion(mutation):
    data = exported()
    if mutation == "checksum":
        data["checksum"] = "invalid"
    elif mutation == "duplicate":
        data["agents"].append(data["agents"][0])
    elif mutation == "missing_binding":
        data["agents"][0]["endpoint_id"] = ""
    elif mutation == "conflicting_binding":
        data["agents"][1]["model_name"] = "different"
    else:
        data["export_version"] = 99
    if mutation != "checksum":
        data = signed(data)
    with pytest.raises(migration.MigrationError):
        migration.convert_config({"name": "example-concise"}, data)


def test_unknown_export_extensions_are_preserved():
    data = exported()
    data["agents"][0]["request_params"]["new_vendor_flag"] = {"enabled": False}
    result, _ = migration.convert_config({"name": "example-concise"}, signed(data))
    assert result["model_overrides"]["extra_params"]["new_vendor_flag"] == {"enabled": False}


def test_spawn_wire_and_manifest_references():
    result, _ = migration.convert_config(
        {"project_id": "p", "config": {"name": "example-concise"}}, exported()
    )
    assert result["config"]["endpoint_name"] == "chat"
    with pytest.raises(migration.MigrationError, match="No legacy"):
        migration.convert_config(
            {"agents": [{"name": "x", "manifest_ref": "test.worker"}]}, exported()
        )


def test_cli_preview_apply_and_refused_overwrite(tmp_path):
    source = tmp_path / "old.yaml"
    source.write_text("name: example-concise\n")
    output = tmp_path / "new.json"
    cmd = [
        sys.executable,
        str(SCRIPT),
        str(source),
        "--agent-export",
        str(FIXTURES / "agent_export_v1.json"),
        "--output",
        str(output),
    ]
    preview = subprocess.run(cmd, capture_output=True, text=True)
    assert preview.returncode == 0, preview.stderr
    assert "field_sources" in preview.stdout and "endpoint_name" in preview.stdout
    assert not output.exists()
    applied = subprocess.run([*cmd, "--apply"], capture_output=True, text=True)
    assert applied.returncode == 0, applied.stderr
    assert json.loads(output.read_text())["endpoint_name"] == "chat"
    assert source.read_text() == "name: example-concise\n"
    if os.name != "nt":
        assert output.stat().st_mode & 0o777 == 0o600
    assert subprocess.run([*cmd, "--apply"], capture_output=True).returncode == 2
    same = subprocess.run([*cmd[:-1], str(source), "--apply"], capture_output=True, text=True)
    assert same.returncode == 2 and "distinct" in same.stderr
    assert source.read_text() == "name: example-concise\n"


def test_failure_never_creates_output(tmp_path):
    source = tmp_path / "old.json"
    source.write_text('{"name":"missing"}')
    output = tmp_path / "new.json"
    failed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(source),
            "--agent-export",
            str(FIXTURES / "agent_export_v1.json"),
            "--output",
            str(output),
            "--apply",
        ],
        capture_output=True,
    )
    assert failed.returncode == 2
    assert not output.exists()


def test_reviewable_example_matches_converter_output_and_wire_schema():
    from ghrah.protocol.payloads.project import ProjectInfoPayload

    example = ROOT / "ghrah-core/examples/agentconf_migration"
    new, report = migration.convert_config(
        json.loads((example / "legacy.json").read_text()),
        json.loads((example / "agent-export.json").read_text()),
    )
    assert new == json.loads((example / "converted.json").read_text())
    assert report == json.loads((example / "field-sources.json").read_text())
    ProjectInfoPayload.model_validate(new)


def test_none_overrides_are_unspecified_and_false_containers_are_rejected():
    new, _ = migration.convert_config(
        {"name": "example-concise", "model_overrides": {"extra_params": None}}, exported()
    )
    assert new["model_overrides"]["extra_params"] == {"stream": False, "stop": None}
    for overrides in [False, {"extra_params": False}]:
        with pytest.raises(ValueError):
            migration.convert_config(
                {"name": "example-concise", "model_overrides": overrides}, exported()
            )
    with pytest.raises(migration.MigrationError, match="object"):
        migration.load_export([])
