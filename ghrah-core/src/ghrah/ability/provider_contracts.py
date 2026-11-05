# SPDX-License-Identifier: Apache-2.0
"""能力后端的提供者槽契约名：版本、登记与接纳共用的唯一字符串来源。

本模块是叶子依赖（不 import 能力访问层，也不被文件后端的实现摘要覆盖），
放在这里可以避免改动 ``contracts.py`` 触发所有文件后端的版本号变化。

v1 索引不再声明 ``provider_requirements``：成员的提供者契约由**包来源**决定。
内置包按身份 ``ghrah.<name>`` 映射到各自后端契约；MCP 快照由 ``tools/list``
生成，固定走 MCP 契约；外部 Python 源包固定走函数入口契约，运行宿主还须
显式接线安装包 Provider；未知来源保持 fail-closed。
"""

from __future__ import annotations

from types import MappingProxyType

FS_CONTRACT = "ghrah.fs.v1"
SHELL_CONTRACT = "ghrah.shell.v1"
CLUSTER_CONTRACT = "ghrah.cluster.v1"
CORE_CONTRACT = "ghrah.core.v1"
MCP_CONTRACT = "ghrah.mcp.v1"
PYTHON_CONTRACT = "ghrah.operation.python.v1"

BUILTIN_CONTRACTS = MappingProxyType(
    {
        "fs": FS_CONTRACT,
        "shell": SHELL_CONTRACT,
        "cluster": CLUSTER_CONTRACT,
        "core": CORE_CONTRACT,
        "mcp": MCP_CONTRACT,
    }
)
"""内置包名 → 提供者契约。``ghrah`` 命名空间只保留给内置包（生成/安装期拒绝外部占用）。"""


def contract_for(origin: str, namespace: str, name: str | None) -> str | None:
    """按包来源与身份解析成员的提供者契约。

    Args:
        origin: ``ability.yaml`` 的 ``origin``（``source`` / ``mcp``）。
        namespace: ``identity.namespace``。
        name: ``identity.name``；可缺省。

    Returns:
        后端契约名；未登记的内置包或未知来源返回 None（调用方 fail-closed）。
    """
    if namespace == "ghrah" and name is not None:
        return BUILTIN_CONTRACTS.get(name)
    if origin == "mcp":
        return MCP_CONTRACT
    if origin == "source" and namespace not in {"ghrah", "mcp"}:
        return PYTHON_CONTRACT
    return None
