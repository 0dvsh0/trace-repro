"""Telemetry capture and the causal edges it produces."""

from __future__ import annotations

import pytest

from trace_repro.graph.memory import MemoryStore
from trace_repro.ingest.tracer import Telemetry, frame_info, short_id
from trace_repro.models import StateEntity
from trace_repro.schema import (
    CAUSED_READ,
    HAPPENED_BEFORE,
    READ_ENTITY,
    WROTE_ENTITY,
)


@pytest.fixture()
def telemetry(memory_store):
    return Telemetry(memory_store, service="checkout-svc")


def test_frame_info_reports_the_calling_function():
    name, path, lineno = frame_info(depth=1)
    assert name == "test_frame_info_reports_the_calling_function"
    assert path.endswith("test_telemetry.py")
    assert lineno > 0


def test_frame_info_ignores_trace_repro_internals():
    """Attribution must survive a helper being added inside the package."""
    from trace_repro.ingest.tracer import caller_frame

    def through_a_helper():
        return caller_frame()

    frame = through_a_helper()
    assert frame is not None
    assert frame.f_code.co_name == "through_a_helper"


def test_ids_are_unique():
    assert len({short_id() for _ in range(500)}) == 500


def test_span_records_status_and_timing(telemetry):
    trace = telemetry.start_request("GET /x", {"a": 1})
    with telemetry.span("handler", trace) as span:
        assert span.trace_id == trace.id
    assert span.status == "OK"
    assert span.end_ns >= span.start_ns
    assert span.duration_ms >= 0.0


def test_span_is_attributed_to_the_calling_function(telemetry):
    trace = telemetry.start_request("GET /x")

    def the_handler():
        with telemetry.span("handler", trace) as span:
            return span

    span = the_handler()
    assert span.function_name == "the_handler"
    assert span.file_path.endswith("test_telemetry.py")


def test_span_records_the_error_on_failure(telemetry):
    trace = telemetry.start_request("GET /x")
    with pytest.raises(ValueError):
        with telemetry.span("handler", trace) as span:
            raise ValueError("boom")
    assert span.status == "ERROR"
    assert span.error_type == "ValueError"
    assert span.error_message == "boom"
    assert telemetry.errors and telemetry.errors[0][1] == "ValueError"


def test_trace_payload_is_canonical_json(telemetry):
    trace = telemetry.start_request("GET /x", {"b": 2, "a": 1})
    assert trace.payload_json == '{"a":1,"b":2}'


def test_bridge_edges_are_created(telemetry):
    entity = StateEntity("orders", "8812")
    trace = telemetry.start_request("GET /x")
    with telemetry.span("handler", trace) as span:
        telemetry.record_read(span, entity)
        telemetry.record_write(span, entity, "status", "PENDING", "AUTHORIZED")

    out = telemetry.store.neighbours("Span", span.id, READ_ENTITY)
    writes = telemetry.store.neighbours("Span", span.id, WROTE_ENTITY)
    assert out == [entity.key]
    assert writes == [entity.key]
    assert telemetry.store.incoming("StateEntity", entity.key, WROTE_ENTITY) == [span.id]


def test_mutations_are_ordered_per_entity(telemetry):
    entity = StateEntity("orders", "8812")
    trace = telemetry.start_request("GET /x")
    with telemetry.span("handler", trace) as span:
        for i in range(3):
            telemetry.record_write(span, entity, "status", f"s{i}", f"s{i + 1}")

    seqs = sorted(m.seq_no for m in telemetry.store.mutations.values())
    assert seqs == [0, 1, 2]


def test_happened_before_links_consecutive_writes(telemetry):
    entity = StateEntity("orders", "8812")
    trace = telemetry.start_request("GET /x")
    with telemetry.span("handler", trace) as span:
        first = telemetry.record_write(span, entity, "status", "PENDING", "AUTHORIZED")
        second = telemetry.record_write(span, entity, "status", "AUTHORIZED", "CAPTURED")

    chain = telemetry.store.neighbours("StateMutation", first.id, HAPPENED_BEFORE)
    assert chain == [second.id]


def test_caused_read_is_linked_from_the_latest_write(telemetry):
    entity = StateEntity("orders", "8812")
    trace = telemetry.start_request("GET /x")
    with telemetry.span("writer", trace) as writer:
        first = telemetry.record_write(writer, entity, "status", "PENDING", "AUTHORIZED")
        second = telemetry.record_write(writer, entity, "status", "AUTHORIZED", "CAPTURED")
    with telemetry.span("reader", trace) as reader:
        telemetry.record_read(reader, entity)

    linked = telemetry.store.neighbours("StateMutation", first.id, CAUSED_READ)
    assert linked == [], "only the latest write can have caused the read"
    assert telemetry.store.neighbours("StateMutation", second.id, CAUSED_READ) == [reader.id]


def test_lock_state_is_recorded(telemetry):
    entity = StateEntity("orders", "8812")
    trace = telemetry.start_request("GET /x")
    with telemetry.span("handler", trace) as span:
        unlocked = telemetry.record_write(span, entity, "status", "A", "B", lock_held=False)
        locked = telemetry.record_write(span, entity, "status", "B", "C", lock_held=True)

    assert telemetry.store.mutations[unlocked.id].lock_held is False
    assert telemetry.store.mutations[locked.id].lock_held is True


def test_different_entities_have_independent_sequences(telemetry):
    orders = StateEntity("orders", "8812")
    carts = StateEntity("carts", "7")
    trace = telemetry.start_request("GET /x")
    with telemetry.span("handler", trace) as span:
        telemetry.record_write(span, orders, "status", "A", "B")
        telemetry.record_write(span, carts, "status", "C", "D")

    seqs = sorted(m.seq_no for m in telemetry.store.mutations.values())
    assert seqs == [0, 0], "each entity has its own write sequence"


def test_counters_track_work(telemetry):
    entity = StateEntity("orders", "1")
    trace = telemetry.start_request("GET /x")
    with telemetry.span("handler", trace) as span:
        telemetry.record_read(span, entity)
        telemetry.record_write(span, entity, "status", "A", "B")

    assert telemetry.counts["traces"] == 1
    assert telemetry.counts["spans"] == 1
    assert telemetry.counts["reads"] == 1
    assert telemetry.counts["mutations"] == 1


def test_telemetry_is_optional(telemetry):
    """Handlers must work un-instrumented; this is how the demo runs untested."""
    assert telemetry.store is not None
    trace = telemetry.start_request("GET /x")
    assert trace.entry_endpoint == "GET /x"


def test_memory_store_exposes_the_same_bridge_api():
    store = MemoryStore()
    entity = StateEntity("orders", "1")
    store.upsert_entity(entity)
    store.record_entity_access("span", entity, READ_ENTITY, 10, False)
    store.record_entity_access("span", entity, WROTE_ENTITY, 20, True)
    assert store.neighbours("Span", "span", READ_ENTITY) == ["orders:1"]
    assert store.neighbours("Span", "span", WROTE_ENTITY) == ["orders:1"]
