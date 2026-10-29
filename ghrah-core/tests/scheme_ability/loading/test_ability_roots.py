# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""能力目录池（``ability_roots``）测试。

这一层回答的是「能力包从哪几个路径找、谁优先」：内置树 rank 0（不可被同名覆盖），
用户目录 rank 100（尚未创建属正常状态）。
"""

from __future__ import annotations

from pathlib import Path

from ghrah.scheme_ability.loading import ability_roots as roots


class TestDefaults:
    def test_default_pool_is_builtin_then_user(self) -> None:
        """默认池两项：内置树在前，用户目录在后（优先级升序）。"""
        pool = roots.default_pool()

        assert [root.source for root in pool] == ["builtin", "skill-ability"]
        assert [root.rank for root in pool] == sorted(root.rank for root in pool)

    def test_builtin_root_outranks_user_root(self) -> None:
        """内置树 rank 更小，因此用户包无法同名覆盖系统能力。"""
        assert roots.BUILTIN_ROOT_RANK < roots.USER_ROOT_RANK

    def test_builtin_root_is_required(self) -> None:
        """内置树缺失是硬故障：它是随包分发的源码。"""
        root = roots.builtin_root()

        assert root.required is True
        assert root.source == "builtin"

    def test_user_root_is_optional(self) -> None:
        """用户目录缺失不是问题：首次安装还没装任何能力包。"""
        assert roots.user_root().required is False

    def test_default_user_root_is_the_skill_directory(self) -> None:
        """用户根就是历史上放技能包的目录（路径只定义一处）。"""
        assert roots.default_user_root() == Path.home() / ".ghrah" / "skill-ability"

    def test_builtin_pool_has_only_builtin(self) -> None:
        """实现面的池只有内置树：外部包没有实现脚本可 import。"""
        pool = roots.builtin_pool()

        assert len(pool) == 1
        assert pool[0].source == "builtin"

    def test_user_root_accepts_override(self, tmp_path: Path) -> None:
        """配置驱动的根路径经 ``user_root`` 进池，rank / source 语义不变。"""
        root = roots.user_root(tmp_path)

        assert Path(root.locator) == tmp_path
        assert root.rank == roots.USER_ROOT_RANK
        assert root.source == roots.USER_ROOT_SOURCE


class TestPathRoot:
    def test_defaults(self, tmp_path: Path) -> None:
        """``path_root`` 缺省用用户级 rank / source，且非必需。"""
        root = roots.path_root(tmp_path)

        assert root.rank == roots.USER_ROOT_RANK
        assert root.source == roots.USER_ROOT_SOURCE
        assert root.required is False

    def test_overrides(self, tmp_path: Path) -> None:
        """三个语义字段都可覆盖。"""
        root = roots.path_root(tmp_path, rank=5, source="project", required=True)

        assert (root.rank, root.source, root.required) == (5, "project", True)

    def test_real_path_root_is_writable(self, tmp_path: Path) -> None:
        """真实目录可写盘（lock 之类只能落在真实目录里）。"""
        assert roots.path_root(tmp_path).writable is True

    def test_label_is_absolute_path(self, tmp_path: Path) -> None:
        """真实目录的标签就是路径，便于报错与排查。"""
        assert roots.path_root(tmp_path).label == str(tmp_path)

    def test_folder_name_is_not_part_of_identity(self) -> None:
        """池项不解析身份：身份来自 ``ability.yaml``，目录名只作定位。"""
        root = roots.path_root("some/where", rank=7, source="x")

        assert root.rank == 7
        assert Path(root.locator) == Path("some/where")


class TestEnsureRoots:
    def test_creates_missing_directories(self, tmp_path: Path) -> None:
        """缺失目录被逐级创建。"""
        target = tmp_path / "nested" / "abilities"
        pool = (roots.path_root(target),)

        ensured = roots.ensure_roots(pool)

        assert ensured == (target,)
        assert target.is_dir()

    def test_is_idempotent(self, tmp_path: Path) -> None:
        """重复调用不报错，已有内容不被动。"""
        keep = tmp_path / "keep.yaml"
        keep.write_text("x", encoding="utf-8", newline="\n")

        roots.ensure_roots((roots.path_root(tmp_path),))
        roots.ensure_roots((roots.path_root(tmp_path),))

        assert keep.read_text(encoding="utf-8") == "x"

    def test_order_follows_pool(self, tmp_path: Path) -> None:
        """返回顺序与入参一致。"""
        first = tmp_path / "a"
        second = tmp_path / "b"

        ensured = roots.ensure_roots((roots.path_root(second), roots.path_root(first)))

        assert ensured == (second, first)

    def test_skips_package_resource_roots(self, tmp_path: Path) -> None:
        """包资源根不建目录：它的存在性由安装决定。

        用鸭子类型的假包资源根，不去碰真实的 ``~/.ghrah``。
        """
        package_root = roots.AbilityRoot(
            locator=object(),  # type: ignore[arg-type]
            rank=roots.BUILTIN_ROOT_RANK,
            source=roots.BUILTIN_ROOT_SOURCE,
            required=True,
        )

        ensured = roots.ensure_roots((package_root, roots.path_root(tmp_path)))

        assert ensured == (tmp_path,)
