# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""当前 Agent/run 的获授内容服务；只读关系库与已提交字节，不依赖 Scheme。"""

from __future__ import annotations

import html
from dataclasses import replace
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from ghrah.protocol.ability_tree import AbilityTreeView, TreeKey
from pydantic import TypeAdapter

from ghrah.ability.access import AbilityAccessStore, AccessError, AccessIdentity, MemberFence
from ghrah.ability.content import ContentReadRequest, ContentReadResult, relative_path

if TYPE_CHECKING:
    from ghrah.ability.execution.invocation import OperationCall
    from ghrah.context.manager import ContextManager


class LocalGrantedContentProvider:
    """一次 Agent 运行独占的内容端口；授权与版本每次从领域库核验。

    装配时创建，注册成功后只允许绑定一次 run。停止或换代后旧对象永久失效，
    不借用同一主体的新运行。服务不拥有数据库连接，也不授予、选工具或激活 MCP。
    """

    def __init__(self, store: AbilityAccessStore, agent_id: str) -> None:
        self.store = store
        self.agent_id = agent_id
        self._identity = AccessIdentity(store.project_id, agent_id, "")

    def bind_run(self, run: str) -> None:
        """由注册装配者绑定当前代次；旧服务不能重新绑定到新运行。"""
        if (
            not run
            or (self._identity.run_id and self._identity.run_id != run)
            or self.store.current_run(self.agent_id) != run
        ):
            raise ValueError("内容服务不能绑定另一运行代次")
        self._identity = replace(self._identity, run_id=run)

    def validate_context(self, agent_id: str) -> None:
        """Context 显式复用服务时，核验稳定主体与当前运行。"""
        if (
            agent_id != self.agent_id
            or not self._identity.run_id
            or self.store.current_run(self.agent_id) != self._identity.run_id
        ):
            raise ValueError("Context 与内容服务的主体或运行代次不一致")

    def validate_agent(self, agent_id: str) -> None:
        """构造 Context 时先核验主体；run 由后续注册边界绑定。"""
        if agent_id != self.agent_id:
            raise ValueError("Context 与内容服务的稳定主体不一致")

    def validate_call(self, call: OperationCall) -> None:
        """执行服务与工具调用必须来自同一项目、稳定主体和运行代次。"""
        identity = call.identity
        if (
            identity.project_id != self._identity.project_id
            or identity.agent != self.agent_id
            or not identity.run
            or identity.run != self._identity.run_id
        ):
            raise AccessError("access_denied")

    async def view(self) -> AbilityTreeView:
        """读取当前获授投影；不扫描包源，也不缓存许可结论。"""
        return await self.store.view(self._identity, self.agent_id)

    async def skills(self) -> list[dict[str, Any]]:
        """列出获授正文及其获准子资源，名称统一为 alias/skill。"""
        return self.skill_rows(await self.view())

    @staticmethod
    def skill_rows(view: AbilityTreeView) -> list[dict[str, Any]]:
        """由一次权威投影生成目录行，不二次读取或扫描来源。"""
        return [
            {
                "full_name": f"{node.alias}/{skill.key}",
                "kind": "skill-ability",
                "description": skill.description,
                "title": skill.title,
                "alias": node.alias,
                "member": f"skill/{skill.key}",
                "instance_id": node.instance_id,
                "version_digest": node.version_digest,
                "assets": [item.model_dump() for item in skill.assets],
                "references": [item.model_dump() for item in skill.references],
                "scripts": [item.model_dump() for item in skill.scripts],
            }
            for node in view.abilities
            for skill in node.skills
            if skill.body_allowed
        ]

    async def catalog_text(self) -> str:
        """每轮生成只含名称与摘要的目录，正文按需读取。"""
        entries = [
            f"- `{item['full_name']}`: {html.escape(item['description'])}"
            for item in await self.skills()
        ]
        return "<available_skills>\n" + "\n".join(entries) + "\n</available_skills>"

    async def read(
        self, alias: str, member: str, *, offset: int = 0, limit: int = 65536
    ) -> ContentReadResult:
        """按登记成员读取一块；最终读取仍由关系库核权。"""
        fence = await self.store.resolve(self._identity, alias, member)
        return await self.store.read_content(
            self._identity, fence, ContentReadRequest(offset=offset, limit=limit)
        )

    async def _relative_reference(self, alias: str, skill_key: str, path: str) -> MemberFence:
        """相对链接只匹配已登记且获授的资源，不将路径交给宿主文件系统。"""
        relative_path(path)
        body = await self.store.resolve(self._identity, alias, f"skill/{skill_key}")
        description = await self.store.describe_content(self._identity, body)
        target = str(PurePosixPath(description.path).parent / path)
        view = await self.view()
        for node in view.abilities:
            if node.alias != alias:
                continue
            for skill in node.skills:
                if skill.key != skill_key or not skill.body_allowed:
                    continue
                for entry in (*skill.assets, *skill.references, *skill.scripts):
                    if entry.path == target:
                        fence = await self.store.resolve(self._identity, alias, entry.member)
                        await self.store.authorize(self._identity, body)
                        if (
                            fence.instance_revision != body.instance_revision
                            or fence.instance_id != body.instance_id
                            or fence.grant_revision != body.grant_revision
                        ):
                            raise AccessError("access_denied")
                        return fence
        raise AccessError("access_denied")

    async def read_relative(self, alias: str, skill_key: str, path: str) -> ContentReadResult:
        """相对链接读取获准附件首块；二进制以标准编码结果返回。"""
        fence = await self._relative_reference(alias, skill_key, path)
        return await self.store.read_content(self._identity, fence, ContentReadRequest())

    async def _read_text(
        self, fence: MemberFence, *, max_bytes: int = 1024 * 1024
    ) -> dict[str, Any]:
        """固定一个成员引用读取完整文本；跨块不重新解析版本或授权修订。"""
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("文本读取上限必须为正整数")
        description = await self.store.describe_content(self._identity, fence)
        if not description.media_type.startswith("text/") or description.total_bytes > max_bytes:
            raise ValueError("内容不是允许加载的有界文本")
        data = bytearray()
        offset = 0
        while True:
            chunk = await self.store.read_chunk(
                self._identity,
                fence,
                ContentReadRequest(offset=offset, limit=self.store.read_limit),
            )
            data.extend(chunk.data)
            if chunk.next_offset is None:
                break
            offset = chunk.next_offset
        await self.store.authorize(self._identity, fence)
        return {
            **description.model_dump(),
            "alias": fence.alias,
            "uri": f"ability:{fence.alias}/{fence.member}",
            "content": data.decode("utf-8"),
        }

    async def load_skill(self, full_name: str, *, path: str | None = None) -> dict[str, Any]:
        """读取目录中一个 Skill 的完整正文，或其获授的相对文本附件。"""
        parts = full_name.split("/")
        if len(parts) != 2:
            raise ValueError("Skill 名称必须为目录中的 alias/skill")
        alias, skill_key = (TypeAdapter(TreeKey).validate_python(part) for part in parts)
        body = await self.store.resolve(self._identity, alias, f"skill/{skill_key}")
        fence = await self._relative_reference(alias, skill_key, path) if path is not None else body
        if (
            fence.instance_revision != body.instance_revision
            or fence.instance_id != body.instance_id
            or fence.grant_revision != body.grant_revision
        ):
            raise AccessError("access_denied")
        result = await self._read_text(fence)
        await self.store.authorize(self._identity, body)
        return {"full_name": full_name, **result}

    async def load_into_context(
        self, context: ContextManager, alias: str, member: str, *, max_bytes: int = 1024 * 1024
    ) -> Any:
        """将完整文本交给唯一 Context 写侧；调用前后核验当前主体与代次。"""
        self.validate_context(context.agent_name)
        if context.ability_content is not self or context.message_factory is None:
            raise ValueError("Context 未接线该内容服务或消息工厂")
        fence = await self.store.resolve(self._identity, alias, member)
        result = await self._read_text(fence, max_bytes=max_bytes)
        self.validate_context(context.agent_name)
        message = context.message_factory.create_message(
            role="user", text=result["content"], source=result["uri"]
        )
        context.add_messages([message])
        return message
