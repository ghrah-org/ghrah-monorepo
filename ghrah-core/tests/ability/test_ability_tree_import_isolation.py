# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""内置能力树在缺失 MCP SDK 时仍可装配。

装配期会 import 全部内置实现模块（内置树投影需要实现类），因此任何一个实现
模块在模块级 import 可选依赖，都会把「装不上 SDK」放大成「整个新模式起不来」。
本用例在子进程里屏蔽 ``mcp`` 包，验证：

1. 内置树能完成投影与实例化；
2. ``mcp_call`` 在运行时缺席时给出明确的「未接线」失败，而不是 ImportError。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_CODE = """
import asyncio
import sys

sys.modules["mcp"] = None  # 任何 `import mcp` 都会立即失败

from ghrah.ability.execution.execution_services import ExecutionServices
from ghrah.ability.execution.invocation import OperationCall
from ghrah.ability.source import BuiltinAbilitySource

source = BuiltinAbilitySource()
assert "ghrah.mcp.mcp_call" in source.list_abilities()

operation = source.create_operation("ghrah.mcp.mcp_call")
result = asyncio.run(
    operation.execute(
        OperationCall(arguments={"server": "local", "tool": "echo"}),
        ExecutionServices(),
    )
)
assert result.outcome.value == "failure", result
assert "not wired" in result.data["error"], result.data
print("ok")
"""


def test_builtin_tree_assembles_without_mcp_sdk() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _CODE],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
