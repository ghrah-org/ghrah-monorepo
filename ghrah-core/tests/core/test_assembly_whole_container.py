"""装配输入里的「整包」由 Core 按自己的能力来源展开。

Subject 侧没有包目录，只能把包名交给 Core；这里是展开落点：
``whole_container=True`` 的成员条目按 ``source.load_package_members`` 展开，
控制成员（conversation / end_task 这类）由装配边界自己加，展开时跳过。
"""

from __future__ import annotations

import pytest
from ghrah.core.agents.assembly import resolve_members
from ghrah.manifest.ability import AbilityManifest, AbilityMetadata
from ghrah.manifest.errors import ManifestNotFoundError
from ghrah.manifest.types import ImplementationDef, PermissionFlags


def _manifest(package: str, member: str, handler: str, kind: str = "python") -> AbilityManifest:
    return AbilityManifest(
        manifest="ability",
        version="1",
        metadata=AbilityMetadata(
            namespace=package,
            name=member,
            title=member.title(),
            description="test member",
            permissions=PermissionFlags(),
        ),
        tool=None,
        implementation=ImplementationDef(type=kind, handler=handler),
    )


class _Source:
    """最小能力来源：一个包 = 两个普通成员 + 一个控制成员。"""

    def __init__(self) -> None:
        self._members = [
            _manifest("acme.demo", "ping", "ping"),
            _manifest("acme.demo", "guide", "guide", kind="skill"),
            _manifest("ghrah.core", "end_task", "end_task"),
        ]

    def load_ability(self, full_name: str) -> AbilityManifest:
        for member in self._members:
            if member.full_name == full_name:
                return member
        raise ManifestNotFoundError(full_name)

    def load_package_members(self, full_name: str) -> list[AbilityManifest]:
        matched = [item for item in self._members if item.full_name.startswith(f"{full_name}.")]
        if not matched:
            raise ManifestNotFoundError(full_name)
        return matched


def test_whole_container_expands_into_all_members() -> None:
    resolved = resolve_members(
        _Source(),  # type: ignore[arg-type]
        [{"member": "acme.demo", "whole_container": True}],
    )
    assert [item.ability_name for item in resolved] == ["acme.demo.ping", "acme.demo.guide"]


def test_whole_container_skips_control_members() -> None:
    resolved = resolve_members(
        _Source(),  # type: ignore[arg-type]
        [{"member": "ghrah.core", "whole_container": True}],
    )
    assert [item.ability_name for item in resolved] == []


def test_explicit_member_still_resolves() -> None:
    resolved = resolve_members(
        _Source(),  # type: ignore[arg-type]
        [{"member": "acme.demo.ping"}],
    )
    assert [item.ability_name for item in resolved] == ["acme.demo.ping"]


def test_unknown_package_fails_with_a_stable_message() -> None:
    with pytest.raises(Exception) as error:
        resolve_members(
            _Source(),  # type: ignore[arg-type]
            [{"member": "acme.missing", "whole_container": True}],
        )
    assert "package not resolvable" in str(error.value)