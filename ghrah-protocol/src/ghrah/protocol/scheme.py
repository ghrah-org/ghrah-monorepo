# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""Scheme 公共描述格式；动作内容由领域的唯一作者模型单向导出。"""

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DescriptorModel(BaseModel):
    """严格的可携带描述值；嵌套数据由发送方复制。"""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=True)


class ActionDescriptor(DescriptorModel):
    """一个动作的完整契约，不包含主体授权事实。"""

    name: str
    input_schema: dict[str, Any] = Field(validation_alias="input", serialization_alias="input")
    output_schema: dict[str, Any] = Field(validation_alias="output", serialization_alias="output")
    mutating: bool
    cancellable: bool
    description: str


class ContractDescriptor(DescriptorModel):
    """确定版本的完整契约；精确指纹不等于语义兼容或来源认证。"""

    format: Literal["ghrah.scheme.local-contract.v1"] = "ghrah.scheme.local-contract.v1"
    kind: str
    contract: str
    version: str
    actions: list[ActionDescriptor]

    @model_validator(mode="after")
    def unique_actions(self) -> "ContractDescriptor":
        """同一契约不得重复声明动作。"""
        if len({item.name for item in self.actions}) != len(self.actions):
            raise ValueError("契约动作不能重复")
        return self

    def fingerprint(self) -> str:
        """计算精确内容摘要；仅排序动作和对象键，保留 schema 数组顺序。"""
        data = self.model_dump(mode="json", by_alias=True)
        data["actions"] = sorted(data["actions"], key=lambda item: item["name"])
        encoded = json.dumps(
            data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


class DiscoveredAction(DescriptorModel):
    """按主体许可裁剪的动作；能发现不代表能读 schema 或执行。"""

    name: str
    description: str
    mutating: bool
    cancellable: bool
    invokable: bool
    input_schema: dict[str, Any] | None
    output_schema: dict[str, Any] | None


class DiscoveredResource(DescriptorModel):
    """一个可见局部绑定的只读快照，不含隐藏成员计数。"""

    kind: str
    alias: str
    contract: str
    contract_version: str
    contract_digest: str
    binding_revision: int
    available: bool
    actions: list[DiscoveredAction]


class CatalogSnapshot(DescriptorModel):
    """有界的可见目录；不返回未过滤总数或基于隐藏项的分页游标。"""

    format: Literal["ghrah.scheme.catalog.v1"] = "ghrah.scheme.catalog.v1"
    entries: list[DiscoveredResource]
