# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""有界派生记录、可信许可声明核定与同步撤权门禁。"""

import asyncio
import math

from .tables import Handle, KernelFault


class References:
    def __init__(self, kernel):
        self.kernel = kernel
        self.nodes: dict[str, Handle] = {}
        self.bases: dict[tuple[str, str], int] = {}
        self.domains: set[str] = set()
        self.revision = 0
        self.max_nodes, self.max_tombstones, self.max_handles = 2048, 256, 64

    def capacity(self, session):
        if len(self.nodes) >= self.max_nodes or len(session.handles) >= self.max_handles:
            raise KernelFault("QUOTA_EXCEEDED", "派生记录或会话句柄额度已满")

    def check(self, handle):
        now = asyncio.get_running_loop().time()
        current = handle
        while current is not None:
            if current.revoked_at is not None:
                raise KernelFault("REVOKED", "引用的授权分支已撤销")
            if current.expiry <= now:
                raise KernelFault("EXPIRED", "许可期限已到")
            if current.basis is not None:
                scheme_id, grant_id, revision = current.basis
                if self.bases.get((scheme_id, grant_id), 0) >= revision:
                    raise KernelFault("REVOKED", "许可根已撤销")
                scheme = self.kernel.schemes[scheme_id]
                if scheme.domain_id in self.domains:
                    raise KernelFault("REVOKED", "精确服务实例已撤权")
            current = self.nodes[current.parent] if current.parent else None

    def install(self, session, obj, actions, *, parent=None, grant=None, root=False):
        self.capacity(session)
        if parent is not None:
            self.check(parent)
        handle = Handle(
            self.kernel.new_id("handle"),
            obj.object_id,
            frozenset(actions),
            owner_id=session.session_id,
            parent=parent.handle_id if parent else None,
            can_open=root,
            basis=(obj.scheme_id, "root", 1),
            kind=obj.kind,
        )
        if grant is not None:
            handle.basis = grant["basis"]
            handle.expiry = grant["expiry"]
            handle.transfer_kind, handle.allowlist = grant["kind"], grant["allowlist"]
            handle.depth_left = grant["depth"]
        if parent is not None:
            handle.expiry = min(handle.expiry, parent.expiry)
        self.check(handle)
        self.nodes[handle.handle_id] = handle
        session.handles[handle.handle_id] = handle
        obj.refs += 1
        return handle

    def grant(self, session, scheme, reply):
        """来源绑定到实际应答 Scheme；不接受 Provider 自报权威名覆盖来源。"""
        grant = reply["grant"]
        if grant["principal"] != session.principal:
            raise KernelFault("ACCESS_DENIED", "许可主体与连接主体不同")
        grant_id, revision = grant["grant_id"], grant["revision"]
        expiry, depth = grant["expiry"], grant["depth_left"]
        if (
            not isinstance(grant_id, str)
            or not grant_id
            or type(revision) is not int
            or revision < 1
            or type(depth) is not int
            or not 0 <= depth <= 4
            or type(expiry) not in (int, float)
            or not (math.isfinite(expiry) or expiry == math.inf)
        ):
            raise KernelFault("PROTOCOL_ERROR", "许可身份、期限或深度无效")
        policy = grant["transfer_policy"]
        kind = policy["kind"]
        allowlist = self.strings(policy.get("principals", []))
        actions = self.strings(grant["allowed_actions"])
        if kind not in ("none", "same_principal", "allowlist") or not actions <= scheme.actions:
            raise KernelFault("PROTOCOL_ERROR", "许可策略或动作上限无效")
        return {
            "basis": (scheme.scheme_id, grant_id, revision),
            "expiry": expiry,
            "depth": depth,
            "kind": kind,
            "allowlist": allowlist,
            "actions": actions,
        }

    @staticmethod
    def strings(values):
        if not isinstance(values, (list, tuple)) or not all(isinstance(x, str) for x in values):
            raise KernelFault("INVALID_ARGUMENT", "权限和主体集合必须为字符串列表")
        return frozenset(values)

    def dup(self, session, meta):
        source, obj, _ = self.kernel._target(session, meta["handle"])
        if source.kind != "resource":
            raise KernelFault("INVALID_ARGUMENT", "订阅、定时器与流端点不支持 dup")
        # 管理根可同会话衰减，不在本轮开放跨会话管理授权。
        target = self.kernel.sessions.get(meta.get("target_session", session.session_id))
        if target is None or not target.active:
            raise KernelFault("SESSION_REQUIRED", "目标会话不活跃")
        actions = self.strings(meta.get("actions", list(source.actions)))
        expiry = meta.get("expiry", source.expiry)
        depth = meta.get("depth_left", source.depth_left)
        can_open = meta.get("can_open", source.can_open)
        if (
            type(expiry) not in (int, float)
            or math.isnan(expiry)
            or expiry > source.expiry
            or expiry <= asyncio.get_running_loop().time()
            or type(depth) is not int
            or not 0 <= depth <= source.depth_left
            or type(can_open) is not bool
            or (can_open and not source.can_open)
            or not actions <= source.actions
        ):
            raise KernelFault("ACCESS_DENIED", "派生不得增加动作、期限或转交深度")
        policy = meta.get(
            "transfer_policy", {"kind": source.transfer_kind, "principals": list(source.allowlist)}
        )
        kind, allowed = policy["kind"], self.strings(policy.get("principals", []))
        # 策略只允许保持原类型并收窄名单，或降为 none。
        if kind != "none" and (kind != source.transfer_kind or not allowed <= source.allowlist):
            raise KernelFault("ACCESS_DENIED", "不得扩大未来转交策略")
        crossing = target is not session
        if crossing:
            if not target.accept_transfers:
                raise KernelFault("TRANSFER_NOT_ACCEPTED", "目标没有选择接受转交")
            permitted = (
                source.transfer_kind == "same_principal" and session.principal == target.principal
            ) or (source.transfer_kind == "allowlist" and target.principal in source.allowlist)
            if not permitted or source.depth_left == 0:
                raise KernelFault("ACCESS_DENIED", "许可不允许向目标主体继续转交")
            depth = min(depth, source.depth_left - 1)
            if target.events.full():
                raise KernelFault("EVENT_QUEUE_FULL", "无法为安装事实预留容量")
        child = self.install(
            target,
            obj,
            actions,
            parent=source,
            grant={
                "basis": source.basis,
                "expiry": expiry,
                "depth": depth,
                "kind": kind,
                "allowlist": allowed,
            },
        )
        child.can_open = can_open
        receipt = {
            "handle_id": child.handle_id,
            "target_session": target.session_id,
            "parent": source.handle_id,
            "depth_left": child.depth_left,
        }
        if crossing:
            target.events.put_nowait({"type": "handle_installed", **receipt})
            self.kernel.changed.set()
        return receipt

    def descendant(self, node, ancestors):
        while node is not None:
            if node.handle_id in ancestors:
                return True
            node = self.nodes[node.parent] if node.parent else None
        return False

    async def revoke(self, session, selector):
        kind = selector["type"]
        tombstone = None
        if kind == "handle":
            node = self.nodes.get(selector["handle"])
            if node is None:
                raise KernelFault("INVALID_HANDLE", "未知派生记录")
            if session.role != "bootstrap" and node.owner_id != session.session_id:
                raise KernelFault("ACCESS_DENIED", "不能撤销其他会话的派生分支")
            selected = [node]
        else:
            if session.role != "bootstrap":
                raise KernelFault("ACCESS_DENIED", "没有许可根或实例撤权资格")
            if kind == "basis":
                sid, grant_id, revision = (
                    selector["scheme_id"],
                    selector["grant_id"],
                    selector["revision"],
                )
                if sid not in self.kernel.schemes or not isinstance(grant_id, str) or not grant_id:
                    raise KernelFault("INVALID_ARGUMENT", "许可根必须绑定已登记方案")
                if type(revision) is not int or revision < 1:
                    raise KernelFault("INVALID_ARGUMENT", "许可修订无效")
                tombstone = (sid, grant_id)
                changed = self.bases.get(tombstone, 0) < revision
                selected = [
                    x
                    for x in self.nodes.values()
                    if x.basis and x.basis[:2] == tombstone and x.basis[2] <= revision
                ]
            elif kind == "domain_generation":
                domain = self.kernel.processes.get(selector["domain"])
                if selector["generation"] != domain.generation:
                    raise KernelFault("STALE_REFERENCE", "实例代次不匹配")
                tombstone = domain.domain_id
                changed = tombstone not in self.domains
                selected = [
                    x
                    for x in self.nodes.values()
                    if x.basis and self.kernel.schemes[x.basis[0]].domain_id == domain.domain_id
                ]
            else:
                raise KernelFault("NOT_SUPPORTED", "本轮只支持 handle/basis/domain_generation")
            new_entry = (
                tombstone not in self.bases if kind == "basis" else tombstone not in self.domains
            )
            if new_entry and len(self.bases) + len(self.domains) >= self.max_tombstones:
                raise KernelFault("QUOTA_EXCEEDED", "撤权墓碑已满，不淘汰可重放记录")
            # 包括尚在 open 慢路径、还没有句柄的许可根。
            if changed:
                if kind == "basis":
                    self.bases[tombstone] = revision
                else:
                    self.domains.add(tombstone)
        roots = {x.handle_id for x in selected}
        selected = [x for x in self.nodes.values() if self.descendant(x, roots)]
        channel_objects = {x.object_id for x in selected if x.kind == "channel"}
        selected += [
            x for x in self.nodes.values() if x.object_id in channel_objects and x not in selected
        ]
        fresh = [x for x in selected if x.revoked_at is None]
        changed = bool(fresh) or (tombstone is not None and changed)
        if changed:
            self.revision += 1
        for node in fresh:
            node.revoked_at = self.revision
        # 从这里起，所有资格检查已按新状态运行；异步清理不控制撤权生效。
        for invocation in list(self.kernel.invocations.values()):
            if invocation.source and not invocation.terminal.is_set():
                try:
                    self.check(self.nodes[invocation.source])
                except KernelFault:
                    self.kernel.calls.interrupt(invocation, "REVOKED")
        for node in fresh:
            self.kernel.events.invalidate(node)
            self.kernel.streams.invalidate(node)
            self.kernel.drop_reference(node)
        return {
            "revision": self.revision,
            "invalidated": len(fresh),
            "cleanup_pending": bool(self.kernel._maintenance),
        }
