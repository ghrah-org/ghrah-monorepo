# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0

"""从消息受理结果投影协议回执。"""

from ghrah.protocol.views.core import DeliveryAcceptanceView, MessageAcceptanceView

from ghrah.communication.storage import MessageRecord


def message_acceptance_view(record: MessageRecord) -> MessageAcceptanceView:
    """从受理事务返回的投递快照生成跨端口回执。"""
    return MessageAcceptanceView(
        message_id=record.message_id,
        accepted_at=record.accepted_at,
        deliveries=[
            DeliveryAcceptanceView.model_validate(
                {
                    "delivery_id": delivery.delivery_id,
                    "policy": delivery.delivery_policy,
                    "policy_applied": delivery.policy_applied,
                    "turn_id": delivery.turn_id,
                    "display_state": delivery.display_state,
                    "queue_position": delivery.queue_position,
                }
            )
            for delivery in record.deliveries
        ],
    )
