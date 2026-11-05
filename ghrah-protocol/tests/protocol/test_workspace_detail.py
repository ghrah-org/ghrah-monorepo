"""Workspace 详情使用 Python/TS 同一个服务端形状。"""

import json
from pathlib import Path

from ghrah.protocol.payloads.workspace import WorkspaceGetResponsePayload


def test_workspace_detail_shared_fixture() -> None:
    source = (
        Path(__file__).resolve().parents[3]
        / "ghrah-observer-webui/packages/protocol/src/__fixtures__/workspace-detail.json"
    )
    data = json.loads(source.read_text())
    assert WorkspaceGetResponsePayload.model_validate(data).model_dump() == data
