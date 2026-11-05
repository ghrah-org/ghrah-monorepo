# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""通过受信 Scheme 会话向实际 Context 提供当前获授内容。"""

from __future__ import annotations

import base64
import html
from typing import TYPE_CHECKING, Any

from ghrah.ability.content import ContentReadResult, relative_path

if TYPE_CHECKING:
    from ghrah.ability.scheme.host import AbilityTreeSession
    from ghrah.context.manager import ContextManager


class GrantedContentProvider:
    """固定请求者；目录每次由关系库重建，正文和附件按需读取。"""

    def __init__(self, client: AbilityTreeSession, agent_id: str) -> None:
        self.client = client
        self.agent_id = agent_id

    async def catalog_text(self) -> str:
        """为 Context 当前轮生成获准正文目录，不扫描来源或建立许可。"""
        view = await self.client.read(self.agent_id)
        entries = [
            f"- `{node.alias}/{skill.key}`: {html.escape(skill.description)}"
            for node in view.abilities
            for skill in node.skills
            if skill.body_allowed
        ]
        return "<available_skills>\n" + "\n".join(entries) + "\n</available_skills>"

    async def read(
        self, alias: str, member: str, *, offset: int = 0, limit: int = 65536
    ) -> ContentReadResult:
        """读取单块；每次通过真实 Scheme Provider 最后核权。"""
        result = await self.client.call(
            f"/scheme/agent/{self.agent_id}/ability/{alias}/{member}",
            "read",
            {"offset": offset, "limit": limit},
        )
        return ContentReadResult.model_validate(result)

    async def read_relative(self, alias: str, skill_key: str, path: str) -> ContentReadResult:
        """相对链接只解析到已登记且获准的子资源，不拼接磁盘路径。"""
        from pathlib import PurePosixPath

        from ghrah.scheme import ErrorCode, SchemeError

        relative_path(path)
        body = await self.read(alias, f"skill/{skill_key}", limit=1)
        target = str(PurePosixPath(body.path).parent / path)
        view = await self.client.read(self.agent_id)
        for node in view.abilities:
            if node.alias != alias:
                continue
            for skill in node.skills:
                if skill.key != skill_key:
                    continue
                for entry in (*skill.assets, *skill.references):
                    if entry.path == target:
                        return await self.read(alias, entry.member)
        raise SchemeError(ErrorCode.ACCESS_DENIED)

    async def load_into_context(
        self, context: ContextManager, alias: str, member: str, *, max_bytes: int = 1024 * 1024
    ) -> Any:
        """完整有界文本进入实际消息存储；二进制由消费方另行处理。"""
        if context.ability_content is not self or context.agent_name != self.agent_id:
            raise ValueError("Context 与受信内容身份不一致")
        data = bytearray()
        first = None
        offset = 0
        while True:
            result = await self.read(alias, member, offset=offset)
            if not result.media_type.startswith("text/") or result.total_bytes > max_bytes:
                raise ValueError("内容不是允许注入的有界文本")
            identity = (result.version_digest, result.digest)
            if first is not None and identity != first:
                raise ValueError("读取期间版本变化")
            first = identity
            data.extend(
                base64.b64decode(result.content, validate=True)
                if result.encoding == "base64"
                else result.content.encode("utf-8")
            )
            if result.next_offset is None:
                break
            offset = result.next_offset
        if context.message_factory is None:
            raise ValueError("Context 缺少消息工厂")
        message = context.message_factory.create_message(
            role="user", text=data.decode("utf-8"), source=f"ability:{alias}/{member}"
        )
        context.add_messages([message])
        return message
