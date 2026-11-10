# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""宿主预先核定的启动记录；运行期只能选择记录，不能提交命令。"""

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from .context import CallerBinding


@dataclass(frozen=True, slots=True)
class LaunchRecord:
    """固定入口、参数、环境及连接绑定；配置只由受信宿主读取。"""

    key: str
    kind: str
    module: str
    binding: CallerBinding
    arguments: tuple[str, ...] = ()
    variant: str = "normal"
    executable: str = sys.executable
    environment: tuple[tuple[str, str], ...] = ()
    configuration: str = "{}"

    def __post_init__(self) -> None:
        if not all(isinstance(x, str) and x for x in (self.key, self.kind, self.variant)):
            raise ValueError("启动记录的键、种类与变体必须非空")
        if not isinstance(self.module, str) or not re.fullmatch(
            r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*", self.module
        ):
            raise ValueError("启动入口必须是固定 Python 模块名")
        if not isinstance(self.executable, str) or not Path(self.executable).is_absolute():
            raise ValueError("启动解释器必须为绝对路径")
        if not isinstance(self.binding, CallerBinding):
            raise ValueError("启动记录缺少受信连接绑定")
        if not isinstance(self.arguments, tuple) or not all(
            isinstance(x, str) and "\x00" not in x for x in self.arguments
        ):
            raise ValueError("固定参数必须是字符串元组")
        reserved = ("--provider-fd", "--caller-fd", "--credential", "--enrollment", "--config-json")
        if any(arg.split("=", 1)[0] in reserved for arg in self.arguments):
            raise ValueError("固定参数不能覆盖 Kernel 注入的连接参数")
        if not isinstance(self.environment, tuple) or any(
            not isinstance(pair, tuple)
            or len(pair) != 2
            or not all(isinstance(x, str) and "\x00" not in x for x in pair)
            or not pair[0]
            or "=" in pair[0]
            for pair in self.environment
        ):
            raise ValueError("固定环境必须是键值对元组")
        if len(dict(self.environment)) != len(self.environment):
            raise ValueError("固定环境变量不能重复")
        if not isinstance(self.configuration, str) or len(self.configuration.encode()) > 65536:
            raise ValueError("受信服务配置超过大小限制")
        if not isinstance(json.loads(self.configuration), dict):
            raise ValueError("受信服务配置必须为 JSON 对象")


@dataclass(frozen=True, slots=True)
class LaunchCatalog:
    """不可变启动清单；空清单不允许创建任何服务。"""

    records: tuple[LaunchRecord, ...] = ()
    _entries: MappingProxyType = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.records, tuple) or not all(
            isinstance(x, LaunchRecord) for x in self.records
        ):
            raise ValueError("启动清单必须由不可变记录组成")
        entries = {(record.key, record.variant): record for record in self.records}
        if len(entries) != len(self.records):
            raise ValueError("启动清单的键与变体不能重复")
        object.__setattr__(self, "_entries", MappingProxyType(entries))

    def select(self, key: str, kind: str, variant: str = "normal") -> LaunchRecord:
        """只选择已声明的记录；种类与变体不隐式产生启动权限。"""
        record = self._entries.get((key, variant))
        if record is None or record.kind != kind:
            raise ValueError("启动入口、种类或变体不在受信清单中")
        return record
