# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""脚本地址、授予与包内文件的起进程前门禁。

这一层不创建子进程。它只回答三件事：模型给的地址是不是已登记的脚本成员、
当前 run 有没有执行权、解析出的文件是不是安装根里的普通 ``.py``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ghrah.ability.access.store import AbilityAccessStore, AccessIdentity, ScriptGrant
from ghrah.ability.content import digest_bytes, relative_path
from ghrah.ability.loading.ability_roots import builtin_root
from ghrah.ability.package.installed_store import is_link
from ghrah.ability.source import BuiltinAbilitySource

__all__ = [
    "ScriptRejectionError",
    "ScriptTarget",
    "parse_model_address",
    "prepare_script",
]

_MODEL_ADDRESS = re.compile(
    r"^skills/(?P<skill>[a-z0-9]+(?:-[a-z0-9]+)*)/scripts/(?P<key>[a-z0-9][a-z0-9._-]*)$"
)


class ScriptRejectionError(Exception):
    """起进程前就能确定的拒绝。``kind`` 使用冻结的脚本错误种类。"""

    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ScriptTarget:
    """已经通过地址、授权和文件核对的执行目标。"""

    model_address: str
    grant: ScriptGrant
    package_root: Path
    script_path: Path
    workspace: Path


def parse_model_address(script: object) -> tuple[str, str]:
    """把模型地址拆成 skill 名和脚本键。

    Args:
        script: ``run_script`` 的 ``script`` 参数。只接受
            ``skills/<skill>/scripts/<key>``，不接受磁盘路径。

    Returns:
        ``(skill, key)``。

    Raises:
        ScriptRejectionError: 绝对路径或 ``..`` 为 ``permission_denied``；
            其余对不上的地址为 ``not_found``。
    """
    if not isinstance(script, str) or not script:
        raise ScriptRejectionError("not_found", "没有已获授的脚本")
    if _out_of_bounds(script):
        raise ScriptRejectionError("permission_denied", "脚本地址越出已登记范围")
    match = _MODEL_ADDRESS.fullmatch(script)
    if match is None:
        raise ScriptRejectionError("not_found", "没有已获授的脚本")
    return match.group("skill"), match.group("key")


def _out_of_bounds(script: str) -> bool:
    """绝对路径、盘符和 ``.`` / ``..`` 段都是越界，不是“没找到”。"""
    if script.startswith(("/", "\\")) or script.startswith("//"):
        return True
    if len(script) >= 2 and script[1] == ":":
        return True
    return any(part in {"", ".", ".."} for part in script.replace("\\", "/").split("/"))


async def prepare_script(
    *,
    store: AbilityAccessStore,
    actor: AccessIdentity,
    agent: str,
    source: BuiltinAbilitySource,
    model_address: str,
    skill: str,
    key: str,
) -> ScriptTarget:
    """核对授予、版本目录和脚本文件。任何失败都不启动解释器。

    Args:
        store: 项目关系库。
        actor: 当前 Agent 的受信身份。
        agent: Agent 标识。
        source: 内置树与安装根。安装包从这里的安装存储取版本目录。
        model_address: 模型传入的脚本地址。
        skill: 已解析的 skill 名。
        key: 已解析的脚本键。

    Returns:
        可交给子进程运行器的目标。

    Raises:
        AccessError: 授予或 run 不成立。
        ScriptRejectionError: 包目录、链接、语言或摘要不成立。
    """
    grant = await store.resolve_script(actor, agent, f"skill/{skill}/scripts/{key}")
    package_root = _package_root(source, grant)
    script_path = _script_file(package_root, grant)
    return ScriptTarget(
        model_address=model_address,
        grant=grant,
        package_root=package_root,
        script_path=script_path,
        workspace=_workspace(grant),
    )


def _package_root(source: BuiltinAbilitySource, grant: ScriptGrant) -> Path:
    """内置包用内置树，其余包用安装版本目录。"""
    if grant.namespace == "ghrah":
        base = builtin_root().path
        root = None if base is None else base / grant.container
    else:
        provider = source.packages
        root = None if provider is None else provider.installed_root(grant.package, grant.release)
    if root is None or is_link(root) or not (root / "ability.yaml").is_file():
        raise ScriptRejectionError("not_found", "脚本所在的包版本不可用")
    return root


def _workspace(grant: ScriptGrant) -> Path:
    """实例根是工作区。空路径或不存在的目录不能当作子进程工作目录。"""
    if not grant.workspace:
        raise ScriptRejectionError("internal", "授予实例没有可用工作区")
    workspace = Path(grant.workspace)
    if not workspace.is_absolute() or not workspace.is_dir() or is_link(workspace):
        raise ScriptRejectionError("internal", "授予实例没有可用工作区")
    return workspace


def _script_file(package_root: Path, grant: ScriptGrant) -> Path:
    """确认登记路径是包根内的普通 ``.py``，且字节与授予摘要一致。"""
    relative = grant.relative_path
    try:
        relative_path(relative)
    except ValueError as exc:
        raise ScriptRejectionError("permission_denied", "脚本路径越出安装根") from exc
    skill = grant.grant_address.split("/")[1]
    if not relative.startswith(f"skills/{skill}/scripts/"):
        raise ScriptRejectionError("permission_denied", "脚本路径越出安装根")
    if is_link(package_root):
        raise ScriptRejectionError("permission_denied", "脚本路径越出安装根")
    current = package_root
    for part in relative.split("/"):
        current = current / part
        if is_link(current):
            raise ScriptRejectionError("permission_denied", "脚本路径越出安装根")
    if not current.is_file():
        raise ScriptRejectionError("not_found", "登记的脚本文件不存在")
    if not current.resolve().is_relative_to(package_root.resolve()):
        raise ScriptRejectionError("permission_denied", "脚本路径越出安装根")
    if current.suffix != ".py":
        raise ScriptRejectionError("unsupported_language", "v1 只运行 .py 脚本")
    try:
        info = current.stat()
    except OSError as exc:
        raise ScriptRejectionError("not_found", "登记的脚本文件不存在") from exc
    if info.st_size != grant.size or digest_bytes(current.read_bytes()) != grant.digest:
        raise ScriptRejectionError("permission_denied", "脚本内容与授予版本不一致")
    return current
