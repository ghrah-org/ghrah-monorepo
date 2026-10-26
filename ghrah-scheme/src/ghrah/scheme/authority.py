# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""注入领域核定端口，不在控制平面维护第二份 grant 表。"""

from typing import Protocol

from ghrah.scheme.models import AccessRequest, Admission, Authorization


class Authority(Protocol):
    """Core 等领域 owner 的授权和接纳端口。"""

    async def authorize(self, request: AccessRequest) -> Authorization:
        """核定取得引用或结果正文的当前许可。"""
        ...

    async def admit(self, request: AccessRequest) -> Admission:
        """核定并提交必要接纳；由此端口定义撤销先后顺序。"""
        ...


class DenyAuthority:
    """没有显式接入领域核定时拒绝所有访问。"""

    async def authorize(self, request: AccessRequest) -> Authorization:
        """拒绝取得或读取结果。"""
        return Authorization(False)

    async def admit(self, request: AccessRequest) -> Admission:
        """拒绝业务接纳。"""
        return Admission(False)
