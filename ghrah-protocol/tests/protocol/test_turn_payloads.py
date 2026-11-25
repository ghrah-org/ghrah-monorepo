# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0

import pytest
from pydantic import ValidationError

from ghrah.protocol.enums import CommandType
from ghrah.protocol.envelope import COMMAND_PAYLOAD_MAP, envelope_from_dict
from ghrah.protocol.payloads.turn import TurnAbortPayload, TurnRollbackPayload
from ghrah.protocol.routing import CORE_COMMANDS

SCOPE = {"project_id": "project", "agent_id": "agent"}


@pytest.mark.parametrize(
    ("command", "model", "payload"),
    [
        (CommandType.TURN_ABORT, TurnAbortPayload, SCOPE),
        (
            CommandType.TURN_ROLLBACK,
            TurnRollbackPayload,
            {**SCOPE, "turn_id": "turn", "target": "node", "node_id": "node"},
        ),
    ],
)
def test_turn_command_envelope_round_trip(command, model, payload):
    assert command.value in CORE_COMMANDS
    assert COMMAND_PAYLOAD_MAP[command] is model
    message = envelope_from_dict({"type": command.value, "request_id": "req", "payload": payload})
    assert message.payload == model.model_validate(payload)
    assert envelope_from_dict(message.model_dump_with_timestamp()).payload == message.payload


@pytest.mark.parametrize("extra", [{"agent_name": "name"}, {"turn_id": ""}, {"reason": 1}])
def test_turn_abort_rejects_invalid_scope_or_extra_fields(extra):
    with pytest.raises(ValidationError):
        TurnAbortPayload.model_validate({**SCOPE, **extra})


@pytest.mark.parametrize(
    "extra",
    [{"target": "node"}, {"node_id": "node"}, {"dry_run": False}, {"agent_name": "name"}],
)
def test_turn_rollback_requires_node_only_for_node_target_and_forbids_internal_fields(extra):
    with pytest.raises(ValidationError):
        TurnRollbackPayload.model_validate({**SCOPE, "turn_id": "turn", **extra})


def test_turn_payload_requires_scope_and_rollback_requires_turn():
    with pytest.raises(ValidationError):
        TurnAbortPayload.model_validate({"agent_id": "agent"})
    with pytest.raises(ValidationError):
        TurnRollbackPayload.model_validate(SCOPE)
    with pytest.raises(ValidationError):
        envelope_from_dict({"type": "turn_abort", "payload": []})
