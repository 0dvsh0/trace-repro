"""A synthetic three-trace incident, used by several test modules.

Shape mirrors the production story: a webhook and an expiry worker from two
independent traces both write one row without a lock, then a third trace reads
it and crashes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from trace_repro.ingest.tracer import Telemetry
from trace_repro.models import Span, Trace
from trace_repro.schema import READ_ENTITY, WROTE_ENTITY

BASE_NS = 1_700_000_000_000_000_000

ENDPOINT_WEBHOOK = "POST /webhooks/payment"
ENDPOINT_WORKER = "cart-expiry worker"
ENDPOINT_CRASH = "POST /checkout/capture"

CRASH_SPAN_ID = "span_C"


@dataclass
class SyntheticIncident:
    store: object
    webhook_trace: Trace
    worker_trace: Trace
    crash_trace: Trace
    crash_span: Span
    entity_key: str = "orders:8812"
    offsets_ms: tuple[float, float, float] = (0.0, 25.0, 100.0)
    _telemetry: Telemetry | None = None
    _t: int = field(default=0)

    @property
    def crash_span_id(self) -> str:
        return self.crash_span.id


def _at(offset_ms: float) -> int:
    return BASE_NS + int(offset_ms * 1_000_000)


def build_incident(store, *, webhook_locks: bool = False,
                   worker_locks: bool = False,
                   files: dict[str, str] | None = None) -> SyntheticIncident:
    """Record a three-trace collision into ``store``.

    ``webhook_locks`` / ``worker_locks`` model the repaired code, so the same
    builder can produce the before and after graphs. ``files`` maps a function
    name to a real path, which lets a caller point the repair planner at a
    repository on disk.
    """
    from trace_repro.models import StateEntity

    paths = files or {}
    telemetry = Telemetry(store, service="checkout-svc")
    entity = StateEntity("orders", "8812")
    store.upsert_entity(entity)

    webhook_trace = Trace("trace_A", ENDPOINT_WEBHOOK, "checkout-svc",
                          '{"order_id": 8812, "event_id": "evt_1"}',
                          start_ns=_at(0.0))
    worker_trace = Trace("trace_B", ENDPOINT_WORKER, "checkout-svc",
                         '{"order_id": 8812, "job": "cart-expiry"}',
                         start_ns=_at(25.0))
    crash_trace = Trace("trace_C", ENDPOINT_CRASH, "checkout-svc",
                        '{"order_id": 8812, "amount_paise": 450000}',
                        start_ns=_at(100.0))
    for trace in (webhook_trace, crash_trace, worker_trace):
        store.upsert_trace(trace)

    webhook_span = Span("span_A", "trace_A", "apply_webhook",
                        start_ns=_at(0.0), end_ns=_at(40.0),
                        function_name="apply_webhook",
                        file_path=paths.get("apply_webhook", "/svc/webhooks.py"))
    worker_span = Span("span_B", "trace_B", "expire_cart",
                       start_ns=_at(25.0), end_ns=_at(70.0),
                       function_name="expire_cart",
                       file_path=paths.get("expire_cart", "/svc/expiry.py"))
    crash_span = Span("span_C", "trace_C", "capture_payment",
                      start_ns=_at(100.0), end_ns=_at(101.0),
                      function_name="capture_payment",
                      file_path=paths.get("capture_payment", "/svc/checkout.py"))
    for span, trace in ((webhook_span, webhook_trace), (worker_span, worker_trace),
                        (crash_span, crash_trace)):
        span.trace_id = trace.id
        store.upsert_span(span)
        store.link_trace_span(trace.id, span.id)

    # Trace A reads the row, does slow work, then writes -- no lock.
    _record(telemetry, webhook_span, entity, "status", "PENDING", "AUTHORIZED",
            webhook_locks, _at(0.5), _at(40.0))
    # Trace B read the row *before* A committed, then writes its stale view.
    _record(telemetry, worker_span, entity, "status", "PENDING", "CANCELLED",
            worker_locks, _at(25.5), _at(70.0))
    # Trace C reads the corrupted row and crashes.
    store.record_entity_access(crash_span.id, entity, READ_ENTITY, _at(100.5), False)

    # Only the *latest* write can have caused the read. Linking CAUSED_READ from
    # every mutation would let shortestPath jump straight from the first write
    # to the crash and silently drop the writer in between -- which is exactly
    # the kind of bug that makes a replay test the wrong interleaving.
    last = telemetry._last_mutation[(entity.entity_type, entity.primary_key)]
    store.link_caused_read(last, CRASH_SPAN_ID)

    crash_span.status = "ERROR"
    crash_span.error_type = "InvalidStateError"
    crash_span.error_message = "Order #8812 is CANCELLED, cannot capture"
    store.upsert_span(crash_span)
    telemetry.flush()

    return SyntheticIncident(
        store=store,
        webhook_trace=webhook_trace,
        worker_trace=worker_trace,
        crash_trace=crash_trace,
        crash_span=crash_span,
        _telemetry=telemetry,
    )


def _record(telemetry: Telemetry, span: Span, entity, field: str,
            old: str, new: str, locked: bool, read_ns: int, write_ns: int) -> None:
    """One read-modify-write: read at ``read_ns``, commit at ``write_ns``."""
    from trace_repro.models import StateMutation

    store = telemetry.store
    store.record_entity_access(span.id, entity, READ_ENTITY, read_ns, locked)

    seq = telemetry._seq.get((entity.entity_type, entity.primary_key), 0)
    telemetry._seq[(entity.entity_type, entity.primary_key)] = seq + 1

    mutation = StateMutation(
        id=f"mut_{field}_{span.id}",
        trace_id=span.trace_id,
        span_id=span.id,
        entity_type=entity.entity_type,
        primary_key=entity.primary_key,
        field=field,
        old_val=old,
        new_val=new,
        ts_ns=write_ns,
        seq_no=seq,
        lock_held=locked,
        function_name=span.function_name,
    )
    store.record_mutation(mutation)
    store.record_entity_access(span.id, entity, WROTE_ENTITY, write_ns, locked)

    prev = telemetry._last_mutation.get((entity.entity_type, entity.primary_key))
    if prev:
        store.link_happened_before(prev, mutation.id, 30.0, True)
    telemetry._last_mutation[(entity.entity_type, entity.primary_key)] = mutation.id
