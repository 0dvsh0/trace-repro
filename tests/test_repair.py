"""The AST-driven repair.

These tests pin down the thing that is easy to get wrong about automated
patching: it must produce *correct* Python, be idempotent, and repair every
contending writer rather than just the one the crash blamed.
"""

from __future__ import annotations

import ast
import shutil
import textwrap
from pathlib import Path

import pytest

from trace_repro.analysis.triage import TriageEngine
from trace_repro.repair.patch import (
    RepairError,
    UpstreamLocator,
    apply_incident,
    plan_incident,
    revert,
)
from trace_repro.repair.plan import RepairPlanner, _apply_to_source

from .synthetic import build_incident

WINDOW = 60_000 * 1_000_000


# --------------------------------------------------------------------------
# Planner units
# --------------------------------------------------------------------------

SAMPLE = textwrap.dedent(
    '''
    def apply_webhook(db, event):
        with db.session() as session:
            order = session.query(Order).filter_by(id=event.order_id).one()
            order.status = transition(order.status, event)
            order.last_event_id = event.id
            session.commit()
            return order
    '''
)


def _planner(tmp_path: Path, source: str, name: str = "apply_webhook") -> RepairPlanner:
    path = tmp_path / "sample.py"
    path.write_text(source, encoding="utf-8")
    return RepairPlanner(path, source)


def test_planner_adds_session_isolation(tmp_path):
    plan = _planner(tmp_path, SAMPLE).plan("apply_webhook")
    assert "session_isolation" in [e.strategy for e in plan.edits]
    patched = _apply_to_source(SAMPLE, plan.edits)
    assert "with db.session(for_update=True) as session:" in patched


def test_planner_adds_row_lock(tmp_path):
    plan = _planner(tmp_path, SAMPLE).plan("apply_webhook")
    assert "row_lock" in [e.strategy for e in plan.edits]
    patched = _apply_to_source(SAMPLE, plan.edits)
    assert ".with_for_update().one()" in patched


def test_planner_adds_idempotency_guard(tmp_path):
    plan = _planner(tmp_path, SAMPLE).plan("apply_webhook")
    assert "idempotency_guard" in [e.strategy for e in plan.edits]
    patched = _apply_to_source(SAMPLE, plan.edits)
    assert "if order.last_event_id == event.id:" in patched
    # The guard must be indented as a sibling of the statement after the load.
    guard_line = next(
        ln for ln in patched.splitlines() if "if order.last_event_id" in ln
    )
    load_line = next(ln for ln in patched.splitlines() if "order = session.query" in ln)
    assert len(guard_line) - len(guard_line.lstrip()) == len(load_line) - len(load_line.lstrip())


def test_planner_output_always_parses(tmp_path):
    plan = _planner(tmp_path, SAMPLE).plan("apply_webhook")
    patched = _apply_to_source(SAMPLE, plan.edits)
    ast.parse(patched)          # must not raise
    compile(patched, "sample.py", "exec")


def test_planner_is_idempotent(tmp_path):
    """Running the repair twice must not double-apply."""
    path = tmp_path / "sample.py"
    path.write_text(SAMPLE, encoding="utf-8")

    first = RepairPlanner(path, SAMPLE).plan("apply_webhook")
    patched_once = _apply_to_source(SAMPLE, first.edits)
    ast.parse(patched_once)

    second = RepairPlanner(path, patched_once).plan("apply_webhook")
    assert second.edits == [], "a repaired function must need no further edits"
    assert _apply_to_source(patched_once, second.edits) == patched_once


def test_planner_leaves_unrelated_functions_alone(tmp_path):
    source = SAMPLE + textwrap.dedent(
        '''
        def read_order(db, order_id):
            with db.session() as session:
                return session.query(Order).filter_by(id=order_id).one()
        '''
    )
    plan = _planner(tmp_path, source).plan("apply_webhook")
    patched = _apply_to_source(source, plan.edits)
    read_fn = patched.split("def read_order")[1]
    assert "db.session()" in read_fn, "edits must not leak into other functions"


def test_planner_reports_unknown_function(tmp_path):
    planner = _planner(tmp_path, SAMPLE)
    with pytest.raises(LookupError, match="not found"):
        planner.plan("does_not_exist")


def test_planner_ignores_a_function_with_no_mutations(tmp_path):
    source = "def noop(db):\n    return db.session()\n"
    plan = _planner(tmp_path, source).plan("noop")
    assert plan.edits == []
    assert "nothing to repair" in plan.notes


def test_every_edit_carries_a_rationale(tmp_path):
    plan = _planner(tmp_path, SAMPLE).plan("apply_webhook")
    for edit in plan.edits:
        assert edit.rationale.strip(), f"{edit.strategy} has no rationale"
        assert edit.before.strip() in SAMPLE or edit.is_insert


# --------------------------------------------------------------------------
# Locator
# --------------------------------------------------------------------------


def test_locator_returns_every_unlocked_writer(any_store):
    incident = build_incident(any_store)
    found = TriageEngine(any_store).triage(incident.crash_span_id)
    targets = UpstreamLocator(any_store).unlocked_mutators(found)

    names = sorted(name for _path, name in targets)
    assert names == ["apply_webhook", "expire_cart"]


def test_locator_skips_writers_that_already_lock(any_store):
    incident = build_incident(any_store, webhook_locks=True)
    found = TriageEngine(any_store).triage(incident.crash_span_id)
    names = [name for _p, name in UpstreamLocator(any_store).unlocked_mutators(found)]
    assert "apply_webhook" not in names


def test_repair_refuses_when_no_writer_is_unlocked(tmp_path):
    """If every writer already locks there is no lost update to repair."""
    from trace_repro.models import Incident, Interference

    incident = Incident(
        incident_id="x", crash_trace_id="t", crash_span_id="s",
        crash_function="f", crash_file="f.py", crash_endpoint="/",
        entity_key="orders:1", entity_type="orders", primary_key="1",
        interferences=[
            Interference(
                entity_key="orders:1", entity_type="orders", primary_key="1",
                other_trace_id="t", other_span_id="s", other_endpoint="/",
                other_payload="{}", mutator_function="locked", mutator_file="a.py",
                crash_function="f", crash_file="b.py", write_ts_ns=1, read_ts_ns=2,
                lock_held=True, hazard="RAW",
            )
        ],
    )
    from trace_repro.graph.memory import MemoryStore

    with pytest.raises(RepairError, match="already held the row lock"):
        plan_incident(MemoryStore(), incident)


# --------------------------------------------------------------------------
# Applied against a real repository
# --------------------------------------------------------------------------


@pytest.fixture()
def demo_incident(any_store, scratch_repo):
    """A triaged incident whose spans point at a real, writable repository."""
    pkg = scratch_repo / "checkout_service"
    build_incident(any_store, files={
        "apply_webhook": str(pkg / "webhooks.py"),
        "expire_cart": str(pkg / "expiry.py"),
        "capture_payment": str(pkg / "checkout.py"),
    })
    return any_store, TriageEngine(any_store).triage("span_C")


def test_apply_patches_every_contending_writer(demo_incident):
    store, incident = demo_incident
    result = apply_incident(store, incident, dry_run=True)
    functions = sorted(p.function_name for p in result.plans)
    assert functions == ["apply_webhook", "expire_cart"]
    assert result.files_changed == []
    assert "with db.session(for_update=True)" in result.diff


def test_apply_writes_and_revert_restores(demo_incident, scratch_repo):
    store, incident = demo_incident
    webhooks = scratch_repo / "checkout_service" / "webhooks.py"
    before = webhooks.read_text(encoding="utf-8")

    result = apply_incident(store, incident, dry_run=False)
    assert result.applied
    assert len(result.files_changed) == 2

    after = webhooks.read_text(encoding="utf-8")
    assert after != before
    assert "with db.session(for_update=True) as session:" in after
    ast.parse(after)
    assert (webhooks.parent / "webhooks.py.orig").exists()

    revert(result)
    assert webhooks.read_text(encoding="utf-8") == before
    # Revert cleans up after itself: no stray backups left in the repository.
    assert not list(scratch_repo.rglob("*.orig"))


def test_apply_refuses_a_missing_file(demo_incident, scratch_repo):
    store, incident = demo_incident
    for interference in incident.interferences:
        interference.mutator_file = str(scratch_repo / "checkout_service" / "gone.py")
    with pytest.raises(RepairError, match="missing file"):
        apply_incident(store, incident, dry_run=True)


def test_dry_run_never_touches_disk(demo_incident, scratch_repo):
    store, incident = demo_incident
    webhooks = scratch_repo / "checkout_service" / "webhooks.py"
    before = webhooks.read_text(encoding="utf-8")

    apply_incident(store, incident, dry_run=True)
    assert webhooks.read_text(encoding="utf-8") == before
    assert not list(scratch_repo.rglob("*.orig"))


@pytest.mark.parametrize(
    "scenario,module,function,strategies",
    [
        ("checkout", "webhooks.py", "apply_webhook",
         {"session_isolation", "row_lock", "idempotency_guard"}),
        ("checkout", "expiry.py", "expire_cart", {"session_isolation", "row_lock"}),
        ("inventory", "receipts.py", "receive_stock",
         {"session_isolation", "row_lock", "idempotency_guard"}),
        ("inventory", "expiry.py", "expire_lot", {"session_isolation", "row_lock"}),
        ("payments", "webhooks.py", "handle_payment_event",
         {"session_isolation", "row_lock", "idempotency_guard"}),
    ],
)
def test_repair_targets_the_real_demo_functions(scenario, module, function, strategies):
    """The planner must understand every service it ships against."""
    from .conftest import DEMO

    planner = RepairPlanner(str(DEMO / scenario / f"{scenario}_service" / module))
    plan = planner.plan(function)
    assert {e.strategy for e in plan.edits} == strategies


@pytest.mark.parametrize(
    "scenario,modules",
    [
        ("checkout", ("webhooks.py", "expiry.py", "checkout.py")),
        ("inventory", ("receipts.py", "expiry.py", "reservations.py")),
        ("payments", ("webhooks.py", "reconciliation.py")),
    ],
)
def test_scenario_repo_is_not_left_patched(scenario, modules):
    """Guard against a failing test silently shipping a repaired scenario."""
    from .conftest import DEMO

    repo = DEMO / scenario
    for name in modules:
        source = (repo / f"{scenario}_service" / name).read_text(encoding="utf-8")
        assert "db.session(for_update=True)" not in source, (
            f"{scenario}/{name} is patched; run the demo so it reverts, "
            "or restore from .orig"
        )
    assert not list(repo.rglob("*.orig")), f"stale .orig backups in demo/{scenario}"


@pytest.mark.parametrize("scenario", ["checkout", "inventory", "payments"])
def test_scenario_contract_assertions_are_valid_python(scenario):
    """What a repository asserts in its contract must actually parse.

    The generated test renders these verbatim, so a typo here would only
    surface at replay time, on someone else's machine.
    """
    from trace_repro.contract import load_contract

    from .conftest import DEMO

    spec = load_contract(DEMO / scenario)
    assert spec.assertions, f"demo/{scenario} asserts nothing"
    for statement in (*spec.assertions, *spec.duplicate_assertions):
        compile(statement, f"{scenario}/incident.json", "exec")


def test_shutil_copytree_is_available():
    assert hasattr(shutil, "copytree")
