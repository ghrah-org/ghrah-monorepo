# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""pytest 全局配置。

Ouroboros 合并装配层（1787622968763）实施后隔离清单清零：
旧基建（SubjectEngine 装配/cluster_transport/transport 三件套）与
四件套（ability_runner/hitl_policy/hitl_notary/persistence）测试
随源码删除；third_party_unit 重写为 ctx 挂载形态。无已知失败豁免。

── Project Root / skill 根目录物化路径测试隔离（2026-08-28 幽灵目录治理）──

事故背景：``SubjectConfig.project.default_root_locator_template`` 默认
``~/.ghrah/projects/{project_id}`` 指向真实 home。测试构造 SubjectConfig
时若漏配模板（只覆写 db_path/workspace_root/manifest_root），凡走到
project_create / bootstrap / migrate 的物化路径即写真实 ~/.ghrah/；
叠加创建事务回滚只删空目录的补偿缺陷，累积出 392 个幽灵项目目录。

双层堵漏（缺一不可）：
1. env 覆写 —— 覆盖 ``SubjectConfig.from_env()`` 路径；
2. dataclass 默认值覆写 —— 覆盖直接构造 ``SubjectConfig()`` /
   ``ProjectConfig(...)`` 路径（dataclass 默认值
   烘焙在生成的 ``__init__`` 签名里，不读环境变量）。

显式传入路径的测试一律尊重、不被覆写。新增 SubjectConfig 路径类
字段时在此同步追加，勿逐测试补参数（判据见 skill 陷阱 31）。
"""

from __future__ import annotations

import os
import tempfile

# pytest 会话临时目录（系统 temp 随平台策略清理；project_id 为 UUID，并发会话无冲突）。
# tempfile.gettempdir()：POSIX 上即 /tmp（与旧字面量逐字节一致，零回归）；
# Windows 上为 %LOCALAPPDATA%\Temp 绝对路径——硬编码 "/tmp" 在 Windows 语义下
# is_absolute()=False，会被 canonical_file_locator 拒绝（勿重新引入同类硬编码）。
_TEST_PROJECTS_TEMPLATE = os.path.join(tempfile.gettempdir(), "ghrah-test-projects", "{project_id}")

# 第 1 层：from_env() 路径。注意 test_config_split 显式 delenv 后断言
# 生产默认值，不受本覆写影响（from_env 的回退字面量独立于此）。
os.environ.setdefault(
    "GHRAH_SUBJECT_PROJECT_DEFAULT_ROOT_LOCATOR_TEMPLATE",
    _TEST_PROJECTS_TEMPLATE,
)

from ghrah.subject.config import ProjectConfig  # noqa: E402

# 第 2 层：dataclass 直接构造路径。ProjectConfig 仅两个字段
# （bootstrap_workspace_locator, default_root_locator_template），
# 位置传参 < 2 个即视为模板未显式提供。
_real_project_config_init = ProjectConfig.__init__


def _isolated_project_config_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
    if "default_root_locator_template" not in kwargs and len(args) < 2:
        kwargs["default_root_locator_template"] = _TEST_PROJECTS_TEMPLATE
    _real_project_config_init(self, *args, **kwargs)


ProjectConfig.__init__ = _isolated_project_config_init  # type: ignore[method-assign]

collect_ignore: list[str] = []
