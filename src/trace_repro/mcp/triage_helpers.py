"""Small helpers shared by the MCP tools.

Kept separate so the server module stays focused on protocol concerns.
"""

from __future__ import annotations

from ..graph.store import GraphStore
from ..models import CausalStep, StateEntity


def build_replay(
    store: GraphStore,
    entity: StateEntity,
    crash_span_id: str,
    crash_trace_id: str,
    max_depth: int = 12,
) -> tuple[list[CausalStep], int, str]:
    """Return ``(replay_steps, crash_ts_ns, crash_payload_json)`` for a crash."""
    from ..analysis.triage import build_replay_plan

    steps = store.causal_chain(entity, crash_span_id, max_depth)
    collisions = store.find_collisions(crash_span_id, 0)
    crash_ts = max((c.read_ts_ns for c in collisions), default=0)

    trace = None
    getter = getattr(store, "trace_by_id", None)
    if getter and crash_trace_id:
        trace = getter(crash_trace_id)
    payload = trace.payload_json if trace else "{}"

    crash_fn = ""
    getter_span = getattr(store, "span_by_id", None)
    if getter_span:
        span = getter_span(crash_span_id)
        crash_fn = span.function_name if span else ""

    endpoint = ""
    getter_ep = getattr(store, "entry_endpoint_of_span", None)
    if getter_ep:
        endpoint = getter_ep(crash_span_id)

    span_start = crash_ts
    if getter_span:
        span = getter_span(crash_span_id)
        if span:
            span_start = span.start_ns

    plan = build_replay_plan(
        steps,
        crash_span_id=crash_span_id,
        crash_trace_id=crash_trace_id,
        crash_endpoint=endpoint,
        crash_ts_ns=crash_ts,
        crash_payload_json=payload,
        crash_span_start_ns=span_start,
        crash_function=crash_fn,
    )
    return plan, crash_ts, payload
