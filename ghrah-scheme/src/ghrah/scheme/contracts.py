# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""Scheme、动作和受限提供者调用的进程内契约。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from ghrah.protocol.scheme import ActionDescriptor, ContractDescriptor
from pydantic import BaseModel, ConfigDict

from ghrah.scheme.codec import ActionCodec
from ghrah.scheme.errors import ErrorCode, SchemeError
from ghrah.scheme.models import JsonObject, Requester, Target


class SchemeModel(BaseModel):
    """动作输入／输出的严格模型；嵌套值另由运行边界复制隔离。"""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class CodecArguments(SchemeModel):
    """提供者内部使用的已校验 JSON；value 包装不进入公开参数或结果。"""

    value: dict[str, Any]


@dataclass(frozen=True)
class ActionSpec:
    """声明一个动作，不能据此新增主体权限。"""

    name: str
    input_model: type[SchemeModel] | None = None
    output_model: type[SchemeModel] | None = None
    mutating: bool = False
    cancellable: bool = True
    description: str = ""
    codec: ActionCodec | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]*", self.name):
            raise ValueError("动作名格式错误")
        if self.codec is not None:
            if self.input_model is not None or self.output_model is not None:
                raise ValueError("动作只能有一个契约作者")
            return
        for model in (self.input_model, self.output_model):
            if model is None or not issubclass(model, SchemeModel):
                raise TypeError("动作模型必须继承 SchemeModel")
            if model.model_config.get("extra") != "forbid" or not model.model_config.get("strict"):
                raise ValueError("动作模型不得放松严格输入约束")

    def decode(self, data: JsonObject) -> SchemeModel:
        """从唯一作者校验输入。"""
        if self.codec is not None:
            return CodecArguments(value=self.codec.decode(data))
        assert self.input_model is not None
        return self.input_model.model_validate(data, strict=True)

    def input_data(self, model: SchemeModel) -> JsonObject:
        """导出规范参数，不暴露内部 codec 包装。"""
        if self.codec is not None:
            assert isinstance(model, CodecArguments)
            return model.value
        return model.model_dump(mode="json")

    def encode(self, model: SchemeModel) -> JsonObject:
        """校验提供者结果。"""
        if self.codec is not None:
            if not isinstance(model, CodecArguments):
                raise ValueError("codec 提供者必须返回 CodecArguments")
            return self.codec.encode(model.value)
        assert self.output_model is not None
        return self.output_model.model_validate(
            model.model_dump(mode="json"), strict=True
        ).model_dump(mode="json")

    def schema(self, *, output: bool = False) -> dict[str, Any]:
        """导出该动作唯一作者的 schema。"""
        if self.codec is not None:
            return self.codec.output_schema if output else self.codec.input_schema
        model = self.output_model if output else self.input_model
        assert model is not None
        return model.model_json_schema()


@dataclass(frozen=True, kw_only=True)
class SchemeSpec:
    """一种资源种类下的确定契约；本地模型是动作 schema 的唯一来源。"""

    kind: str
    contract: str
    version: str
    actions: tuple[ActionSpec, ...]
    digest: str = field(init=False)
    _descriptor_json: str = field(init=False, repr=False, compare=False)

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
        descriptor = ContractDescriptor(
            kind=self.kind,
            contract=self.contract,
            version=self.version,
            actions=[
                ActionDescriptor(
                    name=action.name,
                    input_schema=action.schema(),
                    output_schema=action.schema(output=True),
                    mutating=action.mutating,
                    cancellable=action.cancellable,
                    description=action.description,
                )
                for action in sorted(self.actions, key=lambda item: item.name)
            ],
        )
        object.__setattr__(self, "digest", descriptor.fingerprint())
        object.__setattr__(self, "_descriptor_json", descriptor.model_dump_json(by_alias=True))

    def descriptor(self) -> ContractDescriptor:
        """单向导出完整契约的隔离副本，仅供受信发布和受控投影使用。"""
        return ContractDescriptor.model_validate_json(self._descriptor_json)

    def action(self, name: str) -> ActionSpec:
        """只解析已登记动作，不进行任意属性查找。"""
        for action in self.actions:
            if action.name == name:
                return action
        raise SchemeError(ErrorCode.UNKNOWN_ACTION)


@dataclass(frozen=True)
class ProviderCall:
    """提供者收到的隔离输入，不包含运行时或管理端口。"""

    principal: Requester
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


class ProviderLifecycle(Protocol):
    """唯一执行体 owner 的准备与关闭端口，可被多个逻辑导出共享。"""

    async def prepare(self) -> None:
        """完成握手及资源准备，异常由 owner 保留；不得自行发布就绪。"""
        ...

    async def aclose(self) -> None:
        """释放本 owner 的资源；允许显式重试，完成不冒充外部效果回滚。"""
        ...
