# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""执行主题：调用对象、服务表、Hook 控制流与能力实例化。

模型触发 function call 后，从这里走到对应实现脚本。执行上下文的形状固定为
``execute(call: OperationCall, services: ExecutionServices)``：调用对象只带
成员、参数、``tool_call_id`` 与受信身份，其余依赖都在服务表里。
"""

from __future__ import annotations
