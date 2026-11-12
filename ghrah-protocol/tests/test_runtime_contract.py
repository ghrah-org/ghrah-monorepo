"""Runtime 最小 wire 契约的输入严格性、输出扩展与生成一致性。"""

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghrah.protocol.runtime import RuntimePrincipal, RuntimeSystemStatus


def test_principal_rejects_model_supplied_unknown_identity_fields():
    with pytest.raises(ValidationError):
        RuntimePrincipal.model_validate({"scope_ref": "s", "agent_id": "forged"})


def test_status_fixture_and_additive_fields():
    root = Path(__file__).resolve().parents[2]
    body = json.loads((root / "ghrah-protocol/contracts/runtime/status.ready.json").read_text())
    body["future_field"] = True
    assert RuntimeSystemStatus.model_validate(body).ready


def test_generated_runtime_contracts_are_current():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "export_runtime", root / "scripts/export_runtime_contracts.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for path, content in module.artifacts().items():
        assert path.read_text() == content, path


def test_command_envelope_reuses_bounded_command_set_and_generated_fixture():
    from ghrah.protocol.runtime import RuntimeCommandRequest
    from ghrah.protocol.types import ListAgentsPayload

    root = Path(__file__).resolve().parents[2]
    request = RuntimeCommandRequest.model_validate_json(
        (root / "ghrah-protocol/contracts/runtime/command.list.json").read_text()
    )
    assert request.command == "list_agents"
    assert ListAgentsPayload.model_validate(request.payload).project_id == "example"
    with pytest.raises(ValueError):
        RuntimeCommandRequest(command="execute_arbitrary", payload={})


def test_generator_rejects_constraints_it_cannot_represent(monkeypatch):
    from pydantic import BaseModel, Field

    class EvenNumber(BaseModel):
        value: int = Field(multiple_of=2)

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "export_runtime", root / "scripts/export_runtime_contracts.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "MODELS", (*module.MODELS, EvenNumber))
    with pytest.raises(ValueError, match="multipleOf"):
        module.artifacts()
