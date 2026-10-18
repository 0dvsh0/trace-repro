"""Reservations: the victim.

``reserve_stock`` is where the customer-facing failure is raised. Nothing here is
wrong -- it faithfully refuses to reserve units that are not sellable. The stack
trace points at this line and this line is not the bug.
"""

from __future__ import annotations

from .db import Database
from .observ import traced
from .stock import AVAILABLE, SOLD_OUT


class InsufficientStock(RuntimeError):
    """Raised when a reservation asks for units the lot cannot sell."""


@traced()
def reserve_stock(db: Database, stock_id: int, quantity: int) -> dict:
    """Reserve ``quantity`` units of a lot. 409 InsufficientStock if it cannot."""
    with db.session() as session:
        stock = session.query("stocks").filter_by(id=stock_id).one()

        if stock.status != AVAILABLE or stock.available < quantity:
            raise InsufficientStock(
                f"SKU {stock.sku} has {stock.available} sellable "
                f"({stock.status}), cannot reserve {quantity}"
            )

        stock.available -= quantity
        if stock.available == 0:
            stock.status = SOLD_OUT
        session.conn.execute(
            "INSERT INTO reservations (id, stock_id, quantity, created_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (stock_id * 1000 + quantity, stock_id, quantity),
        )
        session.commit()
        return stock.as_dict()


__all__ = ["InsufficientStock", "reserve_stock"]
