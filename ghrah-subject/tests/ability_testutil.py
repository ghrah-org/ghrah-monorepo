# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""用生产模板库为集成测试声明 v1 能力。"""

from typing import Any

import yaml
from ghrah.chat.content import TextBlock
from ghrah.chat.format import LLMResponse

from ghrah.subject.manifest_store.store import ManifestStore


class EchoLLM:
    model = "project-message-test"

    def configure_tools(self, tools):
        pass

    def apply_model_overrides(self, overrides):
        pass

    async def generate(self, messages, tools=None):
        return LLMResponse(content_blocks=[TextBlock(text="ready")])


def put_agent_template(
    store: ManifestStore,
    name: str = "agent",
    *,
    endpoint: str = "default",
    members: tuple[str, ...] = ("ghrah.core.session_info",),
    model_params: dict[str, Any] | None = None,
) -> str:
    """写入可由生产 Core 装配的最小模板，返回引用。"""
    ref = f"tests.{name}"
    store.put_agent(
        ref,
        yaml.safe_dump(
            {
                "manifest": "agent",
                "version": "2",
                "metadata": {"namespace": "tests", "name": name},
                "model": {"endpoint_name": endpoint, **(model_params or {})},
                "system_prompt": "Reply to the user.",
                "abilities": [{"type": "conversation"}, *({"ref": member} for member in members)],
            }
        ),
        overwrite=True,
    )
    return ref
