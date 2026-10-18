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

from .db import Database
from .expiry import expire_lot
from .observ import current_span, get_telemetry, request_context
from .receipts import receive_stock
from .reservations import InsufficientStock, reserve_stock


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
        except InsufficientStock as exc:
            # A stale stock row is not an internal fault, it is a customer who
            # cannot buy the thing they asked for. 409 is the honest status.
            trace.status = "ERROR"
            trace.end_ns = getattr(current_span(), "end_ns", 0) or trace.end_ns
            telemetry.store.upsert_trace(trace)
            raise HttpError(409, str(exc), type(exc).__name__) from exc
    trace.status = "OK"
    span = current_span()
    trace.end_ns = getattr(span, "end_ns", 0) or trace.end_ns
    telemetry.store.upsert_trace(trace)
    return {"trace_id": trace.id, "status": 200, "body": result}


def receive_stock_endpoint(db: Database, payload: dict) -> dict:
    """POST /inventory/received -- the warehouse's at-least-once callback."""
    return _run(
        db,
        "POST /inventory/received",
        payload,
        receive_stock,
        db,
        int(payload.get("stock_id", 0)),
        str(payload.get("event_id", "evt_unknown")),
        int(payload.get("quantity", 0)),
    )


def stock_expiry_job(db: Database, payload: dict) -> dict:
    """stock-expiry worker -- a scheduled job with no request of its own."""
    return _run(
        db,
        "stock-expiry worker",
        payload,
        expire_lot,
        db,
        int(payload.get("stock_id", 0)),
    )


def reserve_endpoint(db: Database, payload: dict) -> dict:
    """POST /inventory/reserve -- the request that fails, and is not the bug."""
    return _run(
        db,
        "POST /inventory/reserve",
        payload,
        reserve_stock,
        db,
        int(payload.get("stock_id", 0)),
        int(payload.get("quantity", 1)),
    )


__all__ = [
    "HttpError",
    "receive_stock_endpoint",
    "reserve_endpoint",
    "stock_expiry_job",
]
