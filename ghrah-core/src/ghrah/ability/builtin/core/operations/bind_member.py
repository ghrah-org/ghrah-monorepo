# SPDX-License-Identifier: Apache-2.0
"""把已获授成员绑定进模型上下文（工具面常驻）。"""

from ghrah.ability.surface.meta import BindInput, BindOutput, MetaToolAbility

PERMISSIONS = {}
TITLE = "Bind Member"
SUMMARY = (
    "Bind a granted, visible tool into the model context so its full schema stays "
    "available on every turn."
)
TAGS = ["core", "binding"]
EFFECTS = []
TIMEOUT_MS = 30_000
Input = BindInput
Output = BindOutput


class BindMemberAbility(MetaToolAbility):
    """绑定只作用于已授予且可见的成员；控制成员与不可用成员拒绝。"""

    tool_name = "bind_member"
    summary = SUMMARY
    input_model = Input
