"""Checkout: the victim.

``capture_payment`` is where the production 500 is raised. Nothing here is
wrong -- it faithfully refuses to capture against a cancelled order. The stack
trace points at this line and this line is not the bug.
"""

from __future__ import annotations

from .db import Database
from .observ import traced
from .state_machine import AUTHORIZED, CAPTURED


class InvalidStateError(RuntimeError):
    """Raised when a capture is attempted against an order in the wrong state."""


@traced()
def capture_payment(db: Database, order_id: int, amount_paise: int = 450_000) -> dict:
    """Capture an authorised payment. 500 InvalidStateError if not authorised."""
    with db.session() as session:
        order = session.query("orders").filter_by(id=order_id).one()

        if order.status != AUTHORIZED:
            raise InvalidStateError(
                f"Order #{order_id} is {order.status}, cannot capture "
                f"INR {amount_paise / 100:,.0f}"
            )

        order.status = CAPTURED
        credit_wallet(session, order_id)
        session.commit()
        return order.as_dict()


def credit_wallet(session, order_id: int) -> None:
    """Move the captured amount into the customer wallet."""
    try:
        ledger = session.query("wallet_credits").filter_by(order_id=order_id).one()
    except LookupError:
        session.conn.execute(
            "INSERT INTO wallet_credits (order_id, credits) VALUES (?, 0)", (order_id,)
        )
        ledger = session.query("wallet_credits").filter_by(order_id=order_id).one()
    ledger.credits += 1


__all__ = ["InvalidStateError", "capture_payment", "credit_wallet"]
