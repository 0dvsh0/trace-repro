"""Payment provider webhook handling.

``handle_payment_event`` is the upstream mutator trace-repro is going to find
and repair. It contains the defect that costs real money: the provider promises
at-least-once delivery, the handler never checks whether it has already applied
this event id, and the acquirer call happens *between* the read and the write.

A retried delivery therefore walks the whole path again -- authorize, capture,
append a ledger entry -- and the customer is charged twice. Nothing in the
response says so: both deliveries answer 200.
"""

from __future__ import annotations

from .db import Database, sleep_ms
from .observ import traced

# How long the acquirer takes to answer. This is the window in which a retry
# that arrives immediately afterwards still sees the pre-capture state.
ACQUIRER_MS = 240.0

AUTHORIZED = "AUTHORIZED"
DECLINED = "DECLINED"


@traced()
def handle_payment_event(db: Database, payment_id: int, event_id: str) -> dict:
    """Apply one provider event: authorize, capture, and post to the ledger.

    The provider retries, so this can be called twice with the same event id.
    """
    with db.session() as session:
        payment = session.query("payments").filter_by(id=payment_id).one()

        outcome = acquirer_authorization(payment.amount_paise, event_id)

        payment.status = outcome
        payment.captured_paise += payment.amount_paise
        payment.last_event_id = event_id
        post_to_ledger(session, payment_id, payment.amount_paise)
        session.commit()
        return payment.as_dict()


def post_to_ledger(session, payment_id: int, amount_paise: int) -> int:
    """Append the capture to the settlement ledger. Returns the new entry id."""
    cursor = session.conn.execute(
        "INSERT INTO ledger_entries (payment_id, entry_type, amount) VALUES (?, 'CAPTURE', ?)",
        (payment_id, amount_paise),
    )
    return int(cursor.lastrowid)


def acquirer_authorization(amount_paise: int, event_id: str) -> str:
    """Stand-in for a real HTTP call to the card acquirer."""
    sleep_ms(ACQUIRER_MS)
    return AUTHORIZED if amount_paise > 0 else DECLINED


__all__ = ["ACQUIRER_MS", "acquirer_authorization", "handle_payment_event", "post_to_ledger"]
