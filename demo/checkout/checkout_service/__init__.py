"""checkout-svc -- scenario 1: a lost update across three independent traces.

This package is a real, runnable service with a real bug. It is not a mock:
the race is a genuine write-skew between two concurrent read-modify-write
sequences on the same row, and the repair trace-repro applies to it is a
genuine source patch.
"""

from .checkout import InvalidStateError, capture_payment
from .db import Database
from .expiry import expire_cart
from .runtime import HttpError, capture_endpoint, expire_cart_job, handle_webhook
from .state_machine import AUTHORIZED, CANCELLED, CAPTURED, PENDING
from .webhooks import PaymentEvent, apply_webhook

__all__ = [
    "AUTHORIZED",
    "CANCELLED",
    "CAPTURED",
    "Database",
    "HttpError",
    "InvalidStateError",
    "PENDING",
    "PaymentEvent",
    "apply_webhook",
    "capture_endpoint",
    "capture_payment",
    "expire_cart",
    "expire_cart_job",
    "handle_webhook",
]
