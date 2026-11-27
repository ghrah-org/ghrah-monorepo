# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""生成器的声明读取面：在短生命周期子进程里 import Operation 模块。

生成器只 import 取模块常量（``TITLE`` / ``SUMMARY`` / ``TAGS`` /
``PERMISSIONS`` / ``EFFECTS`` / ``TIMEOUT_MS`` / ``RESOURCES`` / ``EXPOSE``）
与 ``Input`` / ``Output`` 模型，**不执行** ``execute``。这一步只在作者/发布侧
的 ``build``；安装器不 import 包代码。

子进程隔离的理由：模块级代码会跑（import 即执行），不能让作者代码碰生成器
进程的状态。失败被原样带回（模块名 + 异常链），不在本进程吞。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "Declaration",
    "DeclarationError",
    "read_declaration",
]

_CHILD_SOURCE = r"""
import importlib.util, json, os, sys

module_path, = sys.argv[1:2]
sys.path.insert(0, os.path.dirname(module_path))
spec = importlib.util.spec_from_file_location("_ghrah_build_probe", module_path)
module = importlib.util.module_from_spec(spec)
# 必须先登记再执行：``from __future__ import annotations`` 下 pydantic 需要能按
# 模块名解析字符串注解（否则 dict[str, Any] 之类会报 not fully defined）。
sys.modules[spec.name] = module
try:
    spec.loader.exec_module(module)
except Exception as exc:
    sys.stdout.write(json.dumps({
        "ok": False, "stage": "import",
        "error": f"{type(exc).__name__}: {exc}",
    }, ensure_ascii=False))
    sys.exit(0)

payload = {}
for name in ("TITLE", "SUMMARY", "TAGS", "PERMISSIONS", "EFFECTS",
             "TIMEOUT_MS", "RESOURCES", "EXPOSE"):
    payload[name] = getattr(module, name, None)

def model_schema(attr):
    model = getattr(module, attr, None)
    if model is None:
        return None
    return model.model_json_schema()

try:
    payload["INPUT_SCHEMA"] = model_schema("Input")
    payload["OUTPUT_SCHEMA"] = model_schema("Output")
except Exception as exc:
    sys.stdout.write(json.dumps({
        "ok": False, "stage": "schema",
        "error": f"{type(exc).__name__}: {exc}",
    }, ensure_ascii=False))
    sys.exit(0)

sys.stdout.write(json.dumps({"ok": True, "payload": payload},
                            ensure_ascii=False))
"""


class DeclarationError(ValueError):
    """读取 Operation 声明失败；消息面向包作者。"""


_LENIENT_DEFAULTS: dict[str, Any] = {
    "TITLE": None,
    "SUMMARY": None,
    "TAGS": [],
    "PERMISSIONS": {},
    "EFFECTS": [],
    "TIMEOUT_MS": 60000,
}
"""宽松模式的默认值：只有 ``Input`` / ``Output`` / ``execute`` 是硬契约。"""


@dataclass(frozen=True)
class Declaration:
    """一个 Operation 模块的声明读数。

    ``title`` / ``summary`` 在宽松模式下允许为空（上传导入会按成员键补默认值）；
    严格模式（作者侧 ``build``）仍然要求作者显式声明。
    """

    title: str | None
    summary: str | None
    tags: list[str]
    permissions: dict[str, Any]
    effects: list[str]
    timeout_ms: int
    resources: dict[str, Any]
    expose: bool
    input_schema: dict[str, Any] | None
    output_schema: dict[str, Any] | None


def read_declaration(script: Path, *, lenient: bool = False) -> Declaration:
    """在子进程里 import ``operations/<键>.py`` 并读取声明。

    Args:
        script: 实现脚本的绝对路径。
        lenient: 宽松模式：缺失 ``TITLE`` / ``SUMMARY`` / ``TAGS`` / ``PERMISSIONS`` /
            ``EFFECTS`` / ``TIMEOUT_MS`` 时用空值或默认值，不报错。上传导入用它，
            因为上传包允许只写 ``Input`` / ``Output`` / ``execute``。

    Returns:
        声明读数。

    Raises:
        DeclarationError: import 失败、必填常量类型不符，或严格模式下常量缺失。
    """
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    try:
        result = subprocess.run(
            [sys.executable, "-c", _CHILD_SOURCE, str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        raise DeclarationError(f"{script.name}: 声明读取超时（60s）") from exc
    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DeclarationError(
            f"{script.name}: 声明读取子进程输出非法 JSON: {result.stdout[:200]!r}"
        ) from exc
    if not envelope.get("ok"):
        raise DeclarationError(
            f"{script.name}: {envelope.get('stage')} 阶段失败: {envelope.get('error')}"
        )
    raw = envelope["payload"]
    return _validate(script.name, raw, lenient=lenient)


def _validate(name: str, raw: dict[str, Any], *, lenient: bool = False) -> Declaration:
    """按声明常量表校验类型；严格模式必填项缺失即失败，宽松模式补默认值。"""
    required = ("TITLE", "SUMMARY", "TAGS", "PERMISSIONS", "EFFECTS", "TIMEOUT_MS")
    if lenient:
        for field in required:
            if raw.get(field) is None:
                raw[field] = _LENIENT_DEFAULTS[field]
    else:
        for field in required:
            if raw.get(field) is None:
                raise DeclarationError(f"{name}: 缺少必填常量 {field}")
    if raw["TITLE"] is not None and (not isinstance(raw["TITLE"], str) or not raw["TITLE"].strip()):
        raise DeclarationError(f"{name}: TITLE 必须是非空字符串")
    if raw["SUMMARY"] is not None and (
        not isinstance(raw["SUMMARY"], str) or not raw["SUMMARY"].strip()
    ):
        raise DeclarationError(f"{name}: SUMMARY 必须是非空字符串")
    if not isinstance(raw["TAGS"], list) or not all(isinstance(t, str) for t in raw["TAGS"]):
        raise DeclarationError(f"{name}: TAGS 必须是 list[str]（可为 []）")
    if not isinstance(raw["PERMISSIONS"], dict):
        raise DeclarationError(f"{name}: PERMISSIONS 必须是映射（可为 {{}}）")
    if not isinstance(raw["EFFECTS"], list) or not all(isinstance(e, str) for e in raw["EFFECTS"]):
        raise DeclarationError(f"{name}: EFFECTS 必须是 list[str]（可为 []）")
    if not isinstance(raw["TIMEOUT_MS"], int) or isinstance(raw["TIMEOUT_MS"], bool):
        raise DeclarationError(f"{name}: TIMEOUT_MS 必须是 int")
    if raw["TIMEOUT_MS"] <= 0:
        raise DeclarationError(f"{name}: TIMEOUT_MS 必须 > 0")
    resources = raw.get("RESOURCES") or {}
    if not isinstance(resources, dict):
        raise DeclarationError(f"{name}: RESOURCES 必须是映射")
    expose = raw.get("EXPOSE")
    if expose is None:
        expose = True
    if not isinstance(expose, bool):
        raise DeclarationError(f"{name}: EXPOSE 必须是 bool")
    if raw.get("INPUT_SCHEMA") is None:
        raise DeclarationError(f"{name}: 缺少 Input 模型（input_schema 无来源）")
    return Declaration(
        title=raw["TITLE"],
        summary=raw["SUMMARY"],
        tags=sorted(set(raw["TAGS"])),
        permissions=raw["PERMISSIONS"],
        effects=sorted(set(raw["EFFECTS"])),
        timeout_ms=raw["TIMEOUT_MS"],
        resources=resources,
        expose=expose,
        input_schema=raw["INPUT_SCHEMA"],
        output_schema=raw["OUTPUT_SCHEMA"],
    )
