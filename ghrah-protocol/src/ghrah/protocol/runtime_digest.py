"""完整装配输入的版本化摘要；不包含 Agent 身份或运行状态。"""

import hashlib
import json
import math
from decimal import Decimal
from typing import Any

from ghrah.protocol.runtime_agents import RuntimeAssemblyInput


def canonical_json(value: Any) -> str:
    """编码有限 JSON 子集，键按码点排序，整数限制在跨语言精确范围。"""
    if value is None or isinstance(value, (str, bool)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        if abs(value) > 2**53 - 1:
            raise ValueError("Integer exceeds the interoperable JSON range")
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite JSON number")
        if value == 0:
            return "0"
        text = repr(value).lower()
        if 1e-6 <= abs(value) < 1e21:
            return (
                format(Decimal(text), "f").rstrip("0").rstrip(".")
                if "." in format(Decimal(text), "f")
                else format(Decimal(text), "f")
            )
        mantissa, exponent = text.split("e")
        exponent_value = int(exponent)
        return (
            mantissa.removesuffix(".0")
            + "e"
            + (f"+{exponent_value}" if exponent_value >= 0 else str(exponent_value))
        )
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return (
            "{"
            + ",".join(
                canonical_json(key) + ":" + canonical_json(value[key]) for key in sorted(value)
            )
            + "}"
        )
    raise ValueError("Value is outside the JSON subset")


def runtime_digest(value: Any) -> str:
    """对规范化 JSON 计算带算法前缀的摘要。"""
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalized_assembly(config: RuntimeAssemblyInput) -> dict[str, Any]:
    """集合去重排序；权限子命令和脚本顺序保持不变。"""
    value: dict[str, Any] = config.model_dump(mode="json")
    permission = value["permission_request"]
    for key in ("auto_approve", "safe_extra_commands"):
        permission[key] = sorted(set(permission[key]))
    ability = value["ability_request"]
    ability["mcp_refs"] = sorted(set(ability["mcp_refs"]))
    ability["surface"]["static"] = sorted(set(ability["surface"]["static"]))
    packages = {}
    for package in ability["packages"]:
        for key in ("operations", "skills"):
            members = {canonical_json(item): item for item in package[key]}
            package[key] = [members[name] for name in sorted(members)]
        packages[canonical_json(package)] = package
    ability["packages"] = [packages[name] for name in sorted(packages)]
    return value


def assembly_digest(config: RuntimeAssemblyInput) -> str:
    """摘要版本和完整五组配置共同参与计算，external_ref 不参与。"""
    return runtime_digest({"digest_version": 1, "config": normalized_assembly(config)})
