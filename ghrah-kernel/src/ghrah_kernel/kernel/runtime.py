"""会话、方案、句柄和调用路由；用户态业务载荷不在此解码。"""

import asyncio
import itertools
import os
import uuid
from collections import deque

from ..context import CallerBinding, ProviderContext
from ..endpoint import AsyncEndpoint
from ..launch import LaunchCatalog
from ..protocol import Envelope, ErrorRecord, ProtocolError, decode_envelope, encode_envelope
from ..wire import MessageConnection, MessageTooLarge
from .calls import Calls
from .events import Events
from .processes import Processes
from .references import References
from .shutdown import Shutdown
from .streams import Streams
from .tables import Invocation, KernelFault, ObjectRecord, Scheme, Session


class Kernel:
    def __init__(self, *, launch_catalog: LaunchCatalog | None = None):
        self.boot_id = uuid.uuid4().hex
        self._ids = itertools.count(1)
        self.sessions: dict[str, Session] = {}
        self.schemes: dict[str, Scheme] = {}
        self.objects: dict[str, ObjectRecord] = {}
        self.invocations: dict[str, Invocation] = {}
        self.enrollments: dict[str, str] = {}
        self.launch_catalog = launch_catalog if launch_catalog is not None else LaunchCatalog()
        self.processes = Processes(self)
        self.references = References(self)
        self.calls = Calls(self)
        self.changed = asyncio.Event()
        self.events = Events(self)
        self.streams = Streams(self)
        self.shutdown = Shutdown(self)
        self._requests = {}
        self._shutdown_exit_task = None
        self._maintenance: set[asyncio.Task] = set()
        self.root_ns: str | None = None
        self.stop = asyncio.Event()
        self._closing = False
        self._connections: list[tuple[AsyncEndpoint, asyncio.Task]] = []
        self._register_lock = asyncio.Lock()
        self.errors: list[str] = []
        self.fact_revision = 0
        self.policy_requests: dict[str, dict] = {}
        self.policy_decisions = deque(maxlen=64)
        self.unhandled_policy = []
        self.abi_calls = {}
        self.proc_id = self.new_id("scheme")
        self.sys_id = self.new_id("scheme")
        self.time_id = self.new_id("scheme")
        self.schemes[self.proc_id] = Scheme(
            self.proc_id,
            None,
            "kernel.proc",
            "kernel.proc/e0",
            frozenset(
                {"spawn", "kill", "status", "wait", "drain", "retire", "snapshot", "ack_policy"}
            ),
        )
        self.schemes[self.sys_id] = Scheme(
            self.sys_id, None, "kernel.sys", "kernel.sys/e0", frozenset({"info", "stats"})
        )
        self.schemes[self.time_id] = Scheme(
            self.time_id,
            None,
            "kernel.time",
            "kernel.time/e0",
            frozenset({"now", "create_timer", "cancel_timer"}),
        )

    def new_id(self, kind: str) -> str:
        return f"{self.boot_id}:{kind}:{next(self._ids)}"

    def notify_supervision(self, event):
        self.fact_revision += 1
        for session in self.sessions.values():
            if session.active and session.role == "bootstrap":
                if session.supervision.full():
                    session.supervision_gap = True
                else:
                    session.supervision.put_nowait({**event, "revision": self.fact_revision})
        self.changed.set()

    def acknowledge_policy(self, arguments):
        reference = arguments["invocation_id"]
        record = self.policy_requests.get(reference) or next(
            (x for x in self.policy_decisions if x["invocation_id"] == reference), None
        )
        if record is None or record["status"] == "reserved":
            raise KernelFault("NOT_FOUND", "没有该待处理超时事实")
        if (arguments["domain_id"], arguments["generation"]) != (
            record["domain_id"],
            record["generation"],
        ):
            raise KernelFault("STALE_REFERENCE", "策略回执的精确实例不匹配")
        decision = arguments["decision"]
        if decision not in ("keep", "kill", "stale"):
            raise KernelFault("INVALID_ARGUMENT", "未知策略回执")
        if record["status"] == "acked":
            if record["decision"] != decision:
                raise KernelFault("REGISTRATION_CONFLICT", "同一策略事实的回执不同")
            return record
        result = {**record, "status": "acked", "decision": decision}
        self.policy_requests.pop(reference)
        self.policy_decisions.append(result)
        return result

    def maintain(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._maintenance.add(task)

        def finished(task):
            self._maintenance.discard(task)
            if not task.cancelled() and task.exception():
                self.errors.append(repr(task.exception()))

        task.add_done_callback(finished)

    def attach_caller(
        self,
        connection,
        credential: str,
        role: str,
        domain=None,
        *,
        principal=None,
        resolution_scope=None,
        diagnostics=False,
    ) -> None:
        binding = CallerBinding(principal, resolution_scope, diagnostics)
        endpoint = AsyncEndpoint(MessageConnection(connection))
        task = asyncio.create_task(self._serve_caller(endpoint, credential, role, domain, binding))
        self._connections.append((endpoint, task))

    async def _serve_caller(self, endpoint, credential, role, domain, binding) -> None:
        session = None
        requests: set[asyncio.Task] = set()
        last_id = 0
        try:
            while True:
                message = decode_envelope(await endpoint.receive())
                if message.kind != "request":
                    raise ProtocolError("Caller 只能发送请求")
                if message.req_id <= last_id:
                    await endpoint.send(
                        encode_envelope(
                            Envelope(
                                "response",
                                message.req_id,
                                error=ErrorRecord("REQUEST_REPLAYED", "旧请求序号不能再次执行"),
                            )
                        )
                    )
                    continue
                last_id = message.req_id
                if message.meta.get("op") == "hello" and session is None:
                    if self.shutdown.active or self._closing:
                        await endpoint.send(
                            encode_envelope(
                                Envelope(
                                    "response",
                                    message.req_id,
                                    error=ErrorRecord("SHUTTING_DOWN", "内核已关闭接纳"),
                                )
                            )
                        )
                        continue
                    if message.meta.get("credential") != credential:
                        await endpoint.send(
                            encode_envelope(
                                Envelope(
                                    "response",
                                    message.req_id,
                                    error=ErrorRecord("ACCESS_DENIED", "会话凭证不匹配"),
                                )
                            )
                        )
                        continue
                    if message.meta.get("requires_ns_root", True) and (
                        self.root_ns is None or binding.resolution_scope is None
                    ):
                        await endpoint.send(
                            encode_envelope(
                                Envelope(
                                    "response",
                                    message.req_id,
                                    error=ErrorRecord("PROVIDER_UNAVAILABLE", "命名根尚未就绪"),
                                )
                            )
                        )
                        continue
                    if any(
                        type(message.meta.get(key, False)) is not bool
                        for key in ("accept_transfers", "accept_channels")
                    ):
                        raise ProtocolError("接收偏好必须为布尔值")
                    try:
                        session = self._new_session(
                            role,
                            domain,
                            binding.principal,
                            message.meta.get("accept_transfers", False),
                            message.meta.get("accept_channels", False),
                            resolution_scope=binding.resolution_scope,
                            diagnostics=binding.diagnostics,
                        )
                    except KernelFault as exc:
                        await endpoint.send(
                            encode_envelope(
                                Envelope(
                                    "response",
                                    message.req_id,
                                    error=ErrorRecord(exc.code, str(exc)),
                                )
                            )
                        )
                        continue
                task = asyncio.create_task(self._answer(endpoint, session, message))
                requests.add(task)
                self._requests[task] = message.meta.get("op")
                task.add_done_callback(lambda task: self._requests.pop(task, None))
                task.add_done_callback(requests.discard)
        except EOFError, OSError, ConnectionError, ProtocolError:
            pass
        finally:
            if role == "bootstrap":
                self.stop.set()
            if session is not None:
                session.active = False
            for task in requests:
                task.cancel()
            await asyncio.gather(*requests, return_exceptions=True)
            if session is not None:
                for handle in list(session.handles):
                    await self._close(session, handle)
                for invocation in list(self.invocations.values()):
                    if invocation.owner is session:
                        invocation.observed = False
                        if invocation.task.done():
                            self.invocations.pop(invocation.invocation_id, None)
            await endpoint.aclose()

    def _new_session(
        self,
        role,
        domain,
        principal,
        accept_transfers,
        accept_channels=False,
        *,
        resolution_scope=None,
        diagnostics=False,
    ) -> Session:
        CallerBinding(principal, resolution_scope, diagnostics)
        has_root = self.root_ns is not None and resolution_scope is not None
        needed = 2 + has_root + (role == "bootstrap")
        if len(self.references.nodes) + needed > self.references.max_nodes:
            raise KernelFault("QUOTA_EXCEEDED", "无法为会话保留初始引用")
        session = Session(
            self.new_id("session"),
            role,
            domain,
            principal=principal,
            resolution_scope=resolution_scope,
            accept_transfers=accept_transfers,
            accept_channels=accept_channels,
        )
        try:
            session.hello = {
                "session_id": session.session_id,
                "boot_id": self.boot_id,
                "kernel_pid": os.getpid(),
                "principal": session.principal,
                "domain_id": session.domain_id,
                "system_events": self.new_id("system_events"),
                "resolution_scope": resolution_scope,
                "ns_root_handle": self._install(
                    session, self.root_ns, resolution_scope, set(), root=True
                )
                if has_root
                else None,
                "sys_handle": self._root_handle(
                    session, self.sys_id, {"info", "stats"} if diagnostics else {"info"}
                ),
                "time_handle": self._root_handle(
                    session, self.time_id, self.schemes[self.time_id].actions
                ),
            }
            if role == "bootstrap":
                session.hello["proc_handle"] = self._root_handle(
                    session, self.proc_id, self.schemes[self.proc_id].actions
                )
                session.hello["supervision_events"] = self.new_id("supervision_events")
        except KernelFault:
            for handle in session.handles.values():
                self.objects.pop(handle.object_id, None)
                self.references.nodes.pop(handle.handle_id, None)
            raise
        self.sessions[session.session_id] = session
        return session

    def _install(
        self,
        session,
        scheme_id,
        provider_object_id,
        actions,
        *,
        root=False,
        parent=None,
        grant=None,
    ) -> str:
        self.references.capacity(session)
        obj = ObjectRecord(self.new_id("object"), scheme_id, provider_object_id, root=root)
        handle = self.references.install(
            session, obj, actions, parent=parent, grant=grant, root=root
        )
        self.objects[obj.object_id] = obj
        return handle.handle_id

    def _root_handle(self, session, scheme_id, actions) -> str:
        return self._install(session, scheme_id, "root", actions, root=True)

    def source_live(self, session, source):
        if not session.active or source.handle_id not in session.handles:
            raise KernelFault("INVALID_HANDLE", "建立期间源引用或会话已关闭")
        self._target(session, source.handle_id)

    def derived(self, session, obj, parent, actions):
        node = self.references.install(
            session,
            obj,
            actions,
            parent=parent,
            grant={
                "basis": parent.basis,
                "expiry": parent.expiry,
                "kind": "none",
                "allowlist": frozenset(),
                "depth": 0,
            },
        )
        self.objects[obj.object_id] = obj
        return node

    def drop_reference(self, node):
        if node.live_ref:
            node.live_ref = False
            obj = self.objects[node.object_id]
            obj.refs -= 1
            self.maintain(self._release(obj))

    async def close_provider(self, domain, scheme, reference):
        if domain.state in ("ready", "draining") and not domain.reaped:
            try:
                await self._rpc(
                    domain, {"op": "close", "scheme_id": scheme, "provider_object_id": reference}
                )
            except KernelFault:
                pass

    def _target(self, session, reference):
        handle = session.handles.get(reference)
        if handle is None:
            raise KernelFault("INVALID_HANDLE", "句柄不属于当前会话")
        if handle.kind != "resource":
            raise KernelFault("INVALID_ARGUMENT", "该操作需要普通资源句柄")
        self.references.check(handle)
        obj = self.objects[handle.object_id]
        scheme = self.schemes[obj.scheme_id]
        if scheme.domain_id is not None:
            domain = self.processes.get(scheme.domain_id)
            if domain.state in ("failed", "stopped") or domain.reaped:
                raise KernelFault("STALE_REFERENCE", "目标实例已经终结")
            if domain.state != "ready":
                raise KernelFault("PROVIDER_UNAVAILABLE", "目标未就绪")
        return handle, obj, scheme

    async def _answer(self, endpoint, session, request):
        try:
            if session is None or not session.active:
                raise KernelFault("SESSION_REQUIRED", "须先建立会话")
            meta, payload = await self._dispatch(session, request)
            reply = Envelope("response", request.req_id, meta, payload)
        except KernelFault as exc:
            reply = Envelope("response", request.req_id, error=ErrorRecord(exc.code, str(exc)))
        except (KeyError, TypeError, ValueError) as exc:
            reply = Envelope(
                "response", request.req_id, error=ErrorRecord("INVALID_ARGUMENT", str(exc))
            )
        except Exception as exc:
            self.errors.append(repr(exc))
            reply = Envelope(
                "response", request.req_id, error=ErrorRecord("INTERNAL_ERROR", repr(exc))
            )
        operation = request.meta.get("op")
        if isinstance(operation, str):
            counts = self.abi_calls.setdefault(operation, {"accepted": 0, "rejected": 0})
            counts["rejected" if reply.error else "accepted"] += 1
        try:

            def prepare():
                try:
                    outgoing = self.calls.delivery(session, request, reply)
                    outgoing = self.events.delivery(session, outgoing)
                    outgoing = self.streams.delivery(session, outgoing)
                except KernelFault as exc:
                    outgoing = Envelope(
                        "response", request.req_id, error=ErrorRecord(exc.code, str(exc))
                    )
                return encode_envelope(outgoing)

            await endpoint.send(b"", prepare=prepare)
        except ConnectionError, OSError:
            pass
        finally:
            if request.meta.get("op") == "shutdown" and not reply.error:
                # 报告提交给管理连接后才退出宿主，不能靠关闭连接模拟成功回执。
                if self._shutdown_exit_task is None:
                    self._shutdown_exit_task = asyncio.create_task(
                        self._finish_shutdown_responses()
                    )

    async def _finish_shutdown_responses(self):
        replies = [task for task, op in self._requests.items() if op == "shutdown"]
        if replies:
            await asyncio.wait(replies, timeout=0.1)
        self.stop.set()

    async def _dispatch(self, session, request):
        meta, op = request.meta, request.meta.get("op")
        if op == "hello":
            return session.hello, None
        if op == "shutdown":
            return await self.shutdown.request(session, meta), None
        if self._closing:
            raise KernelFault("PROVIDER_UNAVAILABLE", "实验正在结束")
        if self.shutdown.active and op not in ("wait", "close", "cancel", "read", "write"):
            raise KernelFault("SHUTTING_DOWN", "内核已关闭新工作接纳")
        if op == "register":
            if session.role != "bootstrap":
                raise KernelFault("ACCESS_DENIED", "会话无注册资格")
            async with self._register_lock:
                return await self._register(session, meta), None
        if op == "open":
            opening = asyncio.create_task(self._open(session, meta))
            try:
                return await asyncio.shield(opening), None
            except asyncio.CancelledError:
                # 会话已标记失活；迟到 OpenOk 仍需完成安装前复核及关闭。
                await asyncio.gather(opening, return_exceptions=True)
                raise
        if op in ("subscribe", "channel"):
            operation = self.events.subscribe if op == "subscribe" else self.streams.create
            establishing = asyncio.create_task(operation(session, meta))
            try:
                return await asyncio.shield(establishing), None
            except asyncio.CancelledError:
                await asyncio.gather(establishing, return_exceptions=True)
                raise
        if op in ("read", "write"):
            return await self.streams.io(session, request)
        if op == "dup":
            return self.references.dup(session, meta), None
        if op == "revoke":
            return await self.references.revoke(session, meta["selector"]), None
        if op == "cancel":
            invocation = self.calls.owned(session, meta["invocation"])
            accepted = self.calls.interrupt(invocation, "CANCELLED")
            return {"accepted": accepted, "terminal": invocation.terminal.is_set()}, None
        if op == "invoke":
            return self.calls.invoke(session, request), None
        if op == "wait":
            if "invocation" in meta:
                return await self.calls.wait(session, meta)
            return await self.events.wait(session, meta)
        if op == "close":
            return await self._close(session, meta["reference"]), None
        raise KernelFault("NOT_SUPPORTED", "尚未实现的实验入口")

    async def _rpc(self, domain, meta, payload=None, *, prepare=None, reply_timeout=2):
        try:
            reply = await domain.peer.exchange(
                meta, payload, prepare=prepare, timeout=reply_timeout
            )
        except MessageTooLarge as exc:
            raise KernelFault("MESSAGE_TOO_LARGE", str(exc)) from exc
        except TimeoutError as exc:
            raise KernelFault("RPC_TIMEOUT", "回复等待到期，不能据此判定端点失效") from exc
        except (ConnectionError, OSError) as exc:
            if not domain.expected_stop and domain.state not in ("stopped", "failed"):
                domain.state = "failed"
                self.notify_supervision({"type": "endpoint_failed", "domain_id": domain.domain_id})
            raise KernelFault("PROVIDER_UNAVAILABLE", "提供者端点失效") from exc
        return reply

    @staticmethod
    def _require_ok(reply):
        if reply.error is not None:
            raise KernelFault(reply.error.code, reply.error.message)
        return reply.meta

    async def _register(self, session, meta):
        domain = self.processes.get(meta["domain"])
        specs, key = meta["schemes"], meta["registration_key"]
        if not isinstance(key, str) or not key or not isinstance(specs, list) or not specs:
            raise KernelFault("INVALID_ARGUMENT", "必须提供方案包和注册键")
        if domain.reaped or domain.state in ("failed", "stopped"):
            raise KernelFault("DOMAIN_RETIRED", "域已终结")
        if domain.registration is not None:
            old_key, old_specs, result = domain.registration
            if key == old_key and specs == old_specs:
                return result
            raise KernelFault("REGISTRATION_CONFLICT", "该代次已提交不可变方案包")
        described = self._require_ok(
            await self._rpc(
                domain,
                {
                    "op": "describe",
                    "enrollment": self.enrollments[domain.domain_id],
                },
            )
        )
        if self.shutdown.active or self._closing:
            raise KernelFault("SHUTTING_DOWN", "方案包提交前已关闭接纳")
        if len({spec["name"] for spec in specs}) != len(specs):
            raise KernelFault("CONTRACT_MISMATCH", "同域方案名重复")
        if sorted(specs, key=lambda x: x["name"]) != sorted(
            described["schemes"], key=lambda x: x["name"]
        ):
            raise KernelFault("CONTRACT_MISMATCH", "方案包与提供者声明不一致")
        records = [
            Scheme(
                self.new_id("scheme"),
                domain.domain_id,
                spec["name"],
                spec["contract"],
                frozenset(spec["actions"]),
            )
            for spec in specs
        ]
        if (
            len(session.handles) + len(records) > self.references.max_handles
            or len(self.references.nodes) + len(records) > self.references.max_nodes
        ):
            raise KernelFault("QUOTA_EXCEEDED", "整包发布缺少根引用额度")
        if domain.domain_id in self.references.domains:
            raise KernelFault("REVOKED", "实例已撤权，不能发布根引用")
        mapping = {record.name: record.scheme_id for record in records}
        self._require_ok(await self._rpc(domain, {"op": "bind_schemes", "schemes": mapping}))
        if domain.reaped or domain.state != "starting":
            raise KernelFault("DOMAIN_RETIRED", "核定期间域已终结")
        for record in records:
            self.schemes[record.scheme_id] = record
        domain.schemes, domain.state = mapping, "ready"
        roots = {
            record.name: self._root_handle(session, record.scheme_id, record.actions)
            for record in records
        }
        if domain.kind == "namespace":
            self.root_ns = mapping["ns"]
        result = {
            **domain.snapshot(),
            "scheme_handles": roots,
            "scheme_refs": {
                record.name: {
                    "scheme_id": record.scheme_id,
                    "domain_generation": domain.generation,
                }
                for record in records
            },
        }
        domain.registration = (key, specs, result)
        return result

    async def _open(self, session, meta):
        parent, base, scheme = self._target(session, meta["base"])
        if not parent.can_open:
            raise KernelFault("ACCESS_DENIED", "此引用没有打开子资源的权限")
        path = meta["path"]
        if not isinstance(path, str):
            raise KernelFault("INVALID_ARGUMENT", "路径必须为字符串")
        provider_object = base.provider_object_id
        for _ in range(8):
            if scheme.domain_id is None:
                raise KernelFault("NOT_SUPPORTED", "内置根不支持子路径")
            domain = self.processes.get(scheme.domain_id)
            if domain.state != "ready" or domain.reaped:
                raise KernelFault("STALE_REFERENCE", "解析目标实例已终结")

            def prepare(request):
                self.references.check(parent)
                if not session.active or domain.state != "ready" or domain.reaped:
                    raise KernelFault("STALE_REFERENCE", "打开提交前会话或目标失效")
                if domain.domain_id in self.references.domains:
                    raise KernelFault("REVOKED", "目标实例已撤权")
                return request

            reply = self._require_ok(
                await self._rpc(
                    domain,
                    {
                        "op": "open",
                        "scheme_id": scheme.scheme_id,
                        "provider_object_id": provider_object,
                        "path": path,
                        "principal": session.principal,
                        "context": ProviderContext(
                            session.principal,
                            session.session_id,
                            session.domain_id,
                            session.resolution_scope,
                            scheme.scheme_id,
                            provider_object,
                        ).to_dict(),
                    },
                    prepare=prepare,
                )
            )
            if reply["result"] == "redirect":
                target = self.schemes.get(reply["scheme_id"])
                if target is None or target.domain_id is None:
                    raise KernelFault("NOT_FOUND", "命名服务返回未注册方案")
                target_domain = self.processes.get(target.domain_id)
                if reply["domain_generation"] != target_domain.generation:
                    raise KernelFault("STALE_REFERENCE", "绑定代次不匹配")
                scheme, provider_object, path = target, "root", reply["path"]
                continue
            if reply["result"] != "opened":
                raise KernelFault("PROTOCOL_ERROR", "无效打开答复")
            try:
                grant = self.references.grant(session, scheme, reply)
                actions = (
                    self.references.strings(reply["actions"]) & grant["actions"] & scheme.actions
                )
                requested = meta.get("actions")
                if requested is not None:
                    requested = self.references.strings(requested)
                    if not requested <= actions:
                        raise KernelFault("ACCESS_DENIED", "请求动作超出许可")
                    actions = requested
                if not session.active or domain.state != "ready" or domain.reaped:
                    raise KernelFault("STALE_REFERENCE", "安装前目标或会话失效")
                handle = self._install(
                    session,
                    scheme.scheme_id,
                    reply["provider_object_id"],
                    actions,
                    parent=parent,
                    grant=grant,
                )
            except KernelFault, KeyError, TypeError, ValueError:
                if domain.state == "ready" and not domain.reaped:
                    await self._rpc(
                        domain,
                        {
                            "op": "close",
                            "scheme_id": scheme.scheme_id,
                            "provider_object_id": reply["provider_object_id"],
                        },
                    )
                raise
            return {
                "handle_id": handle,
                "scheme_id": scheme.scheme_id,
                "domain_generation": domain.generation,
                "contract": scheme.contract,
                "basis": list(grant["basis"]),
                "expiry": session.handles[handle].expiry,
            }
        raise KernelFault("PROTOCOL_ERROR", "命名转交次数超限")

    async def _release(self, obj):
        if obj.refs or obj.pins or self.objects.pop(obj.object_id, None) is None:
            return
        scheme = self.schemes[obj.scheme_id]
        if not obj.root and scheme.domain_id is not None:
            domain = self.processes.get(scheme.domain_id)
            if domain.state in ("ready", "draining") and not domain.reaped:
                try:
                    await self._rpc(
                        domain,
                        {
                            "op": "close",
                            "scheme_id": scheme.scheme_id,
                            "provider_object_id": obj.provider_object_id,
                        },
                    )
                except KernelFault:
                    pass

    async def _close(self, session, reference):
        handle = session.handles.pop(reference, None)
        if handle is not None:
            self.events.close(handle)
            self.streams.close(handle)
            if handle.live_ref:
                handle.live_ref = False
                obj = self.objects[handle.object_id]
                obj.refs -= 1
                await self._release(obj)
            self.changed.set()
            return {"closed": True}
        invocation = self.invocations.get(reference)
        if invocation is not None and invocation.owner is session and invocation.observed:
            invocation.observed = False
            if invocation.task.done():
                self.invocations.pop(reference, None)
            return {"closed": True}
        raise KernelFault("INVALID_HANDLE", "引用不属于当前会话或已经关闭")

    def stats(self):
        return {
            "kernel_pid": os.getpid(),
            "boot_id": self.boot_id,
            "domains_live": sum(not x.reaped for x in self.processes.domains.values()),
            "domains_reaped": sum(x.reaped for x in self.processes.domains.values()),
            "sessions_active": sum(x.active for x in self.sessions.values()),
            "handles": sum(len(x.handles) for x in self.sessions.values()),
            "objects": len(self.objects),
            "invocations": len(self.invocations),
            "inflight": sum(not x.task.done() for x in self.invocations.values()),
            "reference_records": len(self.references.nodes),
            "revocation_tombstones": len(self.references.bases) + len(self.references.domains),
            "revocation_revision": self.references.revision,
            "late_replies": self.calls.late_replies,
            "pending_install_events": sum(
                x.events.qsize() for x in self.sessions.values() if x.active
            ),
            "event_records": len(self.events.records),
            "pending_subscriptions": len(self.events.pending),
            "queued_events": sum(len(x.queue) for x in self.events.records.values()),
            "channels": len(self.streams.records),
            "pipe_fds": sum(
                (x.read_fd >= 0) + (x.write_fd >= 0) for x in self.streams.records.values()
            ),
            "io_waiters": sum(len(x.watchers) for x in self.streams.records.values()),
            "event_waiters": sum(x.waiting for x in self.events.records.values())
            + sum(x.waiting_events for x in self.sessions.values()),
            "event_record_waiters": sum(x.waiting for x in self.events.records.values()),
            "supervision_waiters": sum(x.waiting_supervision for x in self.sessions.values()),
            "pending_supervision_events": sum(
                x.supervision.qsize() for x in self.sessions.values() if x.active
            ),
            "policy_requests": len(self.policy_requests),
        }

    async def aclose(self):
        if self.shutdown.task is not None:
            await asyncio.shield(self.shutdown.task)
        self._closing = True
        self.events.aclose()
        self.streams.aclose()
        await self.processes.aclose()
        for endpoint, _ in self._connections:
            await endpoint.aclose()
        await asyncio.gather(*(task for _, task in self._connections))
        if self._shutdown_exit_task:
            await self._shutdown_exit_task
        await asyncio.gather(*(x.task for x in list(self.invocations.values())))
        while self._maintenance:
            await asyncio.gather(*list(self._maintenance))
        self.unhandled_policy.extend(self.policy_requests.values())
        self.policy_requests.clear()

    def report(self):
        return {
            "stats": self.stats(),
            "domains": [d.snapshot() for d in self.processes.domains.values()],
            "errors": list(self.errors),
            "policy_decisions": list(self.policy_decisions),
            "unhandled_policy": self.unhandled_policy,
            "shutdown": self.shutdown.report,
            "abi_calls": self.abi_calls,
        }
