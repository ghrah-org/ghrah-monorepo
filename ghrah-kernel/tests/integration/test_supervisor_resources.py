"""应用资源关闭失败时仍由 Kernel 回收进程，失败不得伪报为干净收尾。"""

import asyncio

import pytest
from _runtime_host import SCHEMES, open_runtime

from ghrah_kernel.integration.contracts import NAMESPACE_SCHEMES
from ghrah_kernel.supervisor import ServiceBinding, ServiceSpec, Supervisor


@pytest.mark.parametrize("operation", ["stop", "aclose", "shutdown"])
def test_application_close_failure_does_not_abandon_managed_processes(operation):
    async def scenario():
        async with open_runtime(hello=False, assemble=False) as host:
            supervisor = Supervisor(
                host.control,
                (
                    ServiceSpec("names", "namespace", entry_key="names"),
                    ServiceSpec(
                        "one",
                        "service-probe",
                        entry_key="one",
                        bindings=(ServiceBinding("/probe", "probe", "s1", "n1"),),
                        close_action=("probe", "fail"),
                    ),
                ),
                {"namespace": NAMESPACE_SCHEMES, "service-probe": SCHEMES},
            )
            await supervisor.start(monitor=False)
            assert supervisor.view("one")["phase"] == "Running"
            if operation == "shutdown":
                report = await supervisor.shutdown()
                assert not report["clean"]
                assert "one" in report["application_close_errors"][0]
                assert all(x["reaped"] for x in report["domains"])
                await asyncio.wait_for(host.process.wait(), 5)
            else:
                with pytest.raises(RuntimeError, match="资源关闭失败"):
                    if operation == "stop":
                        await supervisor.stop_service("one")
                    else:
                        await supervisor.aclose()
                failed = supervisor.view("one")
                assert failed["phase"] == "Failed" and failed["kernel"]["reaped"]
                if operation == "stop":
                    await supervisor.aclose()
                assert all(state.kernel["reaped"] for state in supervisor.states.values())

    asyncio.run(scenario())
