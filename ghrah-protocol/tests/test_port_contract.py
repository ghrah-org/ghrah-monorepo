# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""错误载荷、分页/长动作视图、语料与 JSON 可往返。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ghrah.protocol.corpus import (
    Corpus,
    CorpusCase,
    JsonShapeError,
    check_json_serializable,
    diff_dicts,
)
from ghrah.protocol.problem import Problem, ProblemCode
from ghrah.protocol.views.common import AckView, Operation, OperationState, Page

# ─── Problem ───


def test_problem_make_derives_retryable_from_code() -> None:
    assert Problem.make(ProblemCode.BUSY).retryable is True
    assert Problem.make(ProblemCode.UNAVAILABLE).retryable is True
    assert Problem.make(ProblemCode.NOT_FOUND).retryable is False


def test_problem_make_accepts_overrides() -> None:
    problem = Problem.make(
        ProblemCode.NOT_FOUND,
        capability="nodes.get",
        detail="missing",
        details={"agent_id": "a1"},
        trace_id="t-1",
        retryable=True,
    )
    assert problem.capability == "nodes.get"
    assert problem.details == {"agent_id": "a1"}
    assert problem.trace_id == "t-1"
    assert problem.retryable is True


def test_problem_has_no_transport_fields() -> None:
    fields = set(Problem.model_fields)
    assert "status" not in fields
    assert "type" not in fields


# ─── 视图 ───


def test_page_defaults_to_empty() -> None:
    page: Page[str] = Page[str]()
    assert page.items == []
    assert page.next_cursor is None
    assert page.has_more is False


def test_operation_terminal_states() -> None:
    assert Operation(operation_id="op").terminal is False
    assert Operation(operation_id="op", state=OperationState.RUNNING).terminal is False
    assert Operation(operation_id="op", state=OperationState.SUCCEEDED).terminal is True
    assert Operation(operation_id="op", state=OperationState.FAILED).terminal is True


def test_ack_view_defaults_ok() -> None:
    assert AckView().ok is True


# ─── JSON 可往返 ───


def test_check_json_serializable_accepts_plain_json() -> None:
    check_json_serializable(
        {"s": "x", "i": 1, "f": 1.5, "b": True, "n": None, "l": [1, {"k": "v"}]}
    )
    check_json_serializable({"items": (1, 2, 3)})


def test_check_json_serializable_rejects_datetime_with_path() -> None:
    with pytest.raises(JsonShapeError) as excinfo:
        check_json_serializable({"node": {"created_at": datetime.now(UTC)}})
    assert "node.created_at" in str(excinfo.value)


def test_check_json_serializable_rejects_set_and_non_str_key() -> None:
    with pytest.raises(JsonShapeError, match="set"):
        check_json_serializable({"x": {1, 2}})
    with pytest.raises(JsonShapeError, match="not str"):
        check_json_serializable({1: "x"})


def test_check_json_serializable_rejects_non_finite_floats() -> None:
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(JsonShapeError, match="non-finite"):
            check_json_serializable({"x": value})


# ─── 差异比较 ───


def test_diff_dicts_reports_missing_extra_and_changed() -> None:
    differences = diff_dicts({"a": 1, "b": 2}, {"a": 9, "c": 3})
    assert len(differences) == 3
    assert any("a: 9 != expected 1" in item for item in differences)
    assert any("b: missing" in item for item in differences)
    assert any("c: unexpected" in item for item in differences)


def test_diff_dicts_reports_list_length_and_items() -> None:
    assert diff_dicts([1, 2], [1, 2, 3]) == ["$: length 3 != expected 2"]
    assert diff_dicts([1], [2]) == ["[0]: 2 != expected 1"]


def test_diff_dicts_returns_empty_for_equal() -> None:
    assert diff_dicts({"a": [1, {"b": None}]}, {"a": [1, {"b": None}]}) == []


# ─── 语料 ───


def _case() -> CorpusCase:
    return CorpusCase(
        name="nodes.get.missing",
        capability="nodes.get",
        payload={"project_id": "p1", "agent_id": "a1", "node_id": "n1"},
        response={"code": "NOT_FOUND", "capability": "nodes.get"},
    )


def test_corpus_roundtrip(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path)
    corpus.save(_case())
    assert corpus.names() == ["nodes.get.missing"]

    loaded = corpus.load("nodes.get.missing")
    assert loaded.capability == "nodes.get"
    assert loaded.payload["agent_id"] == "a1"
    assert loaded.response["code"] == "NOT_FOUND"


def test_corpus_record_rejects_non_json_payload(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path)
    with pytest.raises(JsonShapeError, match="accepted_at"):
        corpus.record(
            name="bad",
            capability="nodes.get",
            payload={"accepted_at": datetime.now(UTC)},
            response={},
        )


def test_corpus_record_refuses_overwrite_when_disabled(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path)
    corpus.record(name="once", capability="nodes.get", payload={}, response={})
    with pytest.raises(FileExistsError):
        corpus.record(
            name="once",
            capability="nodes.get",
            payload={},
            response={},
            overwrite=False,
        )


def test_corpus_sanitizes_names(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path)
    corpus.save(CorpusCase(name="CORE 21 / get", capability="nodes.get", payload={}, response={}))
    assert corpus.names() == ["CORE_21_get"]
