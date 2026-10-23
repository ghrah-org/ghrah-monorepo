# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SKILL.md 扫描与 frontmatter 解析。

扫描口径（与 DSH ``discoverRoot`` 对齐）：

- **只扫第一层，不递归**：``<root>/<full-name>/SKILL.md``；
- 根目录下的普通文件一律跳过——本版只支持 **bundle 目录形态**
  （``ability.lock.yaml`` / ``scripts/`` / ``assets/`` 都依附于目录，
  扁平 ``<name>.md`` 形态与 lock 文件不兼容，暂不支持）；
- 技能身份取**包目录名**（``<包名>-<技能名>``）：与 DSH「目录名仅作路径、
  frontmatter 的 name 为准」不同——跨包重名必须靠目录名区分。目录名与
  frontmatter 的 ``name`` **允许不同**：身份只认目录名，``name`` 只是技能文档
  自称的名字，不参与身份判定，也不做一致性校验；
- 同一个 full_name 由 rank 最小者胜出，被忽略者记为 issue（可见，非静默）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from ghrah.skills.errors import SkillParseError
from ghrah.skills.models import ScanIssue, SkillRoot, SkillScanResult, SkillSummary

__all__ = [
    "SKILL_FILE_NAME",
    "SKILL_NAME_RE",
    "parse_skill_file",
    "scan_skills",
    "split_frontmatter",
]

SKILL_FILE_NAME: str = "SKILL.md"

SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
"""skill 名与 full_name 的格式（kebab-case），与 DSH 一致。

``<包名>-<技能名>`` 也是 kebab-case（两段都是 kebab，用 ``-`` 连接），
所以同一个正则同时校验技能名与 full_name。

注意：与 ability 的 handler 命名（snake_case，``^[a-z][a-z0-9_]*$``）不同——
skill 是独立体系，不沿用 ability 的命名规则。
"""

_FRONTMATTER_DELIMITER = "---"
_REQUIRED_STRING_FIELDS = ("name", "description")
"""注入上下文的**最低门槛**：缺任一项即无法生成目录摘要，因此在此硬约束。

只做「存在 + 非空 + 格式」这一层；更深的字段质量检查（description 质量与长度、
whenToUse 语义、permission 合理性等）归后续的 skill 质量检查模块，不在这里做。
"""


def split_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """把 SKILL.md 拆成 (frontmatter 映射, 正文)。

    第一行必须是裸 ``---``（允许首尾空白），否则视为无 frontmatter。

    Raises:
        SkillParseError: 无起始/结束分隔符、YAML 非法、或顶层不是映射。
    """
    lines = raw.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_DELIMITER:
        raise SkillParseError("缺少 frontmatter 起始分隔符 ---")
    end = next(
        (i for i in range(1, len(lines)) if lines[i].strip() == _FRONTMATTER_DELIMITER),
        None,
    )
    if end is None:
        raise SkillParseError("缺少 frontmatter 结束分隔符 ---")
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise SkillParseError(f"frontmatter YAML 解析失败: {exc}") from exc
    if not isinstance(data, Mapping):
        raise SkillParseError("frontmatter 顶层不是 YAML 映射")
    return dict(data), "\n".join(lines[end + 1 :]).strip()


def parse_skill_file(
    skill_md: Path,
    *,
    skill_dir: Path,
    source: str,
    rank: int,
) -> SkillSummary:
    """解析单个 SKILL.md，返回技能元信息。

    Args:
        skill_md: SKILL.md 路径。
        skill_dir: 技能包目录；**其目录名即 full_name**（``<包名>-<技能名>``）。
        source: 来源标签（目录池的 ``source``）。
        rank: 来源优先级（目录池的 ``rank``）。

    Raises:
        SkillParseError: 文件不可读、无 frontmatter、缺必填字段、name 或目录名格式非法。
    """
    try:
        raw = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # 非 UTF-8（二进制/其它编码）也算"读不出来"：必须收成单条 issue，
        # 不能穿透 scan_skills 的单条 try 把整轮扫描炸掉。
        raise SkillParseError(f"SKILL.md 读取失败: {exc}") from exc

    data, _body = split_frontmatter(raw)

    for field_name in _REQUIRED_STRING_FIELDS:
        value = data.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise SkillParseError(f"缺少必填字段 {field_name}")

    name = data["name"]
    if not SKILL_NAME_RE.match(name):
        raise SkillParseError(f"name 格式非法（应为 kebab-case）: {name!r}")

    full_name = skill_dir.name
    if not SKILL_NAME_RE.match(full_name):
        raise SkillParseError(f"包目录名格式非法（full_name 应为 kebab-case）: {full_name!r}")

    return SkillSummary(
        full_name=full_name,
        name=name,
        description=data["description"].strip(),
        source=source,
        rank=rank,
        skill_dir=skill_dir,
        frontmatter=data,
    )


def scan_skills(pool: Sequence[SkillRoot]) -> SkillScanResult:
    """扫描技能目录池，返回技能元信息列表与问题清单。

    扫描顺序按 ``(rank, 目录路径)`` 升序，保证同一个 full_name 由 rank 最小者胜出；
    返回列表按 full_name 升序，保证跨文件系统/跨机器结果一致。
    """
    skills: dict[str, SkillSummary] = {}
    issues: list[ScanIssue] = []

    for root in sorted(pool, key=lambda item: (item.rank, str(item.path))):
        if not root.path.is_dir():
            issues.append(
                ScanIssue(
                    path=root.path,
                    reason=f"技能目录不存在（source={root.source}）",
                )
            )
            continue
        for entry in sorted(root.path.iterdir(), key=lambda item: item.name):
            if not entry.is_dir():
                # 扁平 <name>.md 形态本版不支持：静默跳过（不是技能，也不是错误）。
                continue
            skill_md = entry / SKILL_FILE_NAME
            if not skill_md.is_file():
                # 普通子目录：不是 skill，跳过。
                continue
            try:
                summary = parse_skill_file(
                    skill_md,
                    skill_dir=entry,
                    source=root.source,
                    rank=root.rank,
                )
            except SkillParseError as exc:
                issues.append(ScanIssue(path=skill_md, reason=str(exc)))
                continue
            existing = skills.get(summary.full_name)
            if existing is not None:
                issues.append(
                    ScanIssue(
                        path=skill_md,
                        reason=(
                            f"重复的 full_name {summary.full_name!r} 已由 "
                            f"{existing.skill_dir}（rank={existing.rank}）提供，本条被忽略"
                        ),
                    )
                )
                continue
            skills[summary.full_name] = summary

    return SkillScanResult(
        skills=tuple(skills[key] for key in sorted(skills)),
        issues=tuple(issues),
    )
