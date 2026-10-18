"""Order state machine.

Tiny on purpose: a webhook can move PENDING -> AUTHORIZED, and only an order
that is *still* PENDING may be expired.
"""

from __future__ import annotations

PENDING = "PENDING"
AUTHORIZED = "AUTHORIZED"
CAPTURED = "CAPTURED"
CANCELLED = "CANCELLED"

TERMINAL_STATES = frozenset({CAPTURED, CANCELLED})


def apply_event(current: str, event_type: str, outcome: str) -> str:
    """Fold a payment event into the order state."""
    if event_type != "payment.authorized":
        return current
    if current in TERMINAL_STATES:
        return current
    return AUTHORIZED if outcome == "authorized" else current


def may_expire(current: str) -> bool:
    """Only an unpaid, un-authorised cart may be cancelled by the expiry worker."""
    return current == PENDING
