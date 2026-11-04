# SPDX-License-Identifier: Apache-2.0
"""显式捕获包内文件；运行内容仅来自已提交清单和按摘要保存的字节。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from ghrah.protocol.ability_tree import TreeModel
from pydantic import Field

from ghrah.ability.loading.ability_schema import AbilityDefinition, load_yaml


def canonical(value: Any) -> str:
    """生成摘要使用的稳定 JSON 文本。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_bytes(data: bytes) -> str:
    """计算内容寻址摘要。"""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def relative_path(path: str) -> str:
    """拒绝绝对路径、上级路径、反斜杠和不规范片段。"""
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or any(p in {"", ".", ".."} for p in path.split("/"))
        or ":" in path
    ):
        raise ValueError(f"非法包内路径: {path!r}")
    return path


@dataclass(frozen=True)
class CaptureLimits:
    """捕获限额；超限失败，不发布不完整版本。"""

    file_bytes: int = 8 * 1024 * 1024
    total_bytes: int = 32 * 1024 * 1024
    entries: int = 1024

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value <= 0
            for value in (self.file_bytes, self.total_bytes, self.entries)
        ):
            raise ValueError("捕获上限必须为正整数")


def capture_bytes(root: Path, path: str, limit: int) -> bytes:
    """逐级使用目录描述符和 NOFOLLOW，替换源路径不能跳出包根。"""
    relative_path(path)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.split("/")[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        file_fd = os.open(
            path.split("/")[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
        )
        with os.fdopen(file_fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError(f"非普通文件或文件超限: {path}")
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            if len(data) > limit or (before.st_size, before.st_mtime_ns) != (
                after.st_size,
                after.st_mtime_ns,
            ):
                raise ValueError(f"捕获期间文件变化或超限: {path}")
            return data
    finally:
        os.close(fd)


def capture_content(
    definition: AbilityDefinition, root: Path, limits: CaptureLimits
) -> tuple[AbilityDefinition, str, tuple[tuple[str, bytes], ...]]:
    """捕获声明清单；保持原文和相对根，不通过正文链接推断包内容。"""
    definition = definition.model_copy(deep=True)
    if definition.assets:
        raise ValueError("旧顶层 assets 归属不明；请显式迁入单 Skill")
    manifest: dict[str, dict[str, Any]] = {}
    blobs: dict[str, bytes] = {}
    total = 0
    linked_bodies: list[tuple[str, str]] = []

    def add(member: str, path: str, media_type: str, expected: str | None = None) -> bytes:
        nonlocal total
        if len(manifest) >= limits.entries:
            raise ValueError("包文件数超限")
        data = capture_bytes(root, path, limits.file_bytes)
        total += len(data)
        if total > limits.total_bytes:
            raise ValueError("包总字节数超限")
        digest = digest_bytes(data)
        if expected is not None and expected != digest:
            raise ValueError(f"内容摘要不符: {path}")
        manifest[member] = {
            "path": path,
            "media_type": media_type,
            "size": len(data),
            "digest": digest,
        }
        blobs[digest] = data
        return data

    for key, skill in definition.skills.items():
        member = f"skill/{key}"
        raw = add(member, skill.body.path, skill.body.media_type).decode("utf-8")
        if raw.startswith("---\n"):
            parts = raw.split("---\n", 2)
            if len(parts) != 3:
                raise ValueError("SKILL.md frontmatter 未闭合")
            metadata = load_yaml(parts[1])
            if not isinstance(metadata, dict) or not all(
                isinstance(metadata.get(name), str) and metadata[name].strip()
                for name in ("name", "description")
            ):
                raise ValueError("SKILL.md 必须包含 name 和 description")
            skill.title = metadata["name"]
            skill.description = metadata["description"]
        elif definition.schema_version == "0.2":
            raise ValueError("标准 SKILL.md 缺少 frontmatter")
        if isinstance(skill.references, list):
            if skill.references:
                raise ValueError("旧 references 列表须显式转换为具名内容清单")
            skill.references = {}
        for role, resources in (("assets", skill.assets), ("references", skill.references)):
            for resource, entry in resources.items():
                add(
                    f"{member}/{role}/{resource}",
                    entry.path,
                    entry.media_type,
                    entry.expected_digest,
                )
        linked_bodies.append((skill.body.path, raw))
    for key, operation in definition.operations.items():
        if operation.script is not None:
            entry = operation.script
            add(f"operations/{key}/script", entry.path, entry.media_type, entry.expected_digest)
    for body_path, raw in linked_bodies:
        # 只校验本地链接；外部 URL 不获取。清单包含未被链接提到的声明文件。
        known = {entry["path"] for entry in manifest.values()}
        body_dir = PurePosixPath(body_path).parent
        for link in re.findall(r"!?\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)", raw):
            url = urlsplit(link)
            if url.scheme or url.netloc or not url.path:
                continue
            path = str(body_dir / unquote(url.path))
            relative_path(path)
            if path not in known:
                raise ValueError(f"Skill 链接未登记: {link}")
    return definition, canonical(manifest), tuple(sorted(blobs.items()))


class ContentReadRequest(TreeModel):
    """每块单独核权；不接收磁盘路径或下载 URL。"""

    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=65536, ge=1, le=65536)


class ContentDescription(TreeModel):
    """固定版本的有界 JSON 内容；文本 UTF-8，二进制 base64。"""

    instance_id: str
    version_digest: str
    member: str
    path: str
    media_type: str
    digest: str
    total_bytes: int = Field(ge=0)


class ContentReadResult(ContentDescription):
    """确定资源的一块内容；范围与编码明确，二进制不自动注入模型。"""

    offset: int = Field(ge=0)
    byte_count: int = Field(ge=0, le=65536)
    next_offset: int | None
    encoding: Literal["utf-8", "base64"]
    content: str


@dataclass(frozen=True)
class ContentChunk:
    """领域读取的确定字节块；编码只在 JSON 适配边界发生。"""

    description: ContentDescription
    offset: int
    data: bytes
    next_offset: int | None

    def to_json_result(self) -> ContentReadResult:
        """文本块尽量以 UTF-8 表达，二进制或跨字符边界的块保持无损。"""
        encoding = "base64"
        content = base64.b64encode(self.data).decode("ascii")
        if self.description.media_type.startswith("text/"):
            try:
                content = self.data.decode("utf-8")
                encoding = "utf-8"
            except UnicodeDecodeError:
                pass
        return ContentReadResult(
            **self.description.model_dump(),
            offset=self.offset,
            byte_count=len(self.data),
            next_offset=self.next_offset,
            encoding=encoding,
            content=content,
        )
