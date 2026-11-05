"""许可期限由部署显式选择，长持句柄与有限 TTL 分别验收。"""

import asyncio
import math
import time
from dataclasses import replace

import pytest
from _runtime_host import U1, identities, open_runtime

from ghrah_kernel.client import RemoteError
from ghrah_kernel.context import ProviderContext
from ghrah_kernel.integration.grants import GrantLifetime
from ghrah_kernel.integration.identity import StaticIdentityConfig
from ghrah_kernel.integration.namespace_provider import NamespaceProvider
from ghrah_kernel.kernel.runtime import Kernel
from ghrah_kernel.kernel.tables import KernelFault, Scheme


def _grants(directory):
    context = ProviderContext(U1.ref, "session", None, "s1", "namespace", "s1")
    return (
        directory.grant(U1.ref, "n1", "resource"),
        NamespaceProvider(directory, U1.ref).open(context, "/")["grant"],
    )


def _install(kernel, session, raw, parent=None):
    scheme = Scheme(
        kernel.new_id("scheme"), None, "lifetime-test", "test/e0", frozenset(raw["allowed_actions"])
    )
    kernel.schemes[scheme.scheme_id] = scheme
    grant = kernel.references.grant(session, scheme, {"grant": raw})
    return kernel._install(
        session, scheme.scheme_id, "resource", scheme.actions, parent=parent, grant=grant
    )


def test_instance_grants_survive_300_seconds_but_are_revocable_and_closable(monkeypatch):
    async def scenario():
        kernel = Kernel()
        session = kernel._new_session("observer", None, U1.ref, False)
        try:
            handles = [_install(kernel, session, grant) for grant in _grants(identities())]
            loop = asyncio.get_running_loop()
            now = loop.time()
            # 只同步推进实际句柄门禁的时钟，不等待 5 分钟，也不触碰异步任务的定时器。
            for elapsed in (301, 86401):
                with monkeypatch.context() as clock:
                    clock.setattr(loop, "time", lambda: now + elapsed)
                    for handle in handles:
                        assert kernel._target(session, handle)[0].expiry == math.inf
            for handle in handles:
                await kernel.references.revoke(session, {"type": "handle", "handle": handle})
                with pytest.raises(KernelFault) as revoked:
                    kernel._target(session, handle)
                assert revoked.value.code == "REVOKED"
                await kernel._close(session, handle)
                with pytest.raises(KernelFault) as closed:
                    kernel._target(session, handle)
                assert closed.value.code == "INVALID_HANDLE"
        finally:
            for handle in list(session.handles):
                await kernel._close(session, handle)
            await kernel.aclose()

    asyncio.run(scenario())


def test_explicit_ttl_and_finite_parent_still_bound_instance_grants(monkeypatch):
    async def scenario():
        kernel = Kernel()
        session = kernel._new_session("observer", None, U1.ref, False)
        try:
            now = asyncio.get_running_loop().time()
            ttl = replace(identities(), grant_lifetime=GrantLifetime("ttl", 30))
            for raw, instance_raw in zip(_grants(ttl), _grants(identities()), strict=True):
                parent_id = _install(kernel, session, raw)
                parent = session.handles[parent_id]
                assert now + 30 <= parent.expiry < now + 31
                child_id = _install(kernel, session, instance_raw, parent=parent)
                assert session.handles[child_id].expiry == parent.expiry
                with pytest.raises(KernelFault) as extended:
                    kernel.references.dup(session, {"handle": parent_id, "expiry": math.inf})
                assert extended.value.code == "ACCESS_DENIED"
                with monkeypatch.context() as clock:
                    clock.setattr(asyncio.get_running_loop(), "time", lambda: now + 301)
                    for handle in (parent_id, child_id):
                        with pytest.raises(KernelFault) as expired:
                            kernel._target(session, handle)
                        assert expired.value.code == "EXPIRED"
        finally:
            for handle in list(session.handles):
                await kernel._close(session, handle)
            await kernel.aclose()

    asyncio.run(scenario())


def test_real_providers_report_explicit_instance_lifetime():
    async def scenario():
        async with open_runtime() as host:
            for path, action in (("/", "inspect"), ("/service", "context")):
                opened = await host.observer._request(
                    "open", base=host.observer.session["ns_root_handle"], path=path
                )
                assert opened.meta["expiry"] == math.inf
                handle = opened.meta["handle_id"]
                assert await host.observer.call(handle, action, {"ttl_seconds": 300})
                await host.observer.revoke({"type": "handle", "handle": handle})
                with pytest.raises(RemoteError) as revoked:
                    await host.observer.call(handle, action, {})
                assert revoked.value.code == "REVOKED"
            parent_expiry = time.monotonic() + 60
            parent = await host.observer.dup(
                host.observer.session["ns_root_handle"], expiry=parent_expiry
            )
            opened = await host.observer._request("open", base=parent["handle_id"], path="/service")
            assert opened.meta["expiry"] == parent_expiry

    asyncio.run(scenario())


def test_real_providers_enforce_configured_ttl_without_renewing_old_handles():
    async def scenario():
        async with open_runtime(lifetime=GrantLifetime("ttl", 0.5)) as host:
            issued_at = time.monotonic()
            handles = []
            for path, action in (("/", "inspect"), ("/service", "context")):
                opened = await host.observer._request(
                    "open", base=host.observer.session["ns_root_handle"], path=path
                )
                assert issued_at + 0.5 <= opened.meta["expiry"] < issued_at + 2
                handles.append((opened.meta["handle_id"], action, opened.meta["expiry"]))
            await asyncio.sleep(max(expiry for _, _, expiry in handles) - time.monotonic() + 0.02)
            fresh = await host.observer.open("/service")
            assert await host.observer.call(fresh, "context", {})
            for handle, action, _ in handles:
                with pytest.raises(RemoteError) as expired:
                    await host.observer.call(handle, action, {})
                assert expired.value.code == "EXPIRED"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mode,seconds",
    [
        ("ttl", None),
        ("ttl", 0),
        ("ttl", -1),
        ("ttl", True),
        ("ttl", math.inf),
        ("ttl", math.nan),
        ("instance", 300),
        ("unknown", None),
    ],
)
def test_deployment_rejects_missing_invalid_or_ambiguous_lifetime(mode, seconds):
    with pytest.raises(ValueError):
        GrantLifetime(mode, seconds)


@pytest.mark.parametrize("lifetime", [GrantLifetime("instance"), GrantLifetime("ttl", 30)])
def test_lifetime_roundtrips_and_legacy_config_cannot_choose_a_hidden_default(lifetime):
    directory = replace(identities(), grant_lifetime=lifetime)
    data = directory.to_dict()
    assert StaticIdentityConfig.from_dict(data) == directory
    del data["grant_lifetime"]
    with pytest.raises(ValueError):
        StaticIdentityConfig.from_dict(data)


@pytest.mark.parametrize("expiry", [math.nan, -math.inf, True])
def test_no_time_limit_does_not_accept_other_invalid_wire_expiries(expiry):
    async def scenario():
        kernel = Kernel()
        session = kernel._new_session("observer", None, U1.ref, False)
        try:
            raw = {**identities().grant(U1.ref, "n1", "resource"), "expiry": expiry}
            with pytest.raises(KernelFault) as failed:
                _install(kernel, session, raw)
            assert failed.value.code == "PROTOCOL_ERROR"
        finally:
            for handle in list(session.handles):
                await kernel._close(session, handle)
            await kernel.aclose()

    asyncio.run(scenario())
