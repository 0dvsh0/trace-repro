"""payments-svc -- scenario 3: an at-least-once delivery applied twice.

This package is a real, runnable service with a real bug. It is not a mock: the
provider's retry really does arrive while the first call is still inside the
acquirer, and the repair trace-repro applies to it is a genuine source patch.
"""

from .db import Database
from .reconciliation import SettlementMismatch, reconcile_settlement
from .runtime import HttpError, payment_webhook, reconcile_endpoint
from .webhooks import handle_payment_event

__all__ = [
    "Database",
    "HttpError",
    "SettlementMismatch",
    "handle_payment_event",
    "payment_webhook",
    "reconcile_endpoint",
    "reconcile_settlement",
]
