# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""加载主题：声明文件的读取、解析与视图投影。

对外只产出「可展示的目录」与「可注入模型工具面的形状」，不执行任何能力。
定义来源是 ``ability/<容器>/ability.yaml``（``ability_schema`` 为其契约）；
本包把定义面 + 实现面投影成 ``ability_view``。
"""

from __future__ import annotations
