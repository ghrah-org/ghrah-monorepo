# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Agent 授权域内的确定性关键词检索；索引只留摘要，不保存 schema。"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ghrah.ability.surface.catalog import SurfaceCatalog, read_agent_catalog
from ghrah.ability.surface.config import DEFAULT_SEARCH_LIMIT, SEARCH_LIMIT_ABS_MAX
from ghrah.ability.surface.naming import RESERVED_TOOL_NAMES

if TYPE_CHECKING:
    from ghrah.ability.access.store import AbilityAccessStore

_WORDS = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]+|[^\W_]+", re.UNICODE)
_CJK = re.compile(r"^[\u3400-\u9fff]+$")


def tokenize(text: str) -> tuple[str, ...]:
    """索引与查询共用 NFKC/casefold、英文词及中文单字和双字切分，无词典依赖。"""
    tokens = []
    for word in _WORDS.findall(unicodedata.normalize("NFKC", text).casefold()):
        if _CJK.fullmatch(word):
            tokens.extend(word)
            tokens.extend(word[index : index + 2] for index in range(len(word) - 1))
        else:
            tokens.append(word)
    return tuple(tokens)


@dataclass(frozen=True, slots=True)
class SearchDocument:
    """检索允许保存的字段；没有 schema、磁盘路径或未获授成员目录。"""

    tool_name: str
    member: str
    package: str
    description: str
    tags: tuple[str, ...]
    effects: tuple[str, ...]


class ToolSearchIndex:
    """固定单个项目/Agent/run 的倒排索引；同分按工具名排序。"""

    def __init__(self, catalog: SurfaceCatalog) -> None:
        self.scope = (catalog.project_id, catalog.agent_id, catalog.run_id)
        self.documents = self.documents_for(catalog)
        self.generation = self.generation_for(catalog)
        self._postings: dict[str, dict[int, float]] = defaultdict(dict)
        count = len(self.documents)
        for index, document in enumerate(self.documents):
            weights: Counter[str] = Counter()
            for value, weight in (
                (document.tool_name, 8),
                (document.member, 5),
                (document.package, 2),
                (document.description, 1),
                (" ".join(document.tags), 3),
                (" ".join(document.effects), 2),
            ):
                for token, frequency in Counter(tokenize(value)).items():
                    weights[token] += weight * (1 + math.log(frequency))
            for token, weight in weights.items():
                self._postings[token][index] = weight
        self._idf = {
            token: math.log(1 + (count + 1) / (len(posting) + 1))
            for token, posting in self._postings.items()
        }

    @staticmethod
    def documents_for(catalog: SurfaceCatalog) -> tuple[SearchDocument, ...]:
        """只从已授权目录生成摘要，排除元工具与独立脚本入口。"""
        return tuple(
            SearchDocument(
                tool.entry.tool_name,
                tool.entry.member.member,
                tool.entry.package,
                tool.description.splitlines()[0].strip() if tool.description.splitlines() else "",
                tool.tags,
                tool.effects,
            )
            for tool in sorted(catalog.tools, key=lambda item: item.entry.tool_name)
            if tool.entry.tool_name not in RESERVED_TOOL_NAMES
            and tool.entry.member.member not in RESERVED_TOOL_NAMES
        )

    @staticmethod
    def generation_for(catalog: SurfaceCatalog) -> tuple:
        """修订与实例版本共同失效；实例切换可能不改变 Agent 关系修订。"""
        return (
            catalog.revision,
            tuple(
                (tool.entry, tool.version_digest)
                for tool in sorted(catalog.tools, key=lambda item: item.entry.tool_name)
            ),
        )

    def search(self, query: str, *, limit: int = DEFAULT_SEARCH_LIMIT) -> list[SearchDocument]:
        """按名称加权 TF-IDF 检索，空查询不列目录，结果上限固定为 20。"""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit 必须是正整数")
        limit = min(limit, SEARCH_LIMIT_ABS_MAX)
        scores: dict[int, float] = defaultdict(float)
        for token in sorted(set(tokenize(query))):
            for index, weight in self._postings.get(token, {}).items():
                scores[index] += weight * self._idf[token]
        return [
            self.documents[index]
            for index in sorted(
                scores, key=lambda index: (-scores[index], self.documents[index].tool_name)
            )[:limit]
        ]


class AgentToolSearch:
    """绑定一个受信 Agent/run 的查询端口，每次先读取当前授权再使用索引。

    缓存只存在本对象中；授予、撤销、选择、成员轴或版本改变时全部重建。
    未获授元数据从不进入索引；索引也不反向写入关系库。
    """

    def __init__(
        self,
        agent: str,
        run: str,
        *,
        store: AbilityAccessStore,
        search_limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> None:
        if isinstance(search_limit, bool) or not isinstance(search_limit, int) or search_limit < 1:
            raise ValueError("search_limit 必须是正整数")
        self._agent = agent
        self._run = run
        self._store = store
        self._limit = min(search_limit, SEARCH_LIMIT_ABS_MAX)
        self._index: ToolSearchIndex | None = None

    async def search(self, query: str, *, limit: int | None = None) -> list[SearchDocument]:
        """过滤当前授权后搜索；读失败时丢弃缓存并拒绝，不返回旧索引结果。"""
        try:
            catalog = await read_agent_catalog(self._agent, self._run, store=self._store)
        except Exception:
            self._index = None
            raise
        if (
            self._index is None
            or self._index.scope != (catalog.project_id, catalog.agent_id, catalog.run_id)
            or self._index.generation != ToolSearchIndex.generation_for(catalog)
            or self._index.documents != ToolSearchIndex.documents_for(catalog)
        ):
            self._index = ToolSearchIndex(catalog)
        return self._index.search(query, limit=self._limit if limit is None else limit)
