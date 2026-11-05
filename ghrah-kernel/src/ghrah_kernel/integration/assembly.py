"""宿主读取受信启动配置；与普通调用参数的入口严格分开。"""

from ..context import CallerBinding
from ..launch import LaunchCatalog, LaunchRecord


def host_configuration(data: dict) -> tuple[LaunchCatalog, dict[str, CallerBinding]]:
    """核定固定控制连接和启动记录，不接收请求级环境或模块覆盖。"""
    if set(data) != {"launches", "callers"} or set(data["callers"]) != {
        "control",
        "observer",
        "peer",
    }:
        raise ValueError("宿主配置必须显式声明启动清单和三个预建连接绑定")
    callers = {name: CallerBinding(**value) for name, value in data["callers"].items()}
    records = []
    for raw in data["launches"]:
        fields = dict(raw)
        fields["binding"] = CallerBinding(**fields["binding"])
        fields["arguments"] = tuple(fields.get("arguments", ()))
        fields["environment"] = tuple(tuple(pair) for pair in fields.get("environment", ()))
        records.append(LaunchRecord(**fields))
    return LaunchCatalog(tuple(records)), callers
