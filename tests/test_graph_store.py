"""Graph backends: both must answer traversal questions identically.

``any_store`` runs every test against the in-process graph *and* against a live
FalkorDB, which is how the "the graph does the real work" claim stays honest.
"""

from __future__ import annotations

import pytest

from trace_repro.graph import queries as Q
from trace_repro.graph.facade import parse_graph_url
from trace_repro.graph.store import GraphStore
from trace_repro.models import ASTFunction, StateEntity, Trace
from trace_repro.schema import (
    CAUSED_READ,
    EMITTED,
    EXECUTED_FUNCTION,
    HAPPENED_BEFORE,
    HAS_SPAN,
    MUTATED,
    READ_ENTITY,
    WROTE_ENTITY,
)

from .synthetic import build_incident

WINDOW = 60_000 * 1_000_000


# --------------------------------------------------------------------------
# URL handling
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,graph,host",
    [
        ("falkordb://incident_8812", "incident_8812", "localhost"),
        ("falkordb://localhost:6379/trace_prod", "trace_prod", "localhost"),
        ("memory://local", "local", "localhost"),
        ("incident_8812", "incident_8812", "localhost"),
    ],
)
def test_graph_url_parsing(url, graph, host):
    parts = parse_graph_url(url)
    assert parts["graph"] == graph
    assert parts["host"] == host


def test_memory_url_opens_the_in_process_graph():
    from trace_repro.graph.memory import MemoryStore

    assert isinstance(parse_graph_url("memory://x")["scheme"], str)
    from trace_repro.graph.facade import open_graph

    assert isinstance(open_graph("memory://x"), MemoryStore)


# --------------------------------------------------------------------------
# Both backends
# --------------------------------------------------------------------------


def test_both_backends_implement_the_contract(memory_store):
    for name in dir(GraphStore):
        if name.startswith("_"):
            continue
        assert hasattr(memory_store, name), f"MemoryStore is missing {name}"


def test_trace_span_function_wiring(any_store):
    trace = Trace("t1", "POST /x", "svc")
    any_store.upsert_trace(trace)
    build_incident(any_store)
    fn = ASTFunction("apply_webhook", "/svc/webhooks.py", (1, 20))
    any_store.upsert_function(fn)
    any_store.link_span_function("span_A", fn)

    # The graph must be able to walk span -> function, which is how the repair
    # planner gets from a telemetry edge to a line of source.
    collisions = any_store.find_collisions("span_C", 60_000 * 1_000_000)
    webhook = next(c for c in collisions if c.mutator_function == "apply_webhook")
    assert webhook.mutator_file == "/svc/webhooks.py"


def test_span_resolves_to_a_function(any_store):
    incident = build_incident(any_store)
    engine_collisions = any_store.find_collisions(incident.crash_span_id, WINDOW)
    names = {c.mutator_function for c in engine_collisions}
    assert "apply_webhook" in names


def test_causal_chain_is_ordered(any_store):
    incident = build_incident(any_store)
    entity = StateEntity("orders", "8812")
    steps = any_store.causal_chain(entity, incident.crash_span_id)
    assert len(steps) == 2
    assert [s.field for s in steps] == ["status", "status"]
    assert steps[0].trace_id == "trace_A"
    assert steps[1].trace_id == "trace_B"
    assert steps[0].ts_ns < steps[1].ts_ns


def test_causal_chain_keeps_the_write_deltas(any_store):
    incident = build_incident(any_store)
    steps = any_store.causal_chain(StateEntity("orders", "8812"), incident.crash_span_id)
    assert [(s.old_val, s.new_val) for s in steps] == [
        ("PENDING", "AUTHORIZED"),
        ("PENDING", "CANCELLED"),
    ]
    assert all(s.lock_held is False for s in steps)


def test_causal_chain_is_empty_for_an_untouched_entity(any_store):
    build_incident(any_store)
    assert any_store.causal_chain(StateEntity("orders", "0000"), "span_C") == []


def test_entity_writers_reports_lock_state(any_store):
    build_incident(any_store)
    writers = any_store.entity_writers(StateEntity("orders", "8812"))
    assert [w["function_name"] for w in writers] == ["apply_webhook", "expire_cart"]
    assert all(w["lock_held"] is False for w in writers)


def test_entity_writers_reports_one_row_per_writer(any_store):
    """Three field writes from one request are one interfering writer."""
    incident = build_incident(any_store)
    entity = StateEntity("orders", "8812")
    telemetry = incident._telemetry
    with telemetry.span("extra_handler", incident.webhook_trace) as span:
        for value in ("1", "2", "3"):
            telemetry.record_write(span, entity, "status", value, value)
    telemetry.flush()

    writers = any_store.entity_writers(entity)
    assert len(writers) == 3, "three writers, not five"
    assert len({w["span_id"] for w in writers}) == 3
    assert span.id in {w["span_id"] for w in writers}
    assert [w["function_name"] for w in writers[:2]] == ["apply_webhook", "expire_cart"]


def test_entity_writers_after_repair(any_store):
    build_incident(any_store, webhook_locks=True, worker_locks=True)
    writers = any_store.entity_writers(StateEntity("orders", "8812"))
    assert all(w["lock_held"] is True for w in writers)


def test_counts(any_store):
    build_incident(any_store)
    assert any_store.trace_count() == 3
    assert any_store.span_count() == 3


def test_clear_empties_the_graph(any_store):
    build_incident(any_store)
    any_store.clear()
    assert any_store.span_count() == 0
    assert any_store.trace_count() == 0


def test_span_lookup_round_trips(any_store):
    build_incident(any_store)
    span = any_store.span_by_id("span_A")
    assert span is not None
    assert span.trace_id == "trace_A"
    assert span.function_name == "apply_webhook"
    assert any_store.span_by_id("nope") is None


def test_trace_lookup_round_trips(any_store):
    build_incident(any_store)
    trace = any_store.trace_by_id("trace_B")
    assert trace is not None
    assert trace.entry_endpoint == "cart-expiry worker"
    assert any_store.trace_by_id("nope") is None


def test_entry_endpoint_of_span(any_store):
    build_incident(any_store)
    assert any_store.entry_endpoint_of_span("span_B") == "cart-expiry worker"


def test_related_schema_constants_are_declared():
    for const in (HAS_SPAN, WROTE_ENTITY, READ_ENTITY, MUTATED, EMITTED,
                  HAPPENED_BEFORE, CAUSED_READ, EXECUTED_FUNCTION):
        assert isinstance(const, str) and const


def test_canonical_queries_are_valid_cypher():
    """Every shipped query must at least be syntactically complete."""
    for name in ("CROSS_TRACE_COLLISION", "REPLAY_DETAIL", "ENTITY_WRITERS",
                 "PRUNE_STATS", "UPSTREAM_MUTATOR"):
        text = getattr(Q, name)
        assert "MATCH" in text, f"{name} does not match anything"
        assert "RETURN" in text, f"{name} returns nothing"
    replay = Q.minimal_replay_subgraph(12)
    assert "shortestPath" in replay
    assert "*..12" in replay


def test_both_backends_render_the_same_collision_shape(memory_store, any_store):
    a = build_incident(memory_store)
    b = build_incident(any_store)
    shape = lambda i: sorted(  # noqa: E731
        (c.other_endpoint, round(c.race_window_ms, 1), c.lock_held, c.hazard)
        for c in i
    )
    assert shape(memory_store.find_collisions(a.crash_span_id, WINDOW)) == \
        shape(any_store.find_collisions(b.crash_span_id, WINDOW))
