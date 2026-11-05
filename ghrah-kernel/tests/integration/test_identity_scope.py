"""身份与局部解析在真实 Kernel 和 Provider 进程之间保持隔离。"""

import asyncio
from dataclasses import replace

import pytest
from _runtime_host import A1, SVC, U1, U2, identities, open_runtime

from ghrah_kernel.client import RemoteError
from ghrah_kernel.integration.identity import Group, StaticIdentityConfig, Subject


def test_two_users_and_same_subject_sessions_have_separate_handles_and_scopes():
    async def scenario():
        async with open_runtime() as host:
            user = host.observer
            assert user.session["principal"] == host.control.session["principal"] == U1.ref
            assert host.peer.session["principal"] == U2.ref
            assert user.session["session_id"] != host.control.session["session_id"]
            one = await user.open("/service")
            two = await host.peer.open("/service")
            admin_root = host.registrations["names"]["scheme_handles"]["ns"]
            cross = await host.control.open("/service", base=admin_root)
            first = await user.call(one, "context", {})
            second = await host.peer.call(two, "context", {})
            third = await host.control.call(cross, "context", {})
            assert first["label"] == "one" and second["label"] == third["label"] == "two"
            assert first["pid"] != second["pid"]
            assert first["parent_pid"] == second["parent_pid"] == host.process.pid
            assert first["principal"] == third["principal"] == U1.ref
            assert first["resolution_scope"] == "s1" and third["resolution_scope"] == "s2"
            with pytest.raises(RemoteError) as foreign:
                await host.control.call(one, "context", {})
            assert foreign.value.code == "INVALID_HANDLE"

    asyncio.run(scenario())


def test_client_hello_and_payload_cannot_choose_subject_group_or_scope():
    async def scenario():
        async with open_runtime(hello=False) as host:
            reply = await host.peer._request(
                "hello",
                credential=host.tokens[2],
                principal=U1.ref,
                groups=["g1"],
                namespace="n1",
                resolution_scope="s1",
                diagnostics=True,
            )
            host.peer.session = reply.meta
            assert reply.meta["principal"] == U2.ref and reply.meta["resolution_scope"] == "s2"
            with pytest.raises(RemoteError) as denied:
                await host.peer.open("/agent")
            assert denied.value.code == "ACCESS_DENIED"
            handle = await host.peer.open("/service")
            fake = {
                "principal": U1.ref,
                "groups": ["g1"],
                "namespace": "n1",
                "context": {"principal": U1.ref},
            }
            result = await host.peer.call(handle, "context", fake)
            assert result["principal"] == U2.ref and result["resolution_scope"] == "s2"
            assert result["payload"] == fake
            with pytest.raises(RemoteError) as stats:
                await host.peer.stats()
            assert stats.value.code == "ACCESS_DENIED"
            info = await host.peer.call(reply.meta["sys_handle"], "info", {})
            assert "domains_live" not in info and len(info["implemented_abi"]) == 14

    asyncio.run(scenario())


def test_resolution_mounts_and_group_membership_do_not_grant_service_access():
    async def scenario():
        async with open_runtime() as host:
            with pytest.raises(RemoteError) as denied:
                await host.observer.open("/restricted")
            assert denied.value.code == "ACCESS_DENIED"
            root = await host.peer.open("/")
            before = await host.peer.call(root, "inspect", {"scope": "s1"})
            assert before["scope"] == "s2" and before["principal"] == U2.ref
            with pytest.raises(RemoteError) as bind:
                await host.peer.call(root, "bind", {})
            assert bind.value.code == "ACCESS_DENIED"
        directory = identities()
        with pytest.raises(ValueError, match="显式许可"):
            directory.grant(SVC.ref, "n1", "resource")
        with pytest.raises(ValueError, match="显式许可"):
            directory.grant(U1.ref, "n2", "restricted")
        with pytest.raises(ValueError, match="显式许可"):
            replace(directory, access=()).grant(U1.ref, "n1", "resource")
        with pytest.raises(ValueError, match="Scope"):
            directory.bind(U2.ref, "s1")

    asyncio.run(scenario())


def test_rebinding_or_unmounting_names_preserves_exact_old_targets():
    async def scenario():
        async with open_runtime() as host:
            user = host.observer
            old = await user.open("/service")
            await host.mount("s1", "/service", "two", "n2")
            new = await user.open("/service")
            assert (await user.call(old, "context", {}))["label"] == "one"
            assert (await user.call(new, "context", {}))["label"] == "two"
            await host.control.call(
                host.registrations["names"]["scheme_handles"]["ns"],
                "unbind",
                {"scope": "s1", "path": "/service"},
            )
            with pytest.raises(RemoteError) as missing:
                await user.open("/service")
            assert missing.value.code == "NOT_FOUND"
            assert (await user.call(old, "context", {}))["label"] == "one"

    asyncio.run(scenario())


def test_user_calls_agent_service_but_downstream_uses_its_own_subject():
    async def scenario():
        async with open_runtime() as host:
            handle = await host.observer.open("/agent")
            result = await host.observer.call(handle, "outbound", {"principal": U1.ref})
            assert result["principal"] == U1.ref
            assert result["service_principal"] == A1.ref
            downstream = result["downstream"]
            assert downstream["principal"] == A1.ref
            assert downstream["caller_domain_id"] == host.registrations["agent"]["domain_id"]
            assert downstream["label"] == "one"
            assert downstream["pid"] != result["pid"]
            with pytest.raises(RemoteError) as denied:
                await host.peer.open("/agent")
            assert denied.value.code == "ACCESS_DENIED"

    asyncio.run(scenario())


def test_static_configuration_is_frozen_and_rejects_unknown_or_ambiguous_identity():
    directory = identities()
    assert StaticIdentityConfig.from_dict(directory.to_dict()) == directory
    assert Subject("other-issuer", "u1", "user").ref != U1.ref
    with pytest.raises(ValueError, match="未知"):
        replace(directory, groups=(Group("g1", ("unknown",)),))
    with pytest.raises(ValueError, match="重复"):
        replace(directory, subjects=directory.subjects + (U1,))
    with pytest.raises(ValueError):
        replace(directory, access=(replace(directory.access[0], subject=U2.ref),))
    assert not hasattr(directory, "add_member") and not hasattr(directory, "remove_member")
