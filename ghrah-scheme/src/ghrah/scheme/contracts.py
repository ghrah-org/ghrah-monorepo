# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""Scheme、动作和受限提供者调用的进程内契约。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from ghrah.scheme.errors import ErrorCode, SchemeError
from ghrah.scheme.models import Principal, Target


class SchemeModel(BaseModel):
    """动作输入／输出的严格模型；嵌套值另由运行边界复制隔离。"""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


@dataclass(frozen=True)
class ActionSpec:
    """声明一个动作，不能据此新增主体权限。"""

    name: str
    input_model: type[SchemeModel]
    output_model: type[SchemeModel]
    mutating: bool = False
    cancellable: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]*", self.name):
            raise ValueError("动作名格式错误")
        for model in (self.input_model, self.output_model):
            if not issubclass(model, SchemeModel):
                raise TypeError("动作模型必须继承 SchemeModel")
            if model.model_config.get("extra") != "forbid" or not model.model_config.get("strict"):
                raise ValueError("动作模型不得放松严格输入约束")


@dataclass(frozen=True, kw_only=True)
class SchemeSpec:
    """一种资源种类下的确定契约；本地模型是动作 schema 的唯一来源。"""

    kind: str
    contract: str
    version: str
    actions: tuple[ActionSpec, ...]
    digest: str = field(init=False)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", self.kind):
            raise ValueError("资源种类格式错误")
        if not re.fullmatch(r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*", self.contract):
            raise ValueError("契约身份必须为规范的小写点分名称")
        if not self.version.strip() or self.version != self.version.strip():
            raise ValueError("契约版本不能为空或包含首尾空白")
        if not isinstance(self.actions, tuple) or not self.actions:
            raise ValueError("必须显式声明非空动作元组")
        if len({action.name for action in self.actions}) != len(self.actions):
            raise ValueError("动作名不能重复")
        # 只定义本地精确内容指纹；动作顺序不改变契约，schema 数组仍保留原序。
        content = {
            "format": "ghrah.scheme.local-contract.v1",
            "kind": self.kind,
            "contract": self.contract,
            "version": self.version,
            "actions": [
                {
                    "name": action.name,
                    "input": action.input_model.model_json_schema(),
                    "output": action.output_model.model_json_schema(),
                    "mutating": action.mutating,
                    "cancellable": action.cancellable,
                    "description": action.description,
                }
                for action in sorted(self.actions, key=lambda item: item.name)
            ],
        }
        encoded = json.dumps(
            content, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        object.__setattr__(self, "digest", "sha256:" + hashlib.sha256(encoded).hexdigest())

    def action(self, name: str) -> ActionSpec:
        """只解析已登记动作，不进行任意属性查找。"""
        for action in self.actions:
            if action.name == name:
                return action
        raise SchemeError(ErrorCode.UNKNOWN_ACTION)


@dataclass(frozen=True)
class ProviderCall:
    """提供者收到的隔离输入，不包含运行时或管理端口。"""

    principal: Principal
    target: Target
    action: str
    arguments: SchemeModel
    invocation_id: str
    admission_basis: str
    admission_record: str | None


class Provider(Protocol):
    """受信提供者实现；正常结果仍须通过输出契约校验。"""

    async def execute(self, call: ProviderCall) -> SchemeModel:
        """执行固定目标动作，不自行替换调用身份。"""
        ...
