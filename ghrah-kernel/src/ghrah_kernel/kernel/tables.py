"""本次 boot 的内存记录与不可复活的实例身份。"""

import asyncio
from dataclasses import dataclass, field

from ..client import RpcClient
from ..protocol import ErrorRecord


class KernelFault(Exception):  # noqa: N818
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass
class Domain:
    domain_id: str
    service_key: str
    generation: int
    kind: str
    process: asyncio.subprocess.Process
    peer: RpcClient
    state: str = "starting"
    exit_code: int | None = None
    reaped: bool = False
    expected_stop: bool = False
    schemes: dict[str, str] = field(default_factory=dict)
    registration: tuple | None = None
    monitor: asyncio.Task | None = None
    fixture: str = "normal"
    spawn_key: str = ""
    forced: bool = False
    entry_key: str = ""

    def snapshot(self) -> dict:
        return {
            "domain_id": self.domain_id,
            "service_key": self.service_key,
            "domain_generation": self.generation,
            "kind": self.kind,
            "pid": self.process.pid,
            "state": self.state,
            "exit_code": self.exit_code,
            "reaped": self.reaped,
            "schemes": dict(self.schemes),
            "expected_stop": self.expected_stop,
            "forced": self.forced,
            "fixture": self.fixture,
            "spawn_key": self.spawn_key,
            "entry_key": self.entry_key,
        }


@dataclass
class Scheme:
    scheme_id: str
    domain_id: str | None
    name: str
    contract: str
    actions: frozenset[str]


@dataclass
class ObjectRecord:
    object_id: str
    scheme_id: str
    provider_object_id: str
    refs: int = 0
    pins: int = 0
    root: bool = False
    invoke_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    kind: str = "resource"


@dataclass
class Handle:
    handle_id: str
    object_id: str
    actions: frozenset[str]
    owner_id: str = ""
    basis: tuple[str, str, int] | None = None
    parent: str | None = None
    expiry: float = float("inf")
    transfer_kind: str = "none"
    allowlist: frozenset[str] = frozenset()
    depth_left: int = 0
    can_open: bool = False
    revoked_at: int | None = None
    live_ref: bool = True
    kind: str = "resource"


@dataclass
class Session:
    session_id: str
    role: str
    domain_id: str | None
    handles: dict[str, Handle] = field(default_factory=dict)
    active: bool = True
    hello: dict = field(default_factory=dict)
    principal: str = ""
    accept_transfers: bool = False
    events: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=8))
    waiting_events: bool = False
    accept_channels: bool = False
    supervision: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=32))
    supervision_gap: bool = False
    waiting_supervision: bool = False
    resolution_scope: str | None = None


@dataclass
class Invocation:
    invocation_id: str
    owner: Session
    object_id: str
    action: str
    payload: bytes | None
    task: asyncio.Task | None = None
    state: str = "QUEUED"
    dispatched: bool = False
    effect: str = "NOT_DISPATCHED"
    result: bytes | None = None
    error: ErrorRecord | None = None
    observed: bool = True
    source: str = ""
    deadline: float | None = None
    terminal: asyncio.Event = field(default_factory=asyncio.Event)
    interrupted: asyncio.Event = field(default_factory=asyncio.Event)
    stop_reason: str | None = None
    policy_action_required: bool = False
