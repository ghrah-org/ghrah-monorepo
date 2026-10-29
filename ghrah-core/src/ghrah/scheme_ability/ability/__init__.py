# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Ability scheme：系统级能力容器的定义面与实现面。

本目录**只放能力包本身**，加载器与格式模型在上一层平铺
（``ghrah.scheme_ability.loading.ability_catalog`` /
``ghrah.scheme_ability.loading.ability_schema``）。

目录形状（一个容器 = 一个目录）::

    ability/
      __init__.py            包标记（目录扫描与 import 需要）
      <container>/
        __init__.py
        ability.yaml         定义面：ghrah.ability-definition 描述符
        operations/
          __init__.py
          <key>.py           实现面：该容器的一个 Operation 的实现脚本

容器按现有 namespace 归并：``fs`` / ``core`` / ``cluster`` / ``shell``。
``identity.namespace`` 统一为 ``ghrah``，``identity.name`` 即容器名，因此容器
全名仍是 ``ghrah.fs``、``ghrah.cluster`` 等；成员键是原来的 ability 名
（``read_file``、``execute_command`` …），成员地址为
``/scheme/ability/<容器>/operations/<成员键>``。

两面各自的权威：

- **定义面**（``ability.yaml``）承载契约事实：容器身份、提供者槽要求、
  每个 Operation 的输入／输出 schema、错误、副作用、执行约束。它不含授权
  结果（授权归 GrantRecord）也不含 Hook 编排。
- **实现面**（``operations/*.py``）承载实现与随实现的运行时声明：
  模块级 ``PERMISSIONS`` / ``HOOKS`` / ``SUMMARY`` / ``TAGS`` / ``TOOL``
  常量，分别对应旧 manifest 的 ``metadata.permissions``、``hooks``、
  ``metadata.description``、``metadata.tags`` 与 ``tool`` 有无。这些字段在
  定义格式里暂无位置，故暂存实现侧，由 loader 读回。

运行时权威：``ghrah.scheme_ability.loading.ability_catalog`` 把上面两面投影成
``AbilityManifest``（可重建的 view），由 ``ghrah.scheme_ability.loading`` 投影而来。
"""

from __future__ import annotations

__all__: list[str] = []
