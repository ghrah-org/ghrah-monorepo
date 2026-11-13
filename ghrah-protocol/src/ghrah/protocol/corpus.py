# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""契约语料与 JSON 可往返强制。

两件事：

1. **契约回归** —— 每个能力录 ``(payload, expected_response)``，实现一改就报红；
2. **JSON 可往返强制** —— 跨模块传的每个值都必须能被 ``json`` 无损往返。
   进程内没有物理边界，这是唯一能挡住「边界泄漏活对象」的手段。

``check_json_serializable`` 刻意比 ``json.dumps`` 更严：拒绝 ``NaN`` / ``Infinity``
（``json.dumps`` 默认会写出**非法 JSON**）、拒绝非 ``str`` 键，并在报错时给出路径。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "Corpus",
    "CorpusCase",
    "JsonShapeError",
    "check_json_serializable",
    "diff_dicts",
]

_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class JsonShapeError(TypeError):
    """值不是合法的 JSON 形状。"""


def check_json_serializable(value: Any, *, path: str = "body") -> None:
    """校验值是否为合法 JSON 形状（可无损往返）。

    Args:
        value: 待校验的值
        path: 出错时用于定位的路径前缀

    Raises:
        JsonShapeError: 含非 JSON 类型、非法键，或非有限浮点数
    """
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise JsonShapeError(f"{path}: non-finite float {value!r} is not valid JSON")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise JsonShapeError(f"{path}: dict key {key!r} is not str")
            check_json_serializable(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            check_json_serializable(item, path=f"{path}[{index}]")
        return
    raise JsonShapeError(f"{path}: {type(value).__name__} is not JSON-serializable")


def _label(path: str) -> str:
    """把空路径显示为 ``$``（JSON 根）。"""
    return path or "$"


def diff_dicts(expected: Any, actual: Any, *, path: str = "") -> list[str]:
    """递归比较两个 JSON 形状，返回人类可读的差异列表。

    Args:
        expected: 期望值（通常来自语料）
        actual: 实际值（通常来自 ``invoke``）
        path: 起始路径前缀

    Returns:
        差异描述；空列表表示完全一致
    """
    if isinstance(expected, dict) and isinstance(actual, dict):
        differences: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            child = f"{path}.{key}" if path else key
            if key not in expected:
                differences.append(f"{child}: unexpected (actual={actual[key]!r})")
            elif key not in actual:
                differences.append(f"{child}: missing (expected={expected[key]!r})")
            else:
                differences.extend(diff_dicts(expected[key], actual[key], path=child))
        return differences
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return [f"{_label(path)}: length {len(actual)} != expected {len(expected)}"]
        differences = []
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            differences.extend(diff_dicts(left, right, path=f"{path}[{index}]"))
        return differences
    if expected != actual:
        return [f"{_label(path)}: {actual!r} != expected {expected!r}"]
    return []


@dataclass(frozen=True, slots=True)
class CorpusCase:
    """一条语料。

    Attributes:
        name: 用例名（同时是文件名主干）
        capability: 能力名，如 ``"nodes.get"``
        payload: 录入的入参
        response: 录入的出参（``invoke`` 返回的 JSON 形状）
    """

    name: str
    capability: str
    payload: dict[str, Any]
    response: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        """投影为可落盘的 JSON 对象。"""
        return {
            "name": self.name,
            "capability": self.capability,
            "payload": self.payload,
            "response": self.response,
        }


class Corpus:
    """磁盘上的语料目录。

    Args:
        root: 语料目录（不存在时由 :meth:`save` 创建）
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        """语料根目录。"""
        return self._root

    def path_for(self, name: str) -> Path:
        """返回某用例的落盘路径。

        Args:
            name: 用例名

        Returns:
            该用例的 ``.json`` 路径
        """
        return self._root / f"{_NAME_SAFE_RE.sub('_', name)}.json"

    def names(self) -> list[str]:
        """列出已录用例名（按文件名排序）。"""
        if not self._root.is_dir():
            return []
        return sorted(path.stem for path in self._root.glob("*.json"))

    def load(self, name: str) -> CorpusCase:
        """读取一条已录用例。

        Args:
            name: 用例名

        Returns:
            解析后的用例

        Raises:
            FileNotFoundError: 用例不存在
            JsonShapeError: 文件格式不合法
        """
        path = self.path_for(name)
        with path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise JsonShapeError(f"{path}: corpus case must be a JSON object")
        capability = raw.get("capability")
        payload = raw.get("payload")
        response = raw.get("response")
        if not isinstance(capability, str):
            raise JsonShapeError(f"{path}: corpus case needs string 'capability'")
        if not isinstance(payload, dict) or not isinstance(response, dict):
            raise JsonShapeError(f"{path}: corpus case needs object 'payload' and 'response'")
        return CorpusCase(name=name, capability=capability, payload=payload, response=response)

    def save(self, case: CorpusCase) -> Path:
        """写入一条用例（覆盖同名文件）。

        Args:
            case: 待写入用例

        Returns:
            写入的文件路径

        Raises:
            JsonShapeError: 用例含非 JSON 值
        """
        check_json_serializable(case.to_json(), path=case.name)
        self._root.mkdir(parents=True, exist_ok=True)
        path = self.path_for(case.name)
        text = json.dumps(case.to_json(), ensure_ascii=False, indent=2, sort_keys=True)
        path.write_text(text + "\n", encoding="utf-8")
        return path

    def record(
        self,
        *,
        name: str,
        capability: str,
        payload: dict[str, Any],
        response: dict[str, Any],
        overwrite: bool = True,
    ) -> CorpusCase:
        """录制一次真实调用。

        Args:
            name: 用例名
            capability: 能力名
            payload: 实际入参
            response: 实际出参
            overwrite: 已存在时是否覆盖

        Returns:
            录制出的用例

        Raises:
            FileExistsError: 用例已存在且 ``overwrite`` 为 ``False``
            JsonShapeError: 入参或出参含非 JSON 值
        """
        path = self.path_for(name)
        if path.exists() and not overwrite:
            raise FileExistsError(path)
        check_json_serializable(payload, path=f"{name}.payload")
        check_json_serializable(response, path=f"{name}.response")
        case = CorpusCase(
            name=name,
            capability=capability,
            payload=dict(payload),
            response=dict(response),
        )
        self.save(case)
        return case
