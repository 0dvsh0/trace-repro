"""The whole loop, against the real services and real races.

This is the test that would catch a regression in the product claim. It runs
the actual concurrent workload, the actual pytest subprocesses, and the actual
source patch -- against a throwaway copy of each target repository.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from trace_repro.analysis.triage import TriageEngine
from trace_repro.codegen.repro import TargetProfile
from trace_repro.contract import load_contract
from trace_repro.pipeline.heal import HealPipeline
from trace_repro.pipeline.workload import reproduce

from .conftest import SCENARIOS

pytestmark = pytest.mark.slow


@pytest.fixture(params=SCENARIOS)
def repo(request, tmp_path: Path) -> Path:
    """A disposable copy of one scenario repository.

    The heal loop rewrites files on disk. Running it against a real demo tree
    means a failing assertion leaves the repository patched.
    """
    source = Path(__file__).resolve().parents[1] / "demo" / request.param
    target = tmp_path / request.param
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(
        "*.orig", "__pycache__", "test_incident_*.py",
    ))
    return target


@pytest.fixture()
def store(memory_store):
    return memory_store


@pytest.fixture()
def incident(store, repo):
    spec = load_contract(repo)
    outcome = reproduce(store, repo_root=str(repo), background_traces=40)
    assert outcome.crashed, f"the {spec.key} workload stopped reproducing the race"
    found = TriageEngine(store).triage(
        outcome.crash_span_id,
        incident_id=spec.incident_id,
        error_type=outcome.error_type,
        error_message=outcome.error_message,
    )
    return spec, outcome, found


# --------------------------------------------------------------------------
# The incident really happens
# --------------------------------------------------------------------------


def test_workload_reproduces_the_production_failure(incident):
    spec, outcome, _ = incident
    assert outcome.http_status >= 400, "a lost update can be a 4xx, not only a 5xx"
    assert outcome.error_type
    assert outcome.final_status


def test_the_participants_are_disjoint_traces(incident):
    spec, _outcome, found = incident
    traces = {c.other_trace_id for c in found.interferences} | {found.crash_trace_id}
    assert len(traces) == len(spec.participants), (
        "every participant must be an independent trace"
    )


def test_every_writer_ran_without_a_lock(incident):
    spec, _outcome, found = incident
    assert len(found.interferences) == len(spec.participants) - 1
    assert all(not c.lock_held for c in found.interferences)
    assert all(c.hazard == "WAW" for c in found.interferences)


def test_stack_trace_alone_misses_the_culprits(incident):
    """The failing frame names one function; the culprits are somewhere else."""
    spec, _outcome, found = incident
    culprits = {c.mutator_function for c in found.interferences}
    assert culprits, "the collision query found no upstream writer"
    assert found.crash_function not in culprits, (
        f"{found.crash_function} is the victim, not the cause"
    )


def test_replay_offsets_reproduce_the_workload(incident):
    spec, _outcome, found = incident
    offsets = {s.trace_id: s.offset_ms for s in found.replay_steps}
    assert len(offsets) == len(spec.participants)
    assert min(offsets.values()) == pytest.approx(0.0, abs=0.5)
    assert max(offsets.values()) > 50.0


def test_background_traffic_is_pruned_away(incident):
    """The graph has to be able to throw away everything that did not matter."""
    _spec, _outcome, found = incident
    assert found.spans_pruned == 40, "the background traces were not discarded"
    assert found.pruning_ratio > 0.9
    assert len(found.replay_steps) == 3


@pytest.mark.parametrize(
    "scenario,slow_module,slow_constant,reader_module,reader_constant",
    [
        ("checkout", "checkout_service.webhooks", "GATEWAY_LATENCY_MS",
         "checkout_service.expiry", "REHYDRATE_MS"),
        ("inventory", "inventory_service.receipts", "SUPPLIER_API_MS",
         "inventory_service.expiry", "REHYDRATE_MS"),
        ("payments", "payments_service.webhooks", "ACQUIRER_MS",
         "payments_service.webhooks", "ACQUIRER_MS"),
    ],
)
def test_interleaving_has_slack_on_both_sides(
    scenario, slow_module, slow_constant, reader_module, reader_constant
):
    """The race must be robust, not lucky.

    For the losing writer to lose on purpose it has to read before the first
    writer commits *and* commit after it. A few milliseconds of slack would make
    the whole product look flaky on a loaded CI runner.
    """
    import importlib

    from .conftest import DEMO

    spec = load_contract(DEMO / scenario)
    offsets = list(spec.offsets_ms)
    first, second, _reader = offsets[:3]

    slow = getattr(importlib.import_module(slow_module), slow_constant)
    reader_ms = getattr(importlib.import_module(reader_module), reader_constant)
    # The payments scenario retries the *same* handler, so both calls take the
    # acquirer round trip.
    reader = reader_ms if scenario == "payments" else reader_ms

    first_commit = first + slow
    second_commit = second + reader
    last_writer = max(first_commit, second_commit)

    read_slack = first_commit - second
    commit_slack = second_commit - first_commit
    reader_slack = offsets[2] - last_writer

    assert read_slack >= 40, f"only {read_slack} ms before the first writer commits"
    assert commit_slack >= 40, f"only {commit_slack} ms after the first writer commits"
    assert reader_slack >= 100, f"only {reader_slack} ms before the reader runs"
    assert first == 0.0


# --------------------------------------------------------------------------
# Red -> Green
# --------------------------------------------------------------------------


@pytest.fixture()
def healed(store, repo, incident):
    _spec, _outcome, found = incident
    pipeline = HealPipeline(store, repo, engine=TriageEngine(store), keep_fix=False)
    result = pipeline.run(found)
    return pipeline, result


def test_red_phase_reproduces_the_failure(incident, healed):
    spec, _outcome, _found = incident
    _pipeline, result = healed
    assert result.red is not None
    assert result.red.exit_code != 0, "the compiled test must fail before the fix"
    assert f"test_{spec.incident_id}_cross_trace_race" in result.red.stdout


def test_red_phase_fails_with_the_production_error(incident, healed):
    spec, outcome, _found = incident
    _pipeline, result = healed
    assert outcome.error_type in result.red.stdout, (
        "the reproduction must fail with the error production saw, "
        "not with an unrelated one"
    )
    assert spec.incident_id in result.red.stdout


def test_repair_touches_every_contending_writer(incident, healed):
    _spec, _outcome, found = incident
    _pipeline, result = healed
    assert result.repair is not None
    expected = sorted(
        {c.mutator_function for c in found.interferences if not c.lock_held}
    )
    assert sorted(p.function_name for p in result.repair.plans) == expected
    # A lost update is only fixed once every contending writer takes the lock.
    assert "session_isolation" in result.repair.strategies
    assert "row_lock" in result.repair.strategies


def test_green_phase_passes(healed):
    _pipeline, result = healed
    assert result.green is not None
    assert result.green.exit_code == 0, result.green.stdout + result.green.stderr
    assert "2 passed" in result.green.summary


def test_pipeline_reports_success(healed):
    _pipeline, result = healed
    assert result.reproduced is True
    assert result.healed is True
    assert result.success is True


def test_pipeline_always_restores_the_repository(healed, repo):
    _pipeline, result = healed
    assert result.reverted, "the patch must be reverted by default"
    assert not list(repo.rglob("*.orig")), "revert must clean up after itself"
    for path in result.repair.files_changed:
        source = Path(path).read_text(encoding="utf-8")
        assert "db.session(for_update=True)" not in source.split('"""')[-1], (
            f"{path} was left patched"
        )
        compile(source, path, "exec")


def test_interrupted_run_is_recovered(scratch_repo, capsys):
    """A run killed outright leaves .orig files; the next run must restore."""
    from trace_repro.cli import Console
    from trace_repro.demo import recover_interrupted_run

    webhooks = scratch_repo / "checkout_service" / "webhooks.py"
    original = webhooks.read_text(encoding="utf-8")
    patched = original.replace(
        "with db.session() as session:",
        "with db.session(for_update=True) as session:",
    )
    assert patched != original
    webhooks.write_text(patched, encoding="utf-8")
    Path(str(webhooks) + ".orig").write_text(original, encoding="utf-8")

    console = Console(colour=False)
    recovered = recover_interrupted_run(scratch_repo, console)

    assert recovered == [webhooks]
    assert webhooks.read_text(encoding="utf-8") == original
    assert not list(scratch_repo.rglob("*.orig"))
    assert "recovered an interrupted run" in capsys.readouterr().out


def test_recovery_is_a_no_op_on_a_clean_repository(scratch_repo):
    from trace_repro.cli import Console
    from trace_repro.demo import recover_interrupted_run

    assert recover_interrupted_run(scratch_repo, Console(colour=False)) == []


def test_keep_fix_leaves_the_repository_patched(store, repo, incident):
    from trace_repro.repair.patch import revert

    _spec, _outcome, found = incident
    pipeline = HealPipeline(store, repo, engine=TriageEngine(store), keep_fix=True)
    result = pipeline.run(found)
    try:
        assert result.green.exit_code == 0
        for plan in result.repair.plans:
            source = Path(plan.file_path).read_text(encoding="utf-8")
            assert "db.session(for_update=True)" in source
        assert result.reverted == []
    finally:
        revert(result.repair)


def test_skip_repair_stays_red(store, repo, incident):
    _spec, _outcome, found = incident
    pipeline = HealPipeline(store, repo, engine=TriageEngine(store))
    result = pipeline.run(found, skip_repair=True)
    assert result.red.exit_code != 0
    assert result.green is None
    assert result.healed is False


# --------------------------------------------------------------------------
# Governance
# --------------------------------------------------------------------------


def test_graph_diff_proves_the_locks_landed(store, repo, incident):
    """Re-run the workload with the patch applied and re-read the graph."""
    from trace_repro.repair.patch import revert

    spec, _outcome, found = incident
    engine = TriageEngine(store)
    pipeline = HealPipeline(store, repo, engine=engine, keep_fix=True)
    result = pipeline.run(found)
    assert result.repair is not None and result.repair.applied

    try:
        fixed_store = type(store)()
        after = reproduce(fixed_store, repo_root=str(repo))
        assert after.http_status == 200, "the repaired workload must not fail"
        assert after.final_status == "CAPTURED" or after.http_status == 200

        diff = pipeline.graph_diff(found, fixed_store)
        contended = sorted({c.mutator_function for c in found.interferences
                            if not c.lock_held})
        assert diff["race_condition_reproduced"] is True
        assert diff["resolved"] is True
        assert sorted(diff["contended_writers"]) == contended
        # A repaired writer either still writes and now holds the lock, or --
        # having re-read fresh state -- correctly declines to write at all. Both
        # are resolutions; what must not survive is an *unlocked* contender.
        assert sorted(diff["resolved_writers"]) == contended
        assert not (set(diff["remaining_unlocked"]) & set(contended)), (
            f"{diff['remaining_unlocked']} still write the contended row unlocked"
        )
        # Anything still unlocked is outside the race window: flagged, not failed.
        for function in diff["remaining_unlocked"]:
            assert function not in contended
    finally:
        revert(result.repair)


def test_repro_test_is_left_in_the_repository(store, repo, incident):
    """The regression must outlive the incident."""
    spec, _outcome, found = incident
    pipeline = HealPipeline(store, repo, engine=TriageEngine(store))
    compiled, installed = pipeline.compile_repro(found)

    assert compiled.path.exists()
    assert spec.primary_key in compiled.path.name
    assert compiled.path.parent == repo / "tests" / "repro"
    assert all(p.exists() for p in installed)

    source = compiled.path.read_text(encoding="utf-8")
    assert "Auto-generated by trace-repro" in source
    assert compiled.test_name in source
    assert compiled.duplicate_test_name in source


# --------------------------------------------------------------------------
# The compiled artefact stands alone
# --------------------------------------------------------------------------


def test_repro_test_runs_without_trace_repro_installed(repo, incident, tmp_path):
    """Run the generated test with ``trace_repro`` made unimportable.

    A shadowing module that raises on import is the only way to prove the
    isolation in an environment where trace-repro *is* installed.
    """
    import os
    import subprocess

    _spec, _outcome, found = incident
    profile = TargetProfile.for_repo(repo, found)
    from trace_repro.codegen.repro import install_harness, write_test

    install_harness(profile)
    compiled = write_test(found, profile)
    rel = compiled.path.relative_to(repo)

    poison = tmp_path / "poison"
    poison.mkdir(exist_ok=True)
    (poison / "trace_repro.py").write_text(
        'raise ImportError("trace-repro must not be importable from the target repo")\n',
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(poison), str(repo)])
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    probe = subprocess.run(
        [sys.executable, "-c", "import trace_repro"],
        cwd=str(poison), capture_output=True, text=True, env=env, timeout=60,
    )
    assert probe.returncode != 0, "the poison module must shadow any installed copy"

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(rel), "-v", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=str(repo), capture_output=True, text=True, timeout=180, env=env,
    )
    assert proc.returncode != 0, "the reproduction must fail on the unpatched service"
    assert "ImportError" not in proc.stderr
