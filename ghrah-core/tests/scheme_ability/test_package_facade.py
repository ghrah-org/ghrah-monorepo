# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""``ghrah.scheme_ability`` 顶层面包的门面契约。

钉住三件事，防止重构时退化成「导入即拉起整棵子模块树」或「索引表与实现漂移」：

1. ``import ghrah.scheme_ability`` 不加载任何子模块（惰性索引的前提）；
2. ``__all__`` 里每个名字都能解析，且**解析到的对象就是归属模块里那个对象**；
3. 索引之外的名字抛 :class:`AttributeError`，而非静默返回 None。
"""

from __future__ import annotations

import subprocess
import sys

import pytest

import ghrah.scheme_ability as scheme

_LAZY_PROBE = """\
import sys
import ghrah.scheme_ability
loaded = sorted(n for n in sys.modules if n.startswith("ghrah.scheme_ability."))
print(len(loaded))
"""


def test_import_loads_no_submodules() -> None:
    """导入顶层面包不应触发任何子模块导入。

    场景：外部框架只 ``import ghrah.scheme_ability`` 做能力名探测时，不该为它拉起
    执行器、Hook 全套依赖。用子进程测量——当前测试进程早已加载过子模块。
    """
    result = subprocess.run(
        [sys.executable, "-c", _LAZY_PROBE],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "0", f"导入期意外加载了子模块：{result.stdout}"


@pytest.mark.parametrize("name", scheme.__all__)
def test_public_name_resolves_from_its_home_module(name: str) -> None:
    """``__all__`` 的每个名字都要能取到，且与归属模块中的对象是同一个。

    场景：新增或搬移能力 API 后，索引表若指错模块，这里会立刻报出来——
    它比对的是 ``is`` 而不是「能取到就行」，避免同名再导出造成两份定义。
    """
    value = getattr(scheme, name)
    suffix = scheme._MODULE_OF[name]
    home = __import__(f"ghrah.scheme_ability.{suffix}", fromlist=[name])
    assert value is getattr(home, name)


def test_unknown_name_raises_attribute_error() -> None:
    """索引外的名字必须抛 AttributeError，让拼错立刻暴露。"""
    with pytest.raises(AttributeError, match="has no attribute 'NoSuchAbility'"):
        _ = scheme.NoSuchAbility


def test_subpackages_are_reachable_as_attributes() -> None:
    """四个主题子包与包根单文件模块可通过属性直接点到。"""
    for name in (*scheme._SUBPACKAGES, *scheme._PLAIN_MODULES):
        module = getattr(scheme, name)
        assert module.__name__ == f"ghrah.scheme_ability.{name}"
