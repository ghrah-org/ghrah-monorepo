# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""wire 快照双向校验（Python 侧半部）。

TS 侧由 protocol-align.spec.ts 用 Zod 解析同一批快照；本测试做镜像校验：
model_validate 快照 JSON 成功 + dump 幂等 + key 容差规则（与 TS 对齐规则同源）。
只覆盖本批新增快照，不回溯存量（最小改动）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import BaseModel

from ghrah.protocol.types import (
    PluginCrashedPayload,
    PluginLifecyclePayload,
    PluginNegotiatedPayload,
    PluginNegotiatePayload,
    PluginNegotiateResultPayload,
    SendMessagePayload,
    TaskClaimEventPayload,
    TaskClaimListPayload,
    TaskClaimListResultPayload,
    TaskClaimPayload,
    TaskDumpPayload,
    TaskDumpResultPayload,
    TaskEvidencePayload,
    TaskInfoPayload,
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

ModelT = TypeVar("ModelT", bound=BaseModel)

# 本批新增快照：名称 → Pydantic 模型（与 TS PAYLOAD_SCHEMA_MAP 同步维护）
S1_SNAPSHOTS: dict[str, type[BaseModel]] = {
    "PluginNegotiatePayload": PluginNegotiatePayload,
    "PluginNegotiateResultPayload": PluginNegotiateResultPayload,
    "PluginLifecyclePayload": PluginLifecyclePayload,
    "PluginNegotiatedPayload": PluginNegotiatedPayload,
    "PluginCrashedPayload": PluginCrashedPayload,
    "TaskEvidencePayload": TaskEvidencePayload,
    "TaskClaimPayload": TaskClaimPayload,
    "TaskSubmitCompletionPayload": TaskSubmitCompletionPayload,
    "TaskVerifyPayload": TaskVerifyPayload,
    "TaskClaimListPayload": TaskClaimListPayload,
    "TaskClaimListResultPayload": TaskClaimListResultPayload,
    "TaskClaimEventPayload": TaskClaimEventPayload,
    "TaskVerificationGapsPayload": TaskVerificationGapsPayload,
    "TaskInfoPayload_verification": TaskInfoPayload,
    "TaskDumpPayload": TaskDumpPayload,
    "TaskDumpResultPayload": TaskDumpResultPayload,
    "SendMessagePayload_with_blocks": SendMessagePayload,
    "SendMessagePayload_with_video": SendMessagePayload,
}


def _load(name: str) -> dict[str, object] | None:
    path = SNAPSHOTS_DIR / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(("name", "model"), sorted(S1_SNAPSHOTS.items()))
def test_snapshot_roundtrip(name: str, model: type[BaseModel]) -> None:
    """快照可被 Python 模型解析，且 dump 幂等（防手贴走样）。"""
    snapshot = _load(name)
    if snapshot is None:
        pytest.skip("TS 快照目录不存在（独立发布 protocol 包时跳过；CI 全仓 checkout 恒生效）")

    parsed = model.model_validate(snapshot)
    dumped = parsed.model_dump(mode="json")

    # 幂等：dump 再解析再 dump 逐位一致
    assert model.model_validate(dumped).model_dump(mode="json") == dumped

    # key 容差（镜像 TS 对齐规则）：
    # - 快照有的 key，dump 必须有（Python 不许丢字段）；
    # - dump 多出的 key 仅当值为 None（默认值补齐场景）。
    snapshot_keys = set(snapshot)
    dumped_keys = set(dumped)
    assert snapshot_keys - dumped_keys == set(), f"{name}: keys missing from dump"
    extra = dumped_keys - snapshot_keys
    assert all(dumped[k] is None for k in extra), f"{name}: extra keys with non-None value: {extra}"


def test_s1_snapshot_files_exist() -> None:
    """本批全部 16 个快照文件在 TS 目录存在（防生成脚本产物漏提交）。"""
    for name in S1_SNAPSHOTS:
        assert (SNAPSHOTS_DIR / f"{name}.json").exists(), f"missing snapshot: {name}"
