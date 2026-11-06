# SPDX-License-Identifier: Apache-2.0
"""在临时数据库中装配完整 Ability；不修改生产 Agent 或来源包。"""

import asyncio
import json
import tempfile
from pathlib import Path

from ghrah.protocol.ability_tree import GrantAbilityRequest
from ghrah.scheme import Principal

from ghrah.ability.access import AbilityAccessStore, AccessIdentity
from ghrah.ability.consumer import GrantedContentProvider
from ghrah.ability.scheme import AbilitySchemeHost
from ghrah.ability.versions import capture_ability_version
from ghrah.chat.factory import ChatMessageFactory
from ghrah.context.manager import ContextManager


async def main() -> None:
    """提交快照、显式授予，并把正文及附件读入实际 Context。"""
    source = Path(__file__).with_name("reporting")
    version = capture_ability_version((source / "ability.yaml").read_text(), root=source)
    admin = AccessIdentity("demo", "admin", "control", True)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = await AbilityAccessStore.open(
            root / "project.db",
            "demo",
            operators=frozenset({"admin"}),
            runs=lambda agent: "demo-run" if agent == "alice" else None,
        )
        host = AbilitySchemeHost(store)
        try:
            await store.commit_version(admin, version)
            await store.register_agent(admin, "alice")
            await store.select_instance(admin, "reporting", version.digest, root)
            await store.grant(
                admin,
                "alice",
                GrantAbilityRequest(
                    alias="reports",
                    instance_id="reporting",
                    whole_container=True,
                    allowed_roots=[str(root)],
                    expected_revision=0,
                ),
            )
            await host.install("reporting")
            client = host.session(Principal("demo", "alice", "demo-run"))
            content = GrantedContentProvider(client, "alice")
            context = ContextManager(
                agent_name="alice", message_factory=ChatMessageFactory(), ability_content=content
            )
            await content.load_into_context(context, "reports", "skill/guide")
            await content.load_into_context(context, "reports", "skill/guide/references/style")
            binary = await content.read("reports", "skill/guide/assets/mark")
            print(
                json.dumps(
                    {
                        "view": (await client.read("alice")).model_dump(mode="json"),
                        "binary": binary.model_dump(mode="json"),
                        "context": [message.text for message in await context.get_llm_messages()],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        finally:
            await host.close()
            await store.close()


if __name__ == "__main__":
    asyncio.run(main())
