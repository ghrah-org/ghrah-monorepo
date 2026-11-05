# SPDX-License-Identifier: Apache-2.0
"""能力后端的提供者槽契约名：版本、登记与接纳共用的唯一字符串来源。

本模块是叶子依赖（不 import 能力访问层，也不被文件后端的实现摘要覆盖），
放在这里可以避免改动 ``contracts.py`` 触发所有文件后端的版本号变化。
"""

FS_CONTRACT = "ghrah.fs.v1"
SHELL_CONTRACT = "ghrah.shell.v1"
CLUSTER_CONTRACT = "ghrah.cluster.v1"
CORE_CONTRACT = "ghrah.core.v1"
MCP_CONTRACT = "ghrah.mcp.v1"
