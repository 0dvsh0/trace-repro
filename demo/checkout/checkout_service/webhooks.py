"""Payment provider webhook handling.

``apply_webhook`` is the upstream mutator trace-repro is going to find and
repair. It contains the canonical lost-update bug: it reads the order, does a
slow network call to the payment gateway, and only then writes the result back
-- with no row lock and no transaction.
"""

from __future__ import annotations

from dataclasses import dataclass

from .db import Database, sleep_ms
from .observ import traced
from .state_machine import CANCELLED, apply_event

# How long the upstream payment gateway takes to answer. This is the window in
# which a concurrent writer can observe a stale order and clobber it.
#
# Paired with REHYDRATE_MS in expiry.py and the offsets in incident.json. For
# the expiry worker's stale write to be the one that survives, with the webhook
# at offset 0 taking G ms end to end and the worker starting at x for R ms:
#
#     x          < G            (worker reads before the webhook commits)
#     x + R      > G            (worker commits after the webhook commits)
#
# The slack on each side is G - x and x + R - G, and the best case is
# x = G - R/2, which caps the slack at R/2. So a robust interleaving needs a
# *slow* rehydrate: R=140 with G=260, x=190 leaves 70 ms of slack on each side,
# which survives a loaded CI scheduler, not just a quiet laptop.
GATEWAY_LATENCY_MS = 260.0


@dataclass(frozen=True)
class PaymentEvent:
    id: str
    order_id: int
    type: str = "payment.authorized"


@traced()
def apply_webhook(db: Database, event: PaymentEvent) -> dict:
    """Apply a payment provider webhook to an order.

    The provider retries, so this can be called twice with the same event id.
    """
    with db.session() as session:
        order = session.query("orders").filter_by(id=event.order_id).one()
        if order.status == CANCELLED:
            # Cancelled orders are not resurrected by a late authorization.
            return order.as_dict()

        authorization = payment_gateway(order.amount_paise, event.id)

        order.status = apply_event(order.status, event.type, authorization)
        order.last_event_id = event.id
        order.webhook_applications += 1      # audit column; doubles on a retry
        session.commit()
        return order.as_dict()


def payment_gateway(amount_paise: int, event_id: str) -> str:
    """Stand-in for a real HTTP call to the payment provider."""
    sleep_ms(GATEWAY_LATENCY_MS)
    return "authorized" if amount_paise > 0 else "declined"


__all__ = [
    "GATEWAY_LATENCY_MS",
    "PaymentEvent",
    "apply_webhook",
    "payment_gateway",
]
