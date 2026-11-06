# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""表面装配与检索共用的确定性成员目录。"""

import pytest

from ghrah.ability.surface import MemberRef, SurfaceCatalog, SurfaceEntry, SurfaceTool


@pytest.fixture
def make_tool():
    """生成带输入契约与检索摘要的授权成员。"""

    def make(name, *, description="Read a document", tags=(), effects=(), package="files"):
        return SurfaceTool(
            entry=SurfaceEntry(
                tool_name=name,
                member=MemberRef(instance_id=f"inst-{package}", member=name),
                package=package,
            ),
            full_name=f"example.{package}.{name}",
            version_digest="version-1",
            description=description,
            tags=tuple(tags),
            effects=tuple(effects),
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        )

    return make


@pytest.fixture
def make_catalog():
    """生成单个受信 Agent/run 的目录。"""

    def make(tools, *, agent="alice", run="alice-run", revision=1):
        return SurfaceCatalog("project", agent, run, revision, tuple(tools))

    return make
