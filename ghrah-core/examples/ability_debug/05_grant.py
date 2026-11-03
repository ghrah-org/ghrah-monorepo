# SPDX-License-Identifier: Apache-2.0
"""内容/授权面调试：提交版本 → 登记 → 授予 → 受控读取（临时库，结束即删）。

运行（仓库根）：``uv run python ghrah-core/examples/ability_debug/05_grant.py``
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ghrah.protocol.ability_tree import GrantAbilityRequest
from ghrah.scheme import Principal

from ghrah.ability.access import AbilityAccessStore, AccessIdentity
from ghrah.ability.consumer import GrantedContentProvider
from ghrah.ability.scheme import AbilitySchemeHost
from ghrah.ability.versions import capture_ability_version

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "ghrah-core/examples/ability_content/reporting"


@contextmanager
def scratch_directory() -> Iterator[Path]:
    """临时工作目录：优先系统 temp，不可写时退回仓库内的 ``.scratch``。

    某些 IDE（或被裁剪的 Windows 环境）拿不到可写的 ``%TEMP%``，
    ``tempfile.TemporaryDirectory()`` 会直接抛 ``FileNotFoundError``；调试脚本不该
    因此跑不起来。候选落点逐个探测可写性，退出时都清掉。
    """
    try:
        with tempfile.TemporaryDirectory() as directory:
            yield Path(directory)
        return
    except FileNotFoundError, PermissionError, OSError:
        pass

    failure: OSError | None = None
    for base in (Path(__file__).resolve().parent, REPO):
        local = base / ".scratch"
        try:
            shutil.rmtree(local, ignore_errors=True)
            local.mkdir(parents=True)
            probe = local / ".probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            failure = exc
            continue
        print(f"（系统临时目录不可用，本次改用 {local}）")
        try:
            yield local
        finally:
            shutil.rmtree(local, ignore_errors=True)
        return
    raise RuntimeError(f"找不到可写的临时目录（试试设置 TEMP 环境变量）：{failure}")


async def main() -> None:
    """跑一遍“封版本 → 授权 → 读正文”的最小闭环。"""
    version = capture_ability_version((EXAMPLE / "ability.yaml").read_text(), root=EXAMPLE)
    admin = AccessIdentity("demo", "admin", "control", True)
    with scratch_directory() as directory:
        store = await AbilityAccessStore.open(
            directory / "project.db",
            "demo",
            operators=frozenset({"admin"}),
            runs=lambda agent: "demo-run" if agent == "alice" else None,
        )
        host = AbilitySchemeHost(store)
        try:
            await store.commit_version(admin, version)
            await store.register_agent(admin, "alice")
            await store.select_instance(admin, "reporting", version.digest, EXAMPLE)
            view = await store.grant(
                admin,
                "alice",
                GrantAbilityRequest(
                    alias="reports",
                    instance_id="reporting",
                    whole_container=True,
                    allowed_roots=[str(EXAMPLE)],
                    expected_revision=0,
                ),
            )
            print("== 授予后的视图 ==")
            node = view.abilities[0]
            print("alias      :", node.alias)
            print("operations :", [operation.key for operation in node.operations])
            print("skill      :", node.skill.key if node.skill else None)
            print("assets     :", [item.key for item in node.skill.assets] if node.skill else [])
            print(
                "references :",
                [item.key for item in node.skill.references] if node.skill else [],
            )

            await host.install("reporting")
            client = host.session(Principal("demo", "alice", "demo-run"))
            content = GrantedContentProvider(client, "alice")
            print("\n== 受控读取 ==")
            print("---- catalog_text() ----")
            print(await content.catalog_text())
            body = await content.read("reports", "skill/guide")
            print("skill body  :", body.media_type, body.total_bytes, "B")
            reference = await content.read_relative("reports", "guide", "references/style.md")
            print("reference   :", reference.media_type, reference.total_bytes)
        finally:
            await host.close()
            await store.close()


if __name__ == "__main__":
    asyncio.run(main())
