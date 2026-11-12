# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""跨模块异常族的共同基类。

放在契约包里是硬要求：消费方要 ``except`` 它，但又不能 import 提供方的模块。
各模块可以派生子类（``CoreApiError`` 等）做精确定位，但**基类必须共享**。
"""

from __future__ import annotations

from ghrah.protocol.problem import Problem

__all__ = ["ApiError"]


class ApiError(Exception):
    """端口调用失败。

    Attributes:
        problem: 可 JSON 往返的错误载荷
    """

    def __init__(self, problem: Problem) -> None:
        super().__init__(f"{problem.code}: {problem.detail}")
        self.problem = problem

    @property
    def code(self) -> str:
        """机器可读错误码（便捷访问）。"""
        return str(self.problem.code)
