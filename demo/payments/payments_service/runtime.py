"""HTTP-shaped entry points.

Each entry point starts a **trace**, then the decorated handler runs inside it.
Note what makes this scenario different from the other two: the two colliding
writers are the *same* handler, in two different traces, with two different
request ids and one event id. No amount of correlating the failing request back
through the logs finds the other delivery -- they share nothing but the row.

Every entry point has the same signature, ``fn(db, payload)``. That is the whole
dispatch contract: the generated reproduction test looks the route up by kind in
``incident.json`` and calls it, so it never needs to know this service's shape.
"""

from __future__ import annotations

from .db import Database
from .observ import current_span, get_telemetry, request_context
from .reconciliation import SettlementMismatch, reconcile_settlement
from .webhooks import handle_payment_event


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
        except SettlementMismatch as exc:
            trace.status = "ERROR"
            trace.end_ns = getattr(current_span(), "end_ns", 0) or trace.end_ns
            telemetry.store.upsert_trace(trace)
            raise HttpError(500, str(exc), type(exc).__name__) from exc
    trace.status = "OK"
    span = current_span()
    trace.end_ns = getattr(span, "end_ns", 0) or trace.end_ns
    telemetry.store.upsert_trace(trace)
    return {"trace_id": trace.id, "status": 200, "body": result}


def payment_webhook(db: Database, payload: dict) -> dict:
    """POST /webhooks/payment -- the provider's at-least-once callback."""
    return _run(
        db,
        "POST /webhooks/payment",
        payload,
        handle_payment_event,
        db,
        int(payload.get("payment_id", 0)),
        str(payload.get("event_id", "evt_unknown")),
    )


def reconcile_endpoint(db: Database, payload: dict) -> dict:
    """POST /billing/reconcile -- the request that fails, and is not the bug."""
    return _run(
        db,
        "POST /billing/reconcile",
        payload,
        reconcile_settlement,
        db,
        int(payload.get("payment_id", 0)),
    )


__all__ = ["HttpError", "payment_webhook", "reconcile_endpoint"]
