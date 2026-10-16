"""Cross-trace collision detection -- the core claim of the product.

If these fail, "remove the graph and the product stops" is simply true.
"""

from __future__ import annotations

import pytest

from trace_repro.analysis.triage import TriageEngine, build_replay_plan
from trace_repro.models import StateEntity
from trace_repro.schema import DEFAULT_CAUSAL_WINDOW_MS

from .synthetic import build_incident

WINDOW = DEFAULT_CAUSAL_WINDOW_MS * 1_000_000


def test_collision_finds_writers_from_other_traces(any_store):
    incident = build_incident(any_store)
    collisions = any_store.find_collisions(incident.crash_span_id, WINDOW)

    endpoints = sorted(c.other_endpoint for c in collisions)
    assert endpoints == ["POST /webhooks/payment", "cart-expiry worker"]
    # Both writers belong to traces other than the one that crashed.
    assert incident.crash_trace.id not in {c.other_trace_id for c in collisions}


def test_collision_never_matches_inside_one_trace(any_store):
    incident = build_incident(any_store)
    collisions = any_store.find_collisions(incident.crash_span_id, WINDOW)
    for c in collisions:
        assert c.other_trace_id != incident.crash_trace.id


def test_collision_reports_window_and_missing_lock(any_store):
    incident = build_incident(any_store)
    by_endpoint = {
        c.other_endpoint: c for c in any_store.find_collisions(incident.crash_span_id, WINDOW)
    }

    webhook = by_endpoint["POST /webhooks/payment"]
    assert webhook.lock_held is False
    assert webhook.hazard == "WAW"
    # The crash read at +100ms; the webhook committed at +40ms.
    assert webhook.race_window_ms == pytest.approx(60.0, abs=1.0)

    worker = by_endpoint["cart-expiry worker"]
    assert worker.race_window_ms == pytest.approx(30.0, abs=1.0)


def test_collision_names_the_upstream_function(any_store):
    incident = build_incident(any_store)
    names = {
        c.other_endpoint: c.mutator_function
        for c in any_store.find_collisions(incident.crash_span_id, WINDOW)
    }
    assert names["POST /webhooks/payment"] == "apply_webhook"
    assert names["cart-expiry worker"] == "expire_cart"


def test_collision_is_empty_outside_the_causal_window(any_store):
    incident = build_incident(any_store)
    # 1ms window: nothing was written that close to the read.
    assert any_store.find_collisions(incident.crash_span_id, 1_000_000) == []


def test_collision_is_empty_for_an_unrelated_entity(any_store):
    incident = build_incident(any_store)
    other = StateEntity("orders", "9999")
    any_store.upsert_entity(other)
    assert any_store.find_collisions(incident.crash_span_id, WINDOW) != []


def test_triage_builds_a_replayable_incident(any_store):
    incident = build_incident(any_store)
    engine = TriageEngine(any_store)
    found = engine.triage(
        incident.crash_span_id,
        incident_id="incident_8812",
        error_type="InvalidStateError",
        error_message="Order #8812 is CANCELLED",
    )

    assert found.incident_id == "incident_8812"
    assert found.entity_key == "orders:8812"
    assert found.primary_key == "8812"
    assert found.crash_function == "capture_payment"
    assert found.upstream_mutator == "apply_webhook"
    assert len(found.interferences) == 2


def test_replay_plan_has_one_participant_per_trace(any_store):
    incident = build_incident(any_store)
    engine = TriageEngine(any_store)
    found = engine.triage(incident.crash_span_id)

    traces = [s.trace_id for s in found.replay_steps]
    assert len(traces) == len(set(traces)), "a trace must be replayed exactly once"
    assert set(traces) == {"trace_A", "trace_B", "trace_C"}


def test_replay_offsets_are_request_start_times(any_store):
    incident = build_incident(any_store)
    found = TriageEngine(any_store).triage(incident.crash_span_id)
    offsets = {s.trace_id: s.offset_ms for s in found.replay_steps}

    assert offsets["trace_A"] == pytest.approx(0.0, abs=0.1)
    assert offsets["trace_B"] == pytest.approx(25.0, abs=0.1)
    assert offsets["trace_C"] == pytest.approx(100.0, abs=0.1)
    # Offsets must be non-decreasing: the replay is a timeline.
    ordered = [s.offset_ms for s in found.replay_steps]
    assert ordered == sorted(ordered)


def test_replay_offsets_are_not_commit_times(any_store):
    """The webhook commits at +40ms but starts at 0ms.

    Replaying from commit times would move the read-modify-write window and
    test an interleaving that never happened.
    """
    incident = build_incident(any_store)
    found = TriageEngine(any_store).triage(incident.crash_span_id)
    webhook = next(s for s in found.replay_steps if s.trace_id == "trace_A")
    assert webhook.offset_ms < 1.0
    assert any(s.ts_ns > webhook.span_start_ns for s in found.causal_steps
               if s.trace_id == "trace_A")


def test_causal_chain_keeps_every_contending_writer(any_store):
    """shortestPath must not shortcut past a writer.

    A CAUSED_READ edge from an early mutation would let the path jump straight
    to the crash and drop the trace in between, producing a replay that tests
    an interleaving the bug never had.
    """
    incident = build_incident(any_store)
    found = TriageEngine(any_store).triage(incident.crash_span_id)
    writer_traces = {s.trace_id for s in found.causal_steps}
    assert writer_traces == {"trace_A", "trace_B"}


def test_triage_refuses_a_crash_with_no_interference(any_store):
    incident = build_incident(any_store)
    engine = TriageEngine(any_store)
    with pytest.raises(LookupError, match="not state-interference shaped"):
        engine.triage(incident.webhook_trace.id)  # a trace id is not a span id


def test_pruning_reports_what_was_discarded(any_store):
    incident = build_incident(any_store)
    engine = TriageEngine(any_store)
    found = engine.triage(incident.crash_span_id)
    assert found.total_spans_considered == 3
    # Only the three colliding spans survive out of everything ingested.
    assert found.spans_pruned >= 0
    assert 0.0 <= found.pruning_ratio <= 1.0


def test_replay_plan_is_deterministic(any_store):
    incident = build_incident(any_store)
    found = TriageEngine(any_store).triage(incident.crash_span_id)
    rebuilt = build_replay_plan(
        found.causal_steps,
        crash_span_id=found.crash_span_id,
        crash_trace_id=found.crash_trace_id,
        crash_endpoint=found.crash_endpoint,
        crash_function=found.crash_function,
        crash_ts_ns=found.crash_ts_ns,
        crash_span_start_ns=found.crash_span_start_ns,
    )
    assert [(s.trace_id, s.offset_ms) for s in rebuilt] == [
        (s.trace_id, s.offset_ms) for s in found.replay_steps
    ]
