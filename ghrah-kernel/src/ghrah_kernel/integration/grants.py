# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""受信部署显式选择许可期限；服务入口与命名视图共用签发形状。"""

import math
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GrantLifetime:
    """instance 不另设计时期限；ttl 必须指定有限正秒数，不做自动续期。"""

    mode: str
    ttl_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.mode == "instance":
            if self.ttl_seconds is not None:
                raise ValueError("instance 许可不能同时指定 TTL")
        elif self.mode == "ttl":
            try:
                valid = (
                    type(self.ttl_seconds) in (int, float)
                    and math.isfinite(self.ttl_seconds)
                    and self.ttl_seconds > 0
                )
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError("ttl 许可必须显式指定有限正秒数")
        else:
            raise ValueError("许可期限必须显式选择 instance 或 ttl")

    def expiry(self) -> float:
        """生成单调时钟上限；无计时上限不取消实例、会话或撤权检查。"""
        if self.mode == "instance":
            return math.inf
        expiry = time.monotonic() + self.ttl_seconds
        if not math.isfinite(expiry):
            raise ValueError("TTL 截止时间溢出")
        return expiry


def issue_grant(
    principal: str, grant_id: str, actions: tuple[str, ...], lifetime: GrantLifetime
) -> dict:
    """按显式期限签发不可转交的入口许可；不读取业务请求中的期限。"""
    return {
        "principal": principal,
        "grant_id": grant_id,
        "revision": 1,
        "allowed_actions": list(actions),
        "expiry": lifetime.expiry(),
        "transfer_policy": {"kind": "none"},
        "depth_left": 0,
    }
