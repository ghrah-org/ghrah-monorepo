# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""能力处理器的汇总入口。

键必须与 :data:`ghrah.core.api.capabilities.CORE_CAPABILITIES` 完全一致，
否则 :class:`ghrah.core.api.runtime.CoreRuntime` 在构造时就会失败。
"""

from __future__ import annotations

from ghrah.protocol.capability import Handler

from ghrah.core.api.handlers.abilities import make_ability_handlers
from ghrah.core.api.handlers.agents import make_agent_handlers
from ghrah.core.api.handlers.branches import make_branch_handlers
from ghrah.core.api.handlers.deliveries import make_delivery_handlers
from ghrah.core.api.handlers.mailbox import make_mailbox_handlers
from ghrah.core.api.handlers.manifests import make_manifest_handlers
from ghrah.core.api.handlers.messages import make_message_handlers
from ghrah.core.api.handlers.nodes import make_node_handlers
from ghrah.core.api.handlers.session_write import make_session_write_handlers
from ghrah.core.api.handlers.sessions import make_session_handlers
from ghrah.core.read.ability import AbilityReadStore
from ghrah.core.read.chain import ChainReadStore
from ghrah.core.read.mailbox import MailboxReadStore
from ghrah.core.units.registry import RuntimeRegistry

__all__ = ["build_handlers"]


def build_handlers(
    store: ChainReadStore,
    mailbox: MailboxReadStore,
    abilities: AbilityReadStore,
    agents: RuntimeRegistry,
) -> dict[str, Handler]:
    """汇总全部能力处理器。

    Args:
        store: 链数据只读适配层
        mailbox: 消息表只读适配层
        abilities: 能力关系库只读适配层
        agents: 运行时注册表

    Returns:
        能力名到处理器的映射
    """
    return {
        **make_node_handlers(store),
        **make_session_handlers(store),
        **make_branch_handlers(store),
        **make_mailbox_handlers(mailbox, agents),
        **make_delivery_handlers(mailbox, agents),
        **make_ability_handlers(abilities),
        **make_agent_handlers(agents),
        **make_session_write_handlers(agents),
        **make_message_handlers(agents),
        **make_manifest_handlers(agents),
    }
