# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""基础入口清单的冻结快照与自省一致性。"""

import asyncio

from ghrah_kernel.abi import ABI_ENTRIES
from ghrah_kernel.kernel.builtins import invoke_builtin
from ghrah_kernel.kernel.runtime import Kernel
from ghrah_kernel.payloads import decode_payload, encode_payload

# 任何增删都必须显式改这份快照，即一次显式的入口升版动作。
SNAPSHOT = (
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


def test_abi_entries_match_frozen_snapshot():
    assert ABI_ENTRIES == SNAPSHOT


def test_abi_entry_count_is_fourteen():
    assert len(ABI_ENTRIES) == 14


def test_kernel_sys_reports_the_frozen_entries():
    async def scenario():
        kernel = Kernel()
        try:
            payload, _effect = await invoke_builtin(
                kernel, None, "kernel.sys", "stats", encode_payload({}), None
            )
            return decode_payload(payload)["implemented_abi"]
        finally:
            await kernel.aclose()

    assert asyncio.run(scenario()) == list(ABI_ENTRIES)
