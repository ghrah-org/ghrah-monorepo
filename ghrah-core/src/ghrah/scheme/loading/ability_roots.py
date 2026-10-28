# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""能力目录池：扫描根在哪里，以及各自多高优先级。

目录池的口径（对齐 DSH 的 skill 目录池）：池中每一项是
「定位器 + rank + source」，``rank`` 越小优先级越高，同一个容器名由优先级高者胜出，
被忽略者记为扫描问题（可见，不静默）。

与 skill 目录池唯一的差别是**定位器类型**：

- **内置能力树**随包分发，取 ``importlib.resources`` 句柄——普通安装下是目录，
  zip/egg 安装下不是 ``Path``；
- **用户能力目录**是真实 ``Path``。

两者都实现 ``importlib.resources.abc.Traversable``（``iterdir`` / ``joinpath`` /
``is_file`` / ``read_text``），所以池里的 ``locator`` 统一取该协议，扫描器不必
区分"这是包内还是盘上"。

本模块是「能力包在哪里」的唯一权威：定义扫描、目录面报告、调试脚本都从这里取根，
不允许再各自拼路径（拼第二份就是第二权威）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

__all__ = [
    "BUILTIN_PACKAGE",
    "BUILTIN_ROOT_RANK",
    "BUILTIN_ROOT_SOURCE",
    "USER_ROOT_RANK",
    "USER_ROOT_SOURCE",
    "AbilityRoot",
    "builtin_pool",
    "builtin_root",
    "default_pool",
    "default_user_root",
    "ensure_roots",
    "path_root",
    "user_root",
]

BUILTIN_PACKAGE = "ghrah.scheme.ability"
"""内置能力树所在的包：``ghrah-core/src/ghrah/scheme/ability/``。"""

BUILTIN_ROOT_RANK: int = 0
"""内置能力树的优先级：最高。

系统能力是运行时底座（``fs`` / ``core`` / ``cluster`` / ``shell`` / ``mcp``），
用户包**不允许**同名覆盖——否则一份外部 ``fs/ability.yaml`` 就能改掉文件系统
能力的语义。
"""

BUILTIN_ROOT_SOURCE: str = "builtin"
"""内置能力树的来源标签。"""

USER_ROOT_RANK: int = 100
"""用户能力目录的优先级：低于内置树，与 skill 目录池的默认 rank 一致。"""

USER_ROOT_SOURCE: str = "skill-ability"
"""用户能力目录的来源标签（也是目录名 ``~/.ghrah/skill-ability``）。"""


@dataclass(frozen=True)
class AbilityRoot:
    """目录池中的一项。

    ``rank`` 越小优先级越高；``source`` 写进扫描结果与报告，供调用方说明来源；
    ``required`` 声明「这个根缺失算不算问题」——内置树缺失是硬故障，用户目录
    尚未创建是正常状态（首次安装还没装任何能力包），不该每轮扫描都报一条问题。
    """

    locator: Traversable
    rank: int
    source: str
    required: bool = False

    @property
    def label(self) -> str:
        """根的可读定位串（报错、报告、控制台展示用）。

        Returns:
            真实目录给出绝对路径；包资源给出 ``package:<包名>``。
        """
        if isinstance(self.locator, Path):
            return str(self.locator)
        return f"package:{BUILTIN_PACKAGE}"

    @property
    def writable(self) -> bool:
        """该根是否可写盘（ability.lock.yaml 之类只能落在真实目录里）。

        Returns:
            真实路径为 True；包资源为 False。
        """
        return isinstance(self.locator, Path)

    @property
    def path(self) -> Path | None:
        """根的真实目录；包资源（可能在 zip 里、不可写）为 None。

        外部代码常以路径形式使用目录池（``pool[0].path``），这里给统一出口，
        避免各处重复 ``isinstance`` 判断。

        Returns:
            真实路径或 None。
        """
        return self.locator if isinstance(self.locator, Path) else None


def builtin_root() -> AbilityRoot:
    """内置能力树（随包分发、只读、最高优先级）。

    Returns:
        定位到 ``ghrah.scheme.ability`` 包的根；``required=True``。
    """
    return AbilityRoot(
        locator=resources.files(BUILTIN_PACKAGE),
        rank=BUILTIN_ROOT_RANK,
        source=BUILTIN_ROOT_SOURCE,
        required=True,
    )


def default_user_root() -> Path:
    """默认用户能力目录：``~/.ghrah/skill-ability``。

    路径只在这里定义一次（``ghrah.skills.roots`` 转调本函数，skill 面与能力面
    看的是同一批包）。

    Returns:
        用户能力目录路径（不保证存在）。
    """
    return Path.home() / ".ghrah" / USER_ROOT_SOURCE


def user_root(root: str | Path | None = None) -> AbilityRoot:
    """用户能力目录（可覆盖默认位置）。

    用在配置驱动根路径的场景（如 subject 的 ``SkillConfig.root``）：把配置值直接
    传进来，``rank`` / ``source`` 语义仍由本模块统一决定。

    Args:
        root: 覆盖默认目录；None 取 :func:`default_user_root`。

    Returns:
        用户目录根；``required=False``（不存在属正常状态）。
    """
    return AbilityRoot(
        locator=Path(root) if root is not None else default_user_root(),
        rank=USER_ROOT_RANK,
        source=USER_ROOT_SOURCE,
    )


def path_root(
    path: str | Path,
    *,
    rank: int = USER_ROOT_RANK,
    source: str = USER_ROOT_SOURCE,
    required: bool = False,
) -> AbilityRoot:
    """由任意目录构造一个池项（测试、项目内目录、额外安装目录）。

    Args:
        path: 目录路径。
        rank: 优先级，越小越优先。
        source: 来源标签。
        required: 缺失是否算扫描问题。

    Returns:
        新的池项。
    """
    return AbilityRoot(locator=Path(path), rank=rank, source=source, required=required)


def builtin_pool() -> tuple[AbilityRoot, ...]:
    """只含内置树的池：**实现面**（投影 view、handler 解析）用它。

    Returns:
        单元素元组。
    """
    return (builtin_root(),)


def default_pool() -> tuple[AbilityRoot, ...]:
    """默认池：内置树（rank 0）→ 用户目录（rank 100）。

    **目录面**（外部展示、装配体检、调试脚本）用它——用户装了什么就能看到什么。

    Returns:
        按优先级升序的池。
    """
    return (builtin_root(), user_root())


def ensure_roots(pool: Sequence[AbilityRoot]) -> tuple[Path, ...]:
    """确保池中每个**可写**根存在（缺失则逐级创建），幂等。

    包资源根不可建，直接跳过——它的存在性由安装决定，不是运行期能补的。

    Args:
        pool: 目录池。

    Returns:
        已确保存在的目录，顺序与入参一致。
    """
    ensured: list[Path] = []
    for root in pool:
        if not isinstance(root.locator, Path):
            continue
        root.locator.mkdir(parents=True, exist_ok=True)
        ensured.append(root.locator)
    return tuple(ensured)
