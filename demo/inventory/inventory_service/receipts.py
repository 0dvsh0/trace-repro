"""Warehouse goods-receipt webhook.

``receive_stock`` books a supplier delivery into a quarantined lot. The
supplier API call happens *between* the read and the write, so any concurrent
writer that read the lot before this call committed is working from a stale
view of what is sellable.
"""

from __future__ import annotations

from .db import Database, sleep_ms
from .observ import traced
from .stock import AVAILABLE, QUARANTINED

# How long the supplier's API takes to confirm the delivery. This is the window
# in which the expiry worker's stale view of the lot survives -- see the
# derivation in expiry.py.
SUPPLIER_API_MS = 260.0

# A confirmed delivery always buys another 90 days of shelf life.
SHELF_LIFE_DAYS = 90


@traced()
def receive_stock(db: Database, stock_id: int, event_id: str, quantity: int) -> dict:
    """Book a supplier delivery into a quarantined lot and release it for sale.

    The supplier retries, so this can be called twice with the same event id.
    """
    with db.session() as session:
        stock = session.query("stocks").filter_by(id=stock_id).one()

        if stock.status == QUARANTINED:
            release_date = supplier_api(stock.sku, event_id)
        else:
            release_date = None

        stock.available += quantity
        stock.status = AVAILABLE
        stock.expires_at = release_date or stock.expires_at
        stock.last_event_id = event_id        # audit column; doubles on a retry
        stock.receipt_applications += 1       # audit column; doubles on a retry
        session.commit()
        return stock.as_dict()


def supplier_api(sku: str, event_id: str) -> str | None:
    """Stand-in for a real HTTP call to the supplier WMS."""
    sleep_ms(SUPPLIER_API_MS)
    return f"{SHELF_LIFE_DAYS}d-from-{event_id}"


__all__ = ["SUPPLIER_API_MS", "receive_stock", "supplier_api"]
