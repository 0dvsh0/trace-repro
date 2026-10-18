"""Cart expiry worker.

Runs on a schedule, entirely outside any user request. Its trace never shares a
``trace_id`` with the checkout that later fails -- which is exactly why an APM
looking at the failing trace alone will never find it.
"""

from __future__ import annotations

from .db import Database, sleep_ms
from .observ import traced
from .state_machine import CANCELLED, may_expire

# Rehydrating the cart from the catalogue and firing the "cart removed" event.
# This has to be SLOW relative to the gateway call in webhooks.py -- see the
# note there for the derivation. 140 ms of catalogue and notification work is
# ordinary for a cart-expiry job, and it is what makes the interleaving
# reproducible instead of a coin flip.
REHYDRATE_MS = 140.0


@traced()
def expire_cart(db: Database, order_id: int) -> dict:
    """Cancel a cart that has been sitting unpaid for too long."""
    with db.session() as session:
        order = session.query("orders").filter_by(id=order_id).one()

        rehydrate_cart(order_id)          # slow work, happens BEFORE the write

        if not may_expire(order.status):
            return order.as_dict()
        order.status = CANCELLED
        session.commit()
        return order.as_dict()


def rehydrate_cart(order_id: int) -> None:
    sleep_ms(REHYDRATE_MS)


__all__ = ["REHYDRATE_MS", "expire_cart", "rehydrate_cart"]
