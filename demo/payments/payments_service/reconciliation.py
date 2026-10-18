"""Nightly settlement reconciliation: the victim.

``reconcile_settlement`` is where the production 500 is raised. Nothing here is
wrong -- it faithfully reports that the ledger does not agree with the payment.
The stack trace points at this line and this line is not the bug.
"""

from __future__ import annotations

from .db import Database
from .observ import traced


class SettlementMismatch(RuntimeError):
    """Raised when the ledger holds a different number of captures than expected."""


@traced()
def reconcile_settlement(db: Database, payment_id: int) -> dict:
    """Check that exactly one capture reached the ledger for this payment."""
    with db.session() as session:
        payment = session.query("payments").filter_by(id=payment_id).one()

        entries = capture_entries(session, payment_id)
        if entries != 1:
            raise SettlementMismatch(
                f"payment {payment_id} is {payment.status} with "
                f"{payment.captured_paise} paise captured, but the ledger holds "
                f"{entries} capture entries"
            )
        return payment.as_dict()


def capture_entries(session, payment_id: int) -> int:
    cursor = session.conn.execute(
        "SELECT COUNT(*) AS n FROM ledger_entries WHERE payment_id = ? AND entry_type = 'CAPTURE'",
        (payment_id,),
    )
    return int(cursor.fetchone()["n"])


__all__ = ["SettlementMismatch", "capture_entries", "reconcile_settlement"]
