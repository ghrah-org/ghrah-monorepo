# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ManifestStore 模块：Subject 端的 Manifest 文件存储和管理。

提供文件系统上的 Manifest CRUD 存储、内置能力同步和命令分发。
"""

from ghrah.subject.manifest_store.store import ManifestStore

__all__ = ["ManifestStore"]
