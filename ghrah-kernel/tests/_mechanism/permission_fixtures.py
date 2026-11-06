"""受信实验许可与显式门闩；这不是 Authority 或密码证明。"""

import asyncio
import time

from .contracts import BOOTSTRAP, OBSERVER


class PermissionFixtures:
    def __init__(self):
        self.gates: dict[str, asyncio.Event] = {}
        self.pending_opens: set[str] = set()
        self.active_calls: set[str] = set()
        self.opened = 0
        self.closed = 0
        self.pending_setups: set[str] = set()
        self.endpoint_dropped = False

    def gate(self, name):
        return self.gates.setdefault(name, asyncio.Event())

    async def issue(self, name, spec, meta):
        path = meta["path"].strip("/") or "main"
        if path not in {
            "main",
            "readonly",
            "short",
            "same",
            "none",
            "slow",
            "expiring-open",
            "renewed",
            "slow-subscribe",
            "slow-channel",
            "bad-peer",
            "reject-channel",
        }:
            raise ValueError("资源路径不存在")
        if name != "ability" and path != "main":
            raise ValueError("此方案没有许可 fixture 子路径")
        ttl = 0.12 if path == "short" else (0.05 if path == "expiring-open" else 60)
        grant = {
            "principal": meta["principal"],
            "grant_id": path,
            "revision": 1,
            "allowed_actions": ["echo", "identity"] if path == "readonly" else spec["actions"],
            "expiry": time.monotonic() + ttl,
            "transfer_policy": {"kind": "allowlist", "principals": [OBSERVER, BOOTSTRAP]},
            "depth_left": 1,
        }
        if path in ("none", "same"):
            grant["transfer_policy"] = {"kind": "none" if path == "none" else "same_principal"}
        if path == "renewed":
            grant["grant_id"], grant["revision"] = "main", 2
        if path in ("slow", "expiring-open"):
            self.pending_opens.add(path)
            try:
                await self.gate(f"open:{path}").wait()
            finally:
                self.pending_opens.discard(path)
        self.opened += 1
        return grant

    def probe(self, arguments):
        return {
            "pending_opens": sorted(self.pending_opens),
            "active_calls": sorted(self.active_calls),
            "pending_setups": sorted(self.pending_setups),
            "opened": self.opened,
            "closed": self.closed,
        }

    def release(self, arguments):
        self.gate(arguments["gate"]).set()
        return {"released": True}
