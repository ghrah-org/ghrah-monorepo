# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Core 的 sqlite 读取模块——**全仓唯一允许直接读表的地方**。

规则只有一条：**表的读取只能发生在这一层**。

```
read/            ← 唯一知道表结构、SQL、软删除语义的地方
  ↑ 返回 DTO（views）
api/handlers     ← 只做「能力名 → read 调用」的编排
  ↑
api/capabilities ← 能力清单（名字 + 请求信封 + 出参 DTO）
  ↑
ports            ← 消费方看到的协议
```

因此：

- 消费方（Subject）**没有**任何读表的途径，只能调能力；
- 要读新的表 / 新的字段 → **先在这里加方法，再在能力清单加一条**，不能绕过；
- 读法有歧义（要不要过滤软删除、空数据是空结果还是 NOT_FOUND）时，
  **以既有面向用户的行为为准**，并在方法 docstring 里写明。

按表族分文件：``chain``（ActionChain 链数据）、后续 ``mailbox``（mm_*）、
``ability``（授权/内容/已安装包）、``manifest``（Agent 定义目录）。
"""

from __future__ import annotations

from ghrah.core.read.chain import (
    ChainDatabaseUnavailableError,
    ChainReadStore,
    DbPathResolver,
)
from ghrah.core.read.mailbox import (
    MailboxBadPageSizeError,
    MailboxProjectMismatchError,
    MailboxReadStore,
    MailboxUnavailableError,
    MessageNotFoundError,
)

__all__ = [
    "ChainDatabaseUnavailableError",
    "ChainReadStore",
    "DbPathResolver",
    "MailboxBadPageSizeError",
    "MailboxProjectMismatchError",
    "MailboxReadStore",
    "MailboxUnavailableError",
    "MessageNotFoundError",
]
