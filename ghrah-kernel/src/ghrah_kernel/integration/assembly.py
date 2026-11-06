"""宿主读取受信启动配置；与普通调用参数的入口严格分开。"""

import json

from ..context import CallerBinding
from ..launch import LaunchCatalog, LaunchRecord
from .identity import StaticIdentityConfig


def host_configuration(data: dict) -> tuple[LaunchCatalog, dict[str, CallerBinding]]:
    """核定固定控制连接和启动记录，不接收请求级环境或模块覆盖。"""
    if set(data) != {"identities", "launches", "callers"} or set(data["callers"]) != {
        "control",
        "observer",
        "peer",
    }:
        raise ValueError("宿主配置必须显式声明身份目录、启动清单和三个预建连接绑定")
    identities = StaticIdentityConfig.from_dict(data["identities"])

    def binding(value: dict) -> CallerBinding:
        declared = CallerBinding(**value)
        return identities.bind(
            declared.principal, declared.resolution_scope, diagnostics=declared.diagnostics
        )

    callers = {name: binding(value) for name, value in data["callers"].items()}
    records = []
    for raw in data["launches"]:
        fields = dict(raw)
        fields["binding"] = binding(fields["binding"])
        configuration = json.loads(fields.get("configuration", "{}"))
        if not isinstance(configuration, dict):
            raise ValueError("服务配置必须是对象")
        if "identities" in configuration:
            snapshot = StaticIdentityConfig.from_dict(configuration["identities"])
            if snapshot != identities:
                raise ValueError("服务身份快照与宿主身份目录不一致")
        configuration["identities"] = identities.to_dict()
        if fields["module"] == "ghrah_kernel.integration.entry":
            if configuration.get("administrator") != callers["control"].principal:
                raise ValueError("受管服务的管理主体必须与控制连接一致")
            if configuration.get("provider") == "agent":
                from .agent_provider import validate_configuration

                validate_configuration(configuration)
                if configuration["principal"] != fields["binding"].principal:
                    raise ValueError("Agent 主体必须与预建连接绑定一致")
            elif configuration.get("provider") != "namespace":
                raise ValueError("未知的受管服务实现")
        fields["configuration"] = json.dumps(configuration)
        fields["arguments"] = tuple(fields.get("arguments", ()))
        fields["environment"] = tuple(tuple(pair) for pair in fields.get("environment", ()))
        records.append(LaunchRecord(**fields))
    return LaunchCatalog(tuple(records)), callers
