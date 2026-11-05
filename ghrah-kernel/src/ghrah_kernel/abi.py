# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""微内核基础入口的唯一权威清单。"""

ABI_ENTRIES = (
    "hello",
    "register",
    "open",
    "dup",
    "invoke",
    "wait",
    "cancel",
    "close",
    "revoke",
    "subscribe",
    "channel",
    "shutdown",
    "read",
    "write",
)
