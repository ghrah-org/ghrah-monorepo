# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""最小健康门控：只有 Init 已完成自检的 Ready 阶段返回 200。"""

from fastapi.responses import JSONResponse

from .state import SystemPhase


def health_response(snapshot: dict) -> JSONResponse:
    """序列化 Init 与监督者的派生视图；探针不写阶段。"""
    return JSONResponse(
        snapshot, status_code=200 if snapshot["status"] == SystemPhase.READY else 503
    )
