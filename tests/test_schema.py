"""Schema and contract invariants.

The labels and relationship types are the contract between the ingest layer,
the Cypher an operator reads, and the MCP tool surface. If they drift, queries
silently match nothing instead of failing loudly.

``incident.json`` is the same idea one layer up: the contract between
trace-repro and a target repository it has never seen.
"""

from __future__ import annotations

import json

import pytest

from trace_repro.contract import (
    KINDS,
    ContractError,
    load_contract,
    parse_contract,
    read_contract_data,
    spec_for,
)
from trace_repro.graph import queries as Q
from trace_repro.models import Interference, StateEntity
from trace_repro.schema import (
    CAUSED_READ,
    DEFAULT_CAUSAL_WINDOW_MS,
    EMITTED,
    ENTITY_ACCESS_RELS,
    EXECUTED_FUNCTION,
    HAPPENED_BEFORE,
    HAS_SPAN,
    HAZARD_RAW,
    HAZARD_WAW,
    MODIFIED_BY_COMMIT,
    MUTATED,
    MUTATED_BY_FUNCTION,
    NODE_LABELS,
    NS_PER_MS,
    PARENT_SPAN,
    READ_ENTITY,
    REL_TYPES,
    WROTE_ENTITY,
    entity_id,
    kind_of,
)


def test_every_label_is_camel_case_and_unique():
    assert len(set(NODE_LABELS)) == len(NODE_LABELS)
    assert all(label[:1].isupper() and "_" not in label for label in NODE_LABELS)


def test_every_relationship_is_screaming_snake_case():
    assert len(set(REL_TYPES)) == len(REL_TYPES)
    assert all(rel.replace("_", "").isupper() for rel in REL_TYPES)


def test_bridge_relationships_are_declared():
    assert READ_ENTITY in REL_TYPES
    assert WROTE_ENTITY in REL_TYPES
    assert set(ENTITY_ACCESS_RELS) == {READ_ENTITY, WROTE_ENTITY}


def test_causal_relationships_are_declared():
    for rel in (HAPPENED_BEFORE, CAUSED_READ, MUTATED, EMITTED, EXECUTED_FUNCTION,
                MUTATED_BY_FUNCTION, HAS_SPAN, PARENT_SPAN, MODIFIED_BY_COMMIT):
        assert rel in REL_TYPES, f"{rel} is used but not declared"


def test_hazards_are_declared():
    assert HAZARD_WAW == "WAW"
    assert HAZARD_RAW == "RAW"


def test_entity_id_is_stable_and_namespaced():
    assert entity_id("Order", 8812) == "order:8812"
    assert entity_id("Order", 8812) == entity_id("ORDER", "8812")


def test_state_entity_key_matches_entity_id():
    assert StateEntity("Order", "8812").key == entity_id("Order", "8812")


def test_time_constants():
    assert NS_PER_MS == 1_000_000
    assert DEFAULT_CAUSAL_WINDOW_MS >= 1000


def test_queries_only_reference_declared_labels_and_rels():
    haystack = "\n".join(
        getattr(Q, name)
        for name in dir(Q)
        if name.isupper() and isinstance(getattr(Q, name), str)
    )
    haystack += Q.minimal_replay_subgraph()
    for rel in ("HAS_SPAN", "READ_ENTITY", "WROTE_ENTITY", "EMITTED", "MUTATED",
                "HAPPENED_BEFORE", "CAUSED_READ", "EXECUTED_FUNCTION"):
        assert rel in haystack, f"{rel} is not used by any shipped query"
    for label in ("Trace", "Span", "StateEntity", "StateMutation", "ASTFunction"):
        assert f":{label}" in haystack, f"(:{label}) is never queried"


def test_schema_statements_are_indexes():
    for stmt in Q.SCHEMA_STATEMENTS:
        assert stmt.startswith("CREATE INDEX ON :")


def test_interference_reports_the_race_window():
    interference = Interference(
        entity_key="orders:8812", entity_type="orders", primary_key="8812",
        other_trace_id="t", other_span_id="s", other_endpoint="/x", other_payload="{}",
        mutator_function="f", mutator_file="f.py", crash_function="g",
        crash_file="g.py", write_ts_ns=1_000_000, read_ts_ns=40_000_000,
        lock_held=False, hazard=HAZARD_WAW,
    )
    assert interference.race_window_ms == pytest.approx(39.0)


# --------------------------------------------------------------------------
# Entry-point classification
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("POST /webhooks/payment", "webhook"),
        ("POST /inventory/received", "webhook"),
        ("POST /payments/callback", "webhook"),
        ("cart-expiry worker", "worker"),
        ("stock-expiry worker", "worker"),
        ("nightly-sweep", "worker"),
        ("POST /checkout/capture", "request"),
        ("POST /billing/reconcile", "request"),
        ("", "request"),
    ],
)
def test_entry_endpoints_are_classified(endpoint, expected):
    assert kind_of(endpoint) == expected


def test_classification_is_case_insensitive():
    assert kind_of("POST /Webhooks/Payment") == kind_of("post /webhooks/payment")


def test_every_classified_kind_has_a_route_key():
    """``kind_of`` may only return kinds a contract can declare a route for."""
    for endpoint in ("POST /webhooks/x", "worker", "POST /checkout/capture"):
        assert kind_of(endpoint) in KINDS


# --------------------------------------------------------------------------
# The repository contract
# --------------------------------------------------------------------------


def _minimal_contract(**overrides) -> dict:
    base = {
        "key": "demo",
        "title": "t",
        "service": "svc",
        "package": "pkg",
        "entity_type": "orders",
        "primary_key": "1",
        "crash_endpoint": "POST /c",
        "outcome_field": "status",
        "seed": {"orders": {"id": 1, "status": "PENDING"}},
        "routes": {"request": "pkg.runtime:c"},
        "participants": [
            {"kind": "request", "endpoint": "POST /a", "offset_ms": 0.0},
            {"kind": "request", "endpoint": "POST /c", "offset_ms": 50.0},
        ],
    }
    base.update(overrides)
    return base


def test_a_minimal_contract_parses():
    spec = parse_contract(_minimal_contract())
    assert spec.incident_id == "incident_1"
    assert spec.entity_key == "orders:1"
    assert spec.offsets_ms == (0.0, 50.0)
    assert spec.route_for("request") == "pkg.runtime:c"
    assert spec.crash_participant.endpoint == "POST /c"


def test_a_missing_required_field_is_a_clear_error():
    broken = _minimal_contract()
    del broken["entity_type"]
    with pytest.raises(ContractError, match="entity_type"):
        parse_contract(broken)


def test_offsets_must_be_ascending():
    """A replay replays *starts*, so an out-of-order list is a contract bug."""
    broken = _minimal_contract(participants=[
        {"kind": "request", "endpoint": "POST /a", "offset_ms": 90.0},
        {"kind": "request", "endpoint": "POST /c", "offset_ms": 10.0},
    ])
    with pytest.raises(ContractError, match="ascending"):
        parse_contract(broken)


def test_an_unknown_kind_is_rejected():
    broken = _minimal_contract(participants=[
        {"kind": "batch", "endpoint": "POST /a", "offset_ms": 0.0},
        {"kind": "request", "endpoint": "POST /c", "offset_ms": 10.0},
    ])
    with pytest.raises(ContractError, match="kind"):
        parse_contract(broken)


def test_a_single_participant_is_not_a_cross_trace_incident():
    broken = _minimal_contract(participants=[
        {"kind": "request", "endpoint": "POST /a", "offset_ms": 0.0},
    ])
    with pytest.raises(ContractError, match="at least two participants"):
        parse_contract(broken)


def test_an_unroutable_kind_is_reported_by_name():
    spec = parse_contract(_minimal_contract())
    with pytest.raises(ContractError, match="no route for kind 'worker'"):
        spec.route_for("worker")


def test_a_repository_without_a_contract_is_absent_not_broken(tmp_path):
    """A target that has not adopted the contract yet must still be codegen-able."""
    assert spec_for(tmp_path) is None
    assert read_contract_data(tmp_path) is None
    with pytest.raises(ContractError, match="incident.json"):
        load_contract(tmp_path)


def test_invalid_json_names_the_file(tmp_path):
    (tmp_path / "incident.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ContractError, match="not valid JSON"):
        load_contract(tmp_path)


def test_every_shipped_scenario_publishes_a_valid_contract():
    from .conftest import DEMO, SCENARIOS

    for scenario in SCENARIOS:
        repo = DEMO / scenario
        spec = load_contract(repo)
        assert spec.key, scenario
        assert spec.crash_endpoint in {p.endpoint for p in spec.participants}, scenario
        assert spec.offsets_ms[0] == 0.0, scenario
        assert spec.outcome_field, scenario
        for participant in spec.participants:
            assert participant.route or spec.route_for(participant.kind)


def test_shipped_contracts_are_json_not_python():
    """The generated test reads them with the standard library alone."""
    from .conftest import DEMO, SCENARIOS

    for scenario in SCENARIOS:
        raw = (DEMO / scenario / "incident.json").read_text(encoding="utf-8")
        assert isinstance(json.loads(raw), dict)
