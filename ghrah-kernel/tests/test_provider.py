"""Provider SDK 的注册、防错、并发、取消与显式效果回执。"""

import asyncio
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from ghrah_kernel.context import ProviderContext
from ghrah_kernel.payloads import decode_payload, encode_payload
from ghrah_kernel.protocol import Envelope, decode_envelope, encode_envelope
from ghrah_kernel.provider import Provider, ProviderFault, ProviderResult

SCHEMES = [{"name": "test", "contract": "test/v0", "actions": ["run"]}]


class Endpoint:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.wire = SimpleNamespace(max_bytes=4096)
        self.closed = False

    async def send(self, value):
        assert len(value) <= self.wire.max_bytes
        await self.outgoing.put(decode_envelope(value))

    async def receive(self):
        value = await self.incoming.get()
        if value is None:
            raise EOFError
        return encode_envelope(value)

    async def aclose(self):
        self.closed = True


def provider(handler):
    endpoint = Endpoint()
    return Provider(endpoint, "secret", SCHEMES, open_handler=lambda *_: {}, invoke_handler=handler)


async def request(service, **meta):
    await service._answer(Envelope("request", 1, meta))
    return await service.endpoint.outgoing.get()


async def bind(service):
    assert (await request(service, op="bind_schemes", schemes={"test": "sid"})).error is None


def invocation(index=1):
    invocation_id = f"i{index}"
    context = ProviderContext(
        "subject", "session", "domain", "scope", "sid", "object", invocation_id
    )
    return Envelope(
        "request",
        index,
        {
            "op": "invoke",
            "scheme_id": "sid",
            "provider_object_id": "object",
            "invocation_id": invocation_id,
            "action": "run",
            "context": asdict(context),
        },
        encode_payload({"principal": "untrusted"}),
    )


def test_describe_credentials_and_immutable_complete_scheme_binding():
    async def scenario():
        service = provider(lambda *_: ProviderResult(None, "NO_EFFECT"))
        assert (
            await request(service, op="describe", enrollment="wrong")
        ).error.code == "ACCESS_DENIED"
        assert (await request(service, op="describe", enrollment="secret")).meta[
            "schemes"
        ] == SCHEMES
        assert (
            await request(service, op="bind_schemes", schemes={})
        ).error.code == "CONTRACT_MISMATCH"
        await bind(service)
        await bind(service)
        assert (
            await request(service, op="bind_schemes", schemes={"test": "other"})
        ).error.code == "CONTRACT_MISMATCH"
        assert service.bound == {"sid": SCHEMES[0]}

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mode,code,effect",
    [
        ("plain", "INVALID_ARGUMENT", "UNKNOWN"),
        ("failure", "PROVIDER_ERROR", "UNKNOWN"),
        ("fault", "ACCESS_DENIED", "NO_EFFECT"),
        ("success", None, "EFFECT_OCCURRED"),
    ],
)
def test_handler_must_report_effect_and_failure_preserves_uncertainty(mode, code, effect):
    async def scenario():
        def handler(context, action, arguments):
            assert context.principal == "subject" and arguments["principal"] == "untrusted"
            if mode == "failure":
                raise RuntimeError("unknown effect")
            if mode == "fault":
                raise ProviderFault("ACCESS_DENIED", "denied", effect="NO_EFFECT")
            return {} if mode == "plain" else ProviderResult({"ok": True}, "EFFECT_OCCURRED")

        service = provider(handler)
        await bind(service)
        await service._answer(invocation())
        result = await service.endpoint.outgoing.get()
        assert (result.error.code if result.error else None) == code
        assert result.meta["reported_effect"] == effect

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["scheme_id", "provider_object_id", "invocation_id"])
def test_mismatching_trusted_context_never_enters_handler(field):
    async def scenario():
        def handler(*_):
            pytest.fail("不应调用 handler")

        service = provider(handler)
        await bind(service)
        call = invocation()
        call.meta["context"][field] = "forged"
        await service._answer(call)
        assert (await service.endpoint.outgoing.get()).error.code == "INVALID_ARGUMENT"

    asyncio.run(scenario())


def test_oversize_effect_receipt_preserves_endpoint_for_next_call():
    async def scenario():
        service = provider(lambda *_: ProviderResult("x" * 8192, "EFFECT_OCCURRED"))
        await bind(service)
        await service._answer(invocation())
        result = await service.endpoint.outgoing.get()
        assert result.error.code == "MESSAGE_TOO_LARGE"
        assert result.meta["reported_effect"] == "EFFECT_OCCURRED"
        assert not service.endpoint.closed
        service.invoke_handler = lambda *_: ProviderResult("next", "NO_EFFECT")
        await service._answer(invocation(2))
        result = await service.endpoint.outgoing.get()
        assert decode_payload(result.opaque_payload) == "next" and result.error is None

    asyncio.run(scenario())


def test_busy_limit_still_accepts_explicit_cancel_and_eof_cleans_tasks():
    async def scenario():
        gate = asyncio.Event()

        async def handler(*_):
            await gate.wait()
            return ProviderResult(None, "NO_EFFECT")

        service = provider(handler)
        await bind(service)
        serving = asyncio.create_task(service.serve())
        try:
            for index in range(1, 65):
                await service.endpoint.incoming.put(invocation(index))
            async with asyncio.timeout(2):
                while len(service.pending) != 64:
                    await asyncio.sleep(0.001)
            await service.endpoint.incoming.put(invocation(65))
            assert (await service.endpoint.outgoing.get()).error.code == "BUSY"
            await service.endpoint.incoming.put(
                Envelope("request", 66, {"op": "cancel", "scheme_id": "sid", "invocation_id": "i1"})
            )
            responses = [await service.endpoint.outgoing.get() for _ in range(2)]
            cancelled = next(x for x in responses if x.req_id == 1)
            assert cancelled.error.code == "CANCELLED"
            assert cancelled.meta["reported_effect"] == "UNKNOWN"
            assert next(x for x in responses if x.req_id == 66).meta["cancel_requested"]
            gate.set()
            results = [await service.endpoint.outgoing.get() for _ in range(63)]
            assert all(x.error is None for x in results)
        finally:
            await service.endpoint.incoming.put(None)
            await asyncio.wait_for(serving, 2)
        assert not service.pending and service.endpoint.closed

    asyncio.run(scenario())
