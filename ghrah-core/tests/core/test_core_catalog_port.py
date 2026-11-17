# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""全局目录端口（``CoreCatalogPort``）：无作用域也能用。

覆盖：零项目/零作用域下包目录、包分组（distribution 视图）与 aclose 幂等；
以及目录端口不会打开任何数据库（用不存在的路径构造也不报错）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ghrah.protocol.errors import ApiError
from ghrah.protocol.problem import ProblemCode
from ghrah.protocol.shell_allow import ShellAllowValidationRequest

from ghrah.core.wiring import build_core_catalog


async def test_catalog_works_without_scope() -> None:
    catalog = build_core_catalog()
    try:
        packages = await catalog.packages()
        assert packages.packages

        groups = await catalog.package_groups()
        # 全局视图：只按发行版对齐，不挂项目/实例事实
        assert groups.scope == "distribution"
        assert groups.project_id is None

        await catalog.aclose()
    finally:
        await catalog.aclose()


async def test_catalog_ignores_missing_database(tmp_path: Path) -> None:
    # 目录端口不打开数据库：给一个不存在的安装根也应返回「不可用」而不是抛错
    catalog = build_core_catalog(install_root=str(tmp_path / "missing-install-root"))
    try:
        packages = await catalog.packages()
        assert packages.installed_available is False
        groups = await catalog.package_groups()
        assert groups.scope == "distribution"
    finally:
        await catalog.aclose()


@pytest.mark.parametrize(
    ("kind", "key"),
    [
        ("exact", "python demo.py --name 'a b'"),
        ("class", "pnpm:script:lint"),
        ("class", "uv:run:mypy"),
    ],
)
async def test_shell_validation_without_project_or_agent(kind, key) -> None:
    """通过真正的端口、请求校验和处理器校验记忆，无需创建 Agent 或项目。"""
    catalog = build_core_catalog()
    try:
        await catalog.validate_shell_allow(request=ShellAllowValidationRequest(kind=kind, key=key))
        rules = await catalog.shell_builtin_rules()
        assert [rule.key for rule in rules.rules] == [
            "uv:run:pytest",
            "uv:run:ruff",
            "pnpm:script:test",
        ]
        assert all(rule.readonly for rule in rules.rules)
    finally:
        await catalog.aclose()


@pytest.mark.parametrize(
    ("kind", "key"),
    [
        ("exact", "rm -rf /tmp/x"),
        ("exact", "python demo.py > /etc/passwd"),
        ("exact", "pnpm test"),
        ("class", "uv:run:rm"),
        ("class", "uv:run:python"),
        ("class", "pnpm:script:exec"),
        ("class", "pnpm:script:lint;rm -rf /"),
        ("class", "arbitrary"),
    ],
)
async def test_shell_validation_rejects_with_public_error(kind, key) -> None:
    """非法、危险和无需存储的内置规则均通过公开错误契约返回。"""
    catalog = build_core_catalog()
    try:
        with pytest.raises(ApiError) as caught:
            await catalog.validate_shell_allow(
                request=ShellAllowValidationRequest(kind=kind, key=key)
            )
        assert caught.value.problem.code == ProblemCode.INVALID_ARGUMENT
        assert caught.value.problem.capability == "shell.validate_allow"
    finally:
        await catalog.aclose()
