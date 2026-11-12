# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""wire 快照生成：Python model_dump 产物落盘 TS __snapshots__ 目录（仓内手贴惯例的脚本化）。

用法（根目录）：uv run python ghrah-protocol/tests/protocol/gen_s1_snapshots.py
仅生成本批新增快照；生成后双侧同 commit（TS protocol-align 消费）。
"""

from __future__ import annotations

import json
from pathlib import Path

from ghrah.plugin.negotiator import PythonHalfInfo, TsHalfReport, negotiate

from ghrah.protocol.types import (
    PluginCrashedPayload,
    PluginLifecyclePayload,
    PluginNegotiatedPayload,
    PluginNegotiatePayload,
    PluginNegotiateResultPayload,
    SendMessagePayload,
    TaskCheckOutcomePayload,
    TaskClaimEventPayload,
    TaskClaimListPayload,
    TaskClaimListResultPayload,
    TaskClaimPayload,
    TaskDumpPayload,
    TaskDumpResultPayload,
    TaskEvidencePayload,
    TaskInfoPayload,
    TaskMissingCheckPayload,
    TaskProvenancePayload,
    TaskSubmitCompletionPayload,
    TaskVerificationGapsPayload,
    TaskVerifyPayload,
)

SNAPSHOTS_DIR = (
    Path(__file__).resolve().parents[3]
    / "ghrah-observer-webui"
    / "packages"
    / "protocol"
    / "src"
    / "__snapshots__"
)

SAMPLES: dict[str, object] = {
    "PluginNegotiatePayload": PluginNegotiatePayload(
        enabled_ts=[
            TsHalfReport(
                plugin_id="task-commit-attribution",
                version="1.0.0",
                provides=["badge/git_commit"],
            ),
            TsHalfReport(plugin_id="demo-plugin", version="0.2.0", provides=["command/demo_run"]),
        ]
    ),
    "PluginNegotiateResultPayload": PluginNegotiateResultPayload.model_validate(
        negotiate(
            [
                PythonHalfInfo(
                    plugin_id="demo",
                    version="1.0.0",
                    provides=["checker/x", "command/y"],
                    requires_capabilities=["core:z"],
                ),
                PythonHalfInfo(plugin_id="py-only", version="1.0.0", provides=["checker/a"]),
            ],
            [
                TsHalfReport(plugin_id="demo", version="1.0.0", provides=["badge/z"]),
                TsHalfReport(plugin_id="ts-only", version="0.3.0", provides=["badge/w"]),
                TsHalfReport(plugin_id="conflicted", version="2.0.0", provides=["badge/c"]),
            ],
        ).model_dump(mode="json")
    ),
    "PluginLifecyclePayload": PluginLifecyclePayload(
        plugin_id="task-commit-attribution", version="1.0.0", instance_ids=["prod-eu", "staging"]
    ),
    "PluginNegotiatedPayload": PluginNegotiatedPayload(
        changed=["task-commit-attribution", "demo-plugin"]
    ),
    "PluginCrashedPayload": PluginCrashedPayload(
        plugin_id="demo-plugin",
        command="command/demo_run",
        error="ValueError: boom",
        instance_id="prod-eu",
    ),
    "TaskEvidencePayload": TaskEvidencePayload(
        evidence_id="ev-001",
        kind="git_commit",
        ref="abc1234",
        digest="sha256:deadbeef",
        payload={"repo": "ghrah", "branch": "main", "short_sha": "abc1234"},
        created_by="agent:backend-dev",
        created_at="2026-09-20T10:00:00Z",
    ),
    "TaskClaimPayload": TaskClaimPayload(
        claim_id="claim-001",
        task_id="task-001",
        claimant_type="agent",
        claimant_id="agent:backend-dev",
        claimant_name="backend-dev",
        note="login flow implemented",
        evidence=[
            TaskEvidencePayload(
                evidence_id="ev-001",
                kind="git_commit",
                ref="abc1234",
                payload={"repo": "ghrah", "branch": "main"},
                created_by="agent:backend-dev",
                created_at="2026-09-20T10:00:00Z",
            )
        ],
        state="submitted",
        checks=[TaskCheckOutcomePayload(checker="commit_in_repo", passed=True)],
        provenance=TaskProvenancePayload(
            agent_id="agent:backend-dev", session_id="sess-001", node_id="node-001"
        ),
        created_at="2026-09-20T10:05:00Z",
    ),
    "TaskSubmitCompletionPayload": TaskSubmitCompletionPayload(
        task_id="task-001",
        claimant_type="agent",
        claimant_id="agent:backend-dev",
        claimant_name="backend-dev",
        note="login flow implemented",
        evidence=[
            {
                "kind": "git_commit",
                "ref": "abc1234",
                "digest": "sha256:deadbeef",
                "payload": {"repo": "ghrah"},
            }
        ],
        provenance={
            "agent_id": "agent:backend-dev",
            "session_id": "sess-001",
            "node_id": "node-001",
        },
        expected_version=7,
    ),
    "TaskVerifyPayload": TaskVerifyPayload(
        task_id="task-001",
        claim_id="claim-001",
        verdict="rejected",
        verifier_id="human:reviewer",
        verifier_name="reviewer",
        reason="missing tests",
        expected_version=8,
    ),
    "TaskClaimListPayload": TaskClaimListPayload(task_id="task-001", state="submitted", limit=50),
    "TaskClaimListResultPayload": TaskClaimListResultPayload(
        claims=[
            TaskClaimPayload(
                claim_id="claim-001",
                task_id="task-001",
                claimant_id="agent:backend-dev",
                state="submitted",
                created_at="2026-09-20T10:05:00Z",
            )
        ],
        count=1,
    ),
    "TaskClaimEventPayload": TaskClaimEventPayload(
        task=TaskInfoPayload.model_validate(
            {
                "task_id": "task-001",
                "project_id": "proj-001",
                "title": "Implement login flow",
                "agent_name": "backend-dev",
                "status": "delivered",
                "priority": "high",
                "created_at": "2026-09-20T00:00:00Z",
                "updated_at": "2026-09-20T10:05:00Z",
                "started_at": "2026-09-20T00:30:00Z",
                "verification": {"evidence_min": 1, "checks": ["commit_in_repo"]},
            }
        ),
        claim=TaskClaimPayload(
            claim_id="claim-001",
            task_id="task-001",
            claimant_id="agent:backend-dev",
            claimant_name="backend-dev",
            state="submitted",
            created_at="2026-09-20T10:05:00Z",
        ),
        previous_status="in_progress",
        reason=None,
    ),
    "TaskVerificationGapsPayload": TaskVerificationGapsPayload(
        evidence_have=0,
        evidence_need=1,
        missing_evidence_kinds=["git_commit"],
        missing_checks=[
            TaskMissingCheckPayload(
                checker="commit_in_repo", candidates=["task-commit-attribution"]
            )
        ],
        failed_checks=[
            TaskCheckOutcomePayload(checker="tests_pass", passed=False, detail="2 failed")
        ],
        missing_approver="human",
    ),
    "TaskInfoPayload_verification": TaskInfoPayload.model_validate(
        {
            "task_id": "task-001",
            "project_id": "proj-001",
            "title": "Implement login flow",
            "description": "Add OAuth login flow",
            "agent_name": "backend-dev",
            "status": "delivered",
            "priority": "high",
            "created_at": "2026-09-20T00:00:00Z",
            "updated_at": "2026-09-20T10:05:00Z",
            "started_at": "2026-09-20T00:30:00Z",
            "completed_at": None,
            "verification": {
                "evidence_min": 1,
                "evidence_kinds": ["git_commit"],
                "checks": ["commit_in_repo"],
                "approver": "human",
            },
        }
    ),
    "TaskDumpPayload": TaskDumpPayload(project_id="proj-001", include_deleted=False, limit=100),
    "TaskDumpResultPayload": TaskDumpResultPayload(
        tasks=[
            TaskInfoPayload.model_validate(
                {
                    "task_id": "task-001",
                    "project_id": "proj-001",
                    "title": "Implement login flow",
                    "status": "delivered",
                    "created_at": "2026-09-20T00:00:00Z",
                    "updated_at": "2026-09-20T10:05:00Z",
                    "verification": {"evidence_min": 1, "checks": ["commit_in_repo"]},
                }
            )
        ],
        claims=[
            TaskClaimPayload(
                claim_id="claim-001",
                task_id="task-001",
                claimant_id="agent:backend-dev",
                state="submitted",
                evidence=[
                    TaskEvidencePayload(
                        evidence_id="ev-001",
                        kind="git_commit",
                        ref="abc1234",
                        created_by="agent:backend-dev",
                        created_at="2026-09-20T10:00:00Z",
                    )
                ],
                created_at="2026-09-20T10:05:00Z",
            )
        ],
        evidence=[
            TaskEvidencePayload(
                evidence_id="ev-001",
                kind="git_commit",
                ref="abc1234",
                created_by="agent:backend-dev",
                created_at="2026-09-20T10:00:00Z",
            )
        ],
    ),
    "SendMessagePayload_with_blocks": SendMessagePayload(
        project_id="proj-001",
        agent_id="agent-001",
        target="requirement-agent",
        content="",
        content_blocks=[
            {"type": "text", "text": "请看这张图纸"},
            {
                "type": "image",
                "base64": "iVBORw0KGgo=",
                "mime_type": "image/png",
            },
        ],
        metadata={"room_id": "room-001"},
    ),
}


def main() -> None:
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    for name, sample in SAMPLES.items():
        model = sample  # type: ignore[assignment]
        data = model.model_dump(mode="json")  # type: ignore[attr-defined]
        path = SNAPSHOTS_DIR / f"{name}.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
