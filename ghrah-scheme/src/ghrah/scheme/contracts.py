# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""Scheme、动作和受限提供者调用的进程内契约。"""

from __future__ import annotations

import re
from dataclasses import dataclass
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


@dataclass(frozen=True)
class SchemeSpec:
    """一种服务资源域及其动作；不要求独立包或独立进程。"""

    name: str
    version: str
    actions: tuple[ActionSpec, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", self.name) or not self.version.strip():
            raise ValueError("Scheme 名称或版本错误")
        if not isinstance(self.actions, tuple) or not self.actions:
            raise ValueError("必须显式声明非空动作元组")
        if len({action.name for action in self.actions}) != len(self.actions):
            raise ValueError("动作名不能重复")

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
