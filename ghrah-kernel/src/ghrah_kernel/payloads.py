"""SDK 业务编解码；路由核心不应依赖此模块。"""

import json
from dataclasses import asdict, is_dataclass
from typing import Any


class PayloadUnavailable(ValueError):  # noqa: N818
    """结果没有可交付的业务载荷。"""


def encode_payload(value: Any) -> bytes:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")


def decode_payload(value: bytes | None) -> Any:
    if value is None:
        raise PayloadUnavailable("业务载荷不可用，未尝试解码")
    return json.loads(value)
