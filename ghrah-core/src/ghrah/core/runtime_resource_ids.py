"""进程内资源接口共用的 ID 编码和公开错误。"""

import re
from typing import Any

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def wire_id(prefix: str, value: str) -> str:
    """将既有 UUID 可逆编码为资源 ID，不另建身份映射。"""
    number = int(value, 16)
    chars = []
    for _ in range(26):
        chars.append(_ALPHABET[number & 31])
        number >>= 5
    return prefix + "_" + "".join(reversed(chars))


def internal_id(prefix: str, value: str) -> str:
    """校验并还原资源 ID；拒绝超过 UUID 位数的别名编码。"""
    if not re.fullmatch(prefix + r"_[0-9A-HJKMNP-TV-Z]{26}", value):
        raise ResourceError(404, "NOT_FOUND", "Resource does not exist")
    number = 0
    for char in value.split("_", 1)[1]:
        number = number * 32 + _ALPHABET.index(char)
    if number >= 2**128:
        raise ResourceError(404, "NOT_FOUND", "Resource does not exist")
    return f"{number:032x}"


class ResourceError(Exception):
    """资源域可判定错误，由 Runtime 转为统一 Problem。"""

    def __init__(self, status: int, code: str | None, detail: str, **details: Any) -> None:
        """保留状态和可公开详情，不混入底层凭据或模型错误正文。"""
        super().__init__(detail)
        self.status, self.code, self.details = status, code, details
