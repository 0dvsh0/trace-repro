"""HTTP-shaped entry points.

Each entry point starts a **trace**, then the decorated handler runs inside it.
Three independent calls to these functions produce three completely disjoint
``trace_id`` trees -- which is precisely the condition trace-repro exists to
detect, because no amount of reading the failing trace will reveal the other two.

Every entry point has the same signature, ``fn(db, payload)``. That is the whole
dispatch contract: the generated reproduction test looks the route up by kind in
``incident.json`` and calls it, so it never needs to know this service's shape.
"""

from __future__ import annotations

from .checkout import InvalidStateError, capture_payment
from .db import Database
from .expiry import expire_cart
from .observ import current_span, get_telemetry, request_context
from .webhooks import PaymentEvent, apply_webhook


class HttpError(Exception):
    """Mirror of an HTTP error response, so the service reports status codes honestly."""

    def __init__(self, status: int, message: str, error_type: str):
        super().__init__(message)
        self.status = status
        self.message = message
        self.error_type = error_type


def _run(db: Database, endpoint: str, payload: dict, body, *args) -> dict:
    telemetry = get_telemetry()
    if telemetry is None:
        return {"trace_id": "", "status": 200, "body": body(*args)}

    trace = telemetry.start_request(endpoint, payload)
    with request_context(trace):
        try:
            result = body(*args)
        except InvalidStateError as exc:
            trace.status = "ERROR"
            trace.end_ns = getattr(current_span(), "end_ns", 0) or trace.end_ns
            telemetry.store.upsert_trace(trace)
            raise HttpError(500, str(exc), type(exc).__name__) from exc
    trace.status = "OK"
    span = current_span()
    trace.end_ns = getattr(span, "end_ns", 0) or trace.end_ns
    telemetry.store.upsert_trace(trace)
    return {"trace_id": trace.id, "status": 200, "body": result}


def handle_webhook(db: Database, payload: dict) -> dict:
    """POST /webhooks/payment -- the payment provider's at-least-once callback."""
    return _run(
        db,
        "POST /webhooks/payment",
        payload,
        apply_webhook,
        db,
        PaymentEvent(
            id=payload.get("event_id", "evt_unknown"),
            order_id=int(payload.get("order_id", 0)),
            type=payload.get("type", "payment.authorized"),
        ),
    )


def expire_cart_job(db: Database, payload: dict) -> dict:
    """cart-expiry worker -- a scheduled job with no request of its own."""
    return _run(
        db,
        "cart-expiry worker",
        payload,
        expire_cart,
        db,
        int(payload.get("order_id", 0)),
    )


def capture_endpoint(db: Database, payload: dict) -> dict:
    """POST /checkout/capture -- the request that fails, and is not the bug."""
    return _run(
        db,
        "POST /checkout/capture",
        payload,
        capture_payment,
        db,
        int(payload.get("order_id", 0)),
        int(payload.get("amount_paise", 450_000)),
    )


__all__ = ["HttpError", "capture_endpoint", "expire_cart_job", "handle_webhook"]
