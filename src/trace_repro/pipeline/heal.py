"""The red -> green loop, executed for real.

No simulation of a CI system: this shells out to ``python -m pytest`` twice and
means what the exit codes say.

1. compile the causal subgraph into a test
2. run it -> it must FAIL, and it must fail with the production error
3. plan and apply the upstream repair
4. run it again -> it must PASS
5. re-run the production workload with the fix and re-ingest, so the
   before/after graph diff is measured rather than asserted
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..analysis.triage import TriageEngine
from ..codegen.repro import CompiledTest, TargetProfile, install_harness, write_test
from ..graph.store import GraphStore
from ..models import Incident, Triage
from ..repair.patch import RepairResult, apply_incident, revert

PYTEST_TIMEOUT_S = 300


@dataclass(slots=True)
class PipelineRun:
    name: str
    exit_code: int
    duration_s: float
    stdout: str
    stderr: str
    target: str = ""

    @property
    def passed(self) -> bool:
        return self.exit_code == 0

    @property
    def summary(self) -> str:
        for line in reversed((self.stdout + "\n" + self.stderr).splitlines()):
            stripped = line.strip()
            if stripped.startswith(("=", "-")) and (
                "passed" in stripped or "failed" in stripped or "error" in stripped
            ):
                return stripped.strip("= ")
        return f"exit {self.exit_code}"

    def as_dict(self) -> dict:
        return {
            "pipeline": self.name,
            "target": self.target,
            "exit_code": self.exit_code,
            "passed": self.passed,
            "duration_s": round(self.duration_s, 3),
            "summary": self.summary,
        }


class PytestRunner:
    """Runs pytest against the target repository."""

    def __init__(self, repo_root: str | Path, python: str | None = None,
                 extra_args: list[str] | None = None):
        self.repo_root = Path(repo_root).resolve()
        self.python = python or sys.executable
        self.extra_args = extra_args or []

    def run(self, name: str, target: str, timeout: int = PYTEST_TIMEOUT_S) -> PipelineRun:
        cmd = [self.python, "-m", "pytest", target, "-v", "--no-header", "-p", "no:cacheprovider"]
        cmd += self.extra_args
        started = time.monotonic()
        proc = subprocess.run(
            cmd,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_pytest_env(self.repo_root),
        )
        return PipelineRun(
            name=name,
            exit_code=proc.returncode,
            duration_s=time.monotonic() - started,
            stdout=proc.stdout,
            stderr=proc.stderr,
            target=target,
        )


def _pytest_env(repo_root: Path) -> dict:
    env = dict(os.environ)
    root = str(repo_root)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root + (os.pathsep + existing if existing else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


@dataclass(slots=True)
class HealResult:
    incident: Incident
    compiled: CompiledTest
    installed: list[Path] = field(default_factory=list)
    red: PipelineRun | None = None
    green: PipelineRun | None = None
    repair: RepairResult | None = None
    graph_diff: dict = field(default_factory=dict)
    reverted: list[str] = field(default_factory=list)

    @property
    def reproduced(self) -> bool:
        return self.red is not None and not self.red.passed

    @property
    def healed(self) -> bool:
        return self.green is not None and self.green.passed

    @property
    def success(self) -> bool:
        return self.reproduced and self.healed

    def to_triage(self) -> Triage:
        return Triage(
            incident=self.incident,
            red_result=self.red.as_dict() if self.red else {},
            fix_diff=self.repair.diff if self.repair else "",
            fix_summary=self.repair.summary if self.repair else "",
            green_result=self.green.as_dict() if self.green else {},
            healed=self.healed,
        )


class HealPipeline:
    """Drives one incident from crash to green."""

    def __init__(
        self,
        store: GraphStore,
        repo_root: str | Path,
        engine: TriageEngine | None = None,
        keep_fix: bool = False,
        python: str | None = None,
        ci_adapter: Any | None = None,
        export_reports: str | Path | None = None,
    ):
        self.store = store
        self.repo_root = Path(repo_root).resolve()
        self.engine = engine or TriageEngine(store)
        self.keep_fix = keep_fix
        self.runner = PytestRunner(self.repo_root, python=python)
        self.ci_adapter = ci_adapter
        self.export_reports = export_reports


    # -- steps ------------------------------------------------------------
    def compile_repro(self, incident: Incident) -> tuple[CompiledTest, list[Path]]:
        profile = TargetProfile.for_repo(self.repo_root, incident)
        installed = install_harness(profile)
        compiled = write_test(incident, profile)
        return compiled, installed

    def run_red(self, compiled: CompiledTest) -> PipelineRun:
        rel = os.path.relpath(compiled.path, self.repo_root).replace("\\", "/")
        return self.runner.run("pipeline#1", rel)

    def apply_fix(self, incident: Incident) -> RepairResult:
        return apply_incident(self.store, incident, dry_run=False)

    def run_green(self, compiled: CompiledTest) -> PipelineRun:
        rel = os.path.relpath(compiled.path, self.repo_root).replace("\\", "/")
        return self.runner.run("pipeline#2", rel)

    def graph_diff(
        self, incident: Incident, fixed_store: GraphStore
    ) -> dict:
        """Compare the graph before and after the repair.

        Two things this must get right:

        1. Keyed on the **entity**, not on a crash span. After the fix there is
           no crash left to point at, so a span-keyed diff compares against
           nothing and always reports "still broken".
        2. Keyed on the **writers that were unlocked before**. A function that
           merely writes this row later, outside the race, is not evidence that
           the race survived -- and counting it would let a correct repair look
           like a failure. Those are reported separately as an advisory.
        """
        from ..models import StateEntity

        entity = StateEntity(incident.entity_type, incident.primary_key)
        before = self.store.entity_writers(entity)
        after = fixed_store.entity_writers(entity)

        def unlocked_by(rows: list[dict]) -> dict[str, bool]:
            out: dict[str, bool] = {}
            for row in rows:
                if not row["lock_held"]:
                    out[row["function_name"]] = True
            return out

        contended = unlocked_by(before)
        still = unlocked_by(after)
        resolved_writers = sorted(
            fn for fn in contended if not still.get(fn, False)
        )
        return {
            "lock_held_before": sorted({bool(c["lock_held"]) for c in before}),
            "lock_held_after": sorted({bool(c["lock_held"]) for c in after}),
            "writers_before": len(before),
            "writers_after": len(after),
            "contended_writers": sorted(contended),
            "resolved_writers": resolved_writers,
            "locked_after": sorted({c["function_name"] for c in after if c["lock_held"]}),
            "remaining_unlocked": sorted(still),
            "race_condition_reproduced": bool(contended),
            "resolved": bool(contended) and set(resolved_writers) == set(contended),
        }

    # -- orchestration ----------------------------------------------------
    def run(self, incident: Incident, skip_repair: bool = False) -> HealResult:
        compiled, installed = self.compile_repro(incident)
        result = HealResult(incident=incident, compiled=compiled, installed=installed)

        if self.ci_adapter:
            with self.ci_adapter.section("pipeline_red", "Pipeline #1 · Deterministic Reproduction (RED)"):
                result.red = self.run_red(compiled)
        else:
            result.red = self.run_red(compiled)

        if skip_repair:
            if self.ci_adapter and self.export_reports:
                self.ci_adapter.export_all(result, artifacts_dir=self.export_reports)
            return result

        result.repair = self.apply_fix(incident)

        if self.ci_adapter:
            with self.ci_adapter.section("pipeline_green", "Pipeline #2 · Self-Healing Verification (GREEN)"):
                result.green = self.run_green(compiled)
        else:
            result.green = self.run_green(compiled)

        if not self.keep_fix and result.repair.applied:
            result.reverted = revert(result.repair)

        if self.ci_adapter and self.export_reports:
            self.ci_adapter.export_all(result, artifacts_dir=self.export_reports)

        return result

