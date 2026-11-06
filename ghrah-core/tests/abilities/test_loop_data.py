# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""LoopData tests：循环数据只经 LOOP 服务提供，不进入调用对象。"""

from __future__ import annotations

from ghrah.ability.execution.loop_data import LoopData
from ghrah.types.results import ActionOutcome, ActionResult


def test_get_update_and_raw_roundtrip() -> None:
    raw = {"llm_response": "hello"}
    data = LoopData(raw)

    assert data.get("llm_response") == "hello"

    data.update({"tool_args": {"file_path": "a.txt"}})
    data.update({"extra": True})

    assert data.raw["tool_args"] == {"file_path": "a.txt"}
    assert data.raw["extra"] is True
    assert data.get("missing", "fallback") == "fallback"
    # 初始化是快照语义：外部 dict 不被反向修改
    assert raw == {"llm_response": "hello"}


def test_snapshot_is_deep_copy() -> None:
    data = LoopData()
    data.update({"cumulative_token_usage": {"input": 1}})

    snapshot = data.snapshot()
    snapshot["cumulative_token_usage"]["input"] = 99

    assert data.get("cumulative_token_usage") == {"input": 1}


def test_typed_properties_default_empty_string() -> None:
    data = LoopData()

    assert data.llm_response == ""
    assert data.response == ""
    assert data.cot_content == ""
    assert data.last_action_result is None


def test_typed_properties_read_raw_keys() -> None:
    result = ActionResult(outcome=ActionOutcome.SUCCESS, data={"content": "done"})
    data = LoopData(
        {
            "llm_response": "本轮正文",
            "response": "累积响应",
            "cot_content": "思考过程",
            "last_action_result": result,
        }
    )

    assert data.llm_response == "本轮正文"
    assert data.response == "累积响应"
    assert data.cot_content == "思考过程"
    assert data.last_action_result is result


def test_typed_properties_ignore_non_string_values() -> None:
    """非字符串值不当作模型文本（回退为空串，而不是把对象塞给调用方）。"""
    data = LoopData({"llm_response": 42, "response": ["x"], "cot_content": None})

    assert data.llm_response == ""
    assert data.response == ""
    assert data.cot_content == ""


def test_update_merges_another_loop_data() -> None:
    """Hook 的 modified_context 走 update：合并而非替换。"""
    data = LoopData({"llm_response": "keep"})
    data.update(LoopData({"hook_modified": True}))

    assert data.raw == {"llm_response": "keep", "hook_modified": True}


def test_update_none_is_noop() -> None:
    data = LoopData({"llm_response": "keep"})
    data.update(None)

    assert data.raw == {"llm_response": "keep"}
