"""机制回归共享的替身契约与固定启动清单，不包含业务实现。"""

from ghrah_kernel.context import CallerBinding
from ghrah_kernel.launch import LaunchCatalog, LaunchRecord
from ghrah_kernel.supervisor import ServiceSpec

SERVICE_SCHEMES = {
    "namespace": [{"name": "ns", "contract": "ns.mock/e0", "actions": ["bind"]}],
    "ability": [
        {
            "name": "ability",
            "contract": "ability.mock/e0",
            "actions": [
                "add",
                "echo",
                "identity",
                "delay",
                "counter",
                "probe",
                "release",
                "events",
                "emit",
                "stream",
                "fault",
            ],
        },
        {"name": "view", "contract": "view.mock/e0", "actions": ["identity"]},
    ],
    "agent": [
        {
            "name": "agent",
            "contract": "agent.mock/e0",
            "actions": ["run", "status", "attempt_register"],
        }
    ],
}

DEFAULT_SERVICES = (
    ServiceSpec("names", "namespace"),
    ServiceSpec(
        "tools",
        "ability",
        "on_failure",
        (("/scheme/ability/main", "ability"), ("/scheme/view/main", "view")),
    ),
    ServiceSpec("worker", "agent", "always", (("/scheme/agent/demo", "agent"),)),
)


def mechanism_catalog():
    """显式允许机制夹具入口，产品 Kernel 不再硬编码测试模块。"""
    return LaunchCatalog(
        tuple(
            LaunchRecord(
                kind,
                kind,
                "_mechanism.service_main",
                CallerBinding(f"fixture:{kind}", "root", True),
                arguments=("--kind", kind, "--fixture", variant),
                variant=variant,
            )
            for kind in SERVICE_SCHEMES
            for variant in (
                ("normal", "exit", "spin", "ignore_term") if kind == "ability" else ("normal",)
            )
        )
    )
