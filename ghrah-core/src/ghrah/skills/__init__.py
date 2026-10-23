# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""skill 发现与初始化（skill-ability）。

与 ability / manifest **并列的独立体系**：本包不 import ``ghrah.abilities``
与 ``ghrah.manifest``，只做一件事——把文件系统上的 skill 包（一个目录一个
skill）扫描成元信息，并为缺失 ``ability.lock.yaml`` 的 skill 首次生成该文件。

当前能力：

- :class:`SkillAbilityProvider`：技能存储目录池的持有者——**外层推荐入口**，
  池的构成在构造时确定，之后的扫盘 / 查表 / 指纹 / 初始化都不再传 root；
- :func:`scan_skills` / :func:`parse_skill_file`：扫盘 + frontmatter 解析（池在手时的底层入口）；
- :func:`initialize_skills`：确保技能目录存在、扫描、补齐缺失的 lock；
- :func:`query_fingerprint` / :func:`query_fingerprints`：查询当前指纹与 lock
  登记值（只读对比；漂移的处置属后续能力）；
- :func:`read_skill_document`：按需读取单个 skill 的 SKILL.md 原文（正文不进
  每轮注入的目录摘要，正文由 ``load_skill`` 能力按需拉取）；
- :func:`render_catalog_message` / :func:`digest_catalog`：把技能清单渲染成注入
  上下文的目录摘要（正文不在其中）；
- :func:`default_pool` / :func:`default_skill_root`：默认目录池
  （``~/.ghrah/skill-ability``，当前池中唯一且优先级最高的一项）。

尚未实现（后续能力）：权限核定、版本累加、目录变更监视、skill 质量检查。
"""

from __future__ import annotations

from ghrah.skills.catalog import (
    CATALOG_DESCRIPTION_MAX_CHARS,
    catalog_description,
    digest_catalog,
    render_catalog_message,
)
from ghrah.skills.document import read_skill_document
from ghrah.skills.errors import SkillError, SkillLockError, SkillNotFoundError, SkillParseError
from ghrah.skills.initialize import initialize_skills
from ghrah.skills.lock import (
    INITIAL_VERSION,
    LOCK_FILE_NAME,
    LOCK_HEADER,
    LOCK_SCHEMA_VERSION,
    build_lock,
    create_lock,
    extract_permission_request,
    fingerprint_skill,
    read_fingerprint,
    read_lock,
    render_lock,
)
from ghrah.skills.models import (
    LockOutcome,
    ScanIssue,
    SkillDocument,
    SkillFingerprint,
    SkillInitResult,
    SkillRoot,
    SkillScanResult,
    SkillSummary,
)
from ghrah.skills.provider import SkillAbilityProvider, default_provider
from ghrah.skills.query import query_fingerprint, query_fingerprints
from ghrah.skills.roots import (
    DEFAULT_SKILL_ROOT_RANK,
    DEFAULT_SKILL_ROOT_SOURCE,
    default_pool,
    default_skill_root,
    ensure_roots,
    pool_from_root,
)
from ghrah.skills.scan import (
    SKILL_FILE_NAME,
    SKILL_NAME_RE,
    parse_skill_file,
    scan_skills,
    split_frontmatter,
)

__all__ = [
    "CATALOG_DESCRIPTION_MAX_CHARS",
    "DEFAULT_SKILL_ROOT_RANK",
    "DEFAULT_SKILL_ROOT_SOURCE",
    "SkillAbilityProvider",
    "INITIAL_VERSION",
    "LOCK_FILE_NAME",
    "LOCK_HEADER",
    "LOCK_SCHEMA_VERSION",
    "SKILL_FILE_NAME",
    "SKILL_NAME_RE",
    "LockOutcome",
    "ScanIssue",
    "SkillError",
    "SkillDocument",
    "SkillFingerprint",
    "SkillInitResult",
    "SkillLockError",
    "SkillNotFoundError",
    "SkillParseError",
    "SkillRoot",
    "SkillScanResult",
    "SkillSummary",
    "build_lock",
    "catalog_description",
    "create_lock",
    "default_pool",
    "default_provider",
    "default_skill_root",
    "digest_catalog",
    "ensure_roots",
    "extract_permission_request",
    "fingerprint_skill",
    "initialize_skills",
    "parse_skill_file",
    "pool_from_root",
    "query_fingerprint",
    "query_fingerprints",
    "read_fingerprint",
    "read_lock",
    "read_skill_document",
    "render_catalog_message",
    "render_lock",
    "scan_skills",
    "split_frontmatter",
]
