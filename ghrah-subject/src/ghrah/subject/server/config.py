# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Subject Observer Server 配置模块。"""

from __future__ import annotations

import os


class ObserverServerConfig:
    """Observer WebSocket 服务器配置。

    Attributes:
        host: 监听地址
        port: 监听端口（默认 4112，区别于 Core 的 4111）
        ws_path: WebSocket 端点路径
        ping_interval: 心跳检测间隔（秒）
        ping_timeout: 心跳超时时间（秒）
        event_replay_capacity: EventStore 事件缓冲区容量
        log_level: 日志级别
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 4112,
        ws_path: str = "/ws",
        ping_interval: float = 30.0,
        ping_timeout: float = 10.0,
        event_replay_capacity: int = 1000,
        log_level: str = "INFO",
    ) -> None:
        self.host = host
        self.port = port
        self.ws_path = ws_path
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.event_replay_capacity = event_replay_capacity
        self.log_level = log_level

    @classmethod
    def from_env(cls) -> ObserverServerConfig:
        """从环境变量创建配置。

        环境变量命名规则：GHRAH_SUBJECT_SERVER_<大写属性名>
        例如：GHRAH_SUBJECT_SERVER_PORT
        """
        return cls(
            host=os.environ.get("GHRAH_SUBJECT_SERVER_HOST", "0.0.0.0"),
            port=_env_int("PORT", 4112),
            ws_path=os.environ.get("GHRAH_SUBJECT_SERVER_WS_PATH", "/ws"),
            ping_interval=_env_float("PING_INTERVAL", 30.0),
            ping_timeout=_env_float("PING_TIMEOUT", 10.0),
            event_replay_capacity=_env_int("EVENT_REPLAY_CAPACITY", 1000),
            log_level=os.environ.get("GHRAH_SUBJECT_SERVER_LOG_LEVEL", "INFO"),
        )


def _env_int(key: str, default: int) -> int:
    val = os.environ.get(f"GHRAH_SUBJECT_SERVER_{key}")
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        raise ValueError(
            f"Environment variable GHRAH_SUBJECT_SERVER_{key} must be an integer, got '{val}'"
        ) from None


def _env_float(key: str, default: float) -> float:
    val = os.environ.get(f"GHRAH_SUBJECT_SERVER_{key}")
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        raise ValueError(
            f"Environment variable GHRAH_SUBJECT_SERVER_{key} must be a number, got '{val}'"
        ) from None
