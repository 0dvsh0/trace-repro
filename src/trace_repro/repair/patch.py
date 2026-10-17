"""Locate the upstream mutator and apply the plan.

The locator answers "which real function caused this?" by following the graph
edges the incident already established -- it does not search the repo. If the
graph says the crash was caused by a write emitted by ``apply_webhook``, that is
the function we open. Everything else is verification.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

from ..analysis.triage import TriageEngine
from ..graph.store import GraphStore
from ..models import Incident
from .plan import FunctionPlan, RepairPlanner, _apply_to_source


class RepairError(RuntimeError):
    pass


@dataclass(slots=True)
class RepairResult:
    incident_id: str
    plans: list[FunctionPlan] = field(default_factory=list)
    diff: str = ""
    files_changed: list[str] = field(default_factory=list)
    applied: bool = False
    summary: str = ""

    @property
    def strategies(self) -> list[str]:
        out: list[str] = []
        for plan in self.plans:
            for edit in plan.edits:
                if edit.strategy not in out:
                    out.append(edit.strategy)
        return out


class UpstreamLocator:
    """Follows ``(:StateEntity)-[:MUTATED_BY_FUNCTION]->(:ASTFunction)``."""

    def __init__(self, store: GraphStore):
        self.store = store

    def unlocked_mutators(self, incident: Incident) -> list[tuple[str, str]]:
        """``[(file_path, function_name)]`` for every writer that ran unlocked.

        Every unlocked writer must be repaired. Locking only the one that
        happened to be blamed leaves the others free to clobber it.
        """
        seen: list[tuple[str, str]] = []
        for interference in incident.interferences:
            if interference.lock_held:
                continue
            if not interference.mutator_function or not interference.mutator_file:
                continue
            key = (interference.mutator_file, interference.mutator_function)
            if key not in seen:
                seen.append(key)
        return seen

    def verify(self, incident: Incident) -> list[str]:
        problems: list[str] = []
        if not incident.interferences:
            problems.append("incident has no cross-trace interferences")
        if any(i.lock_held for i in incident.interferences):
            problems.append("an interfering writer already held the row lock")
        return problems


def plan_incident(store: GraphStore, incident: Incident) -> list[FunctionPlan]:
    locator = UpstreamLocator(store)
    problems = locator.verify(incident)
    if problems:
        raise RepairError("; ".join(problems))
    plans = []
    for file_path, func_name in locator.unlocked_mutators(incident):
        path = Path(file_path)
        if not path.exists():
            raise RepairError(f"graph points at a missing file: {file_path}")
        planner = RepairPlanner(file_path)
        try:
            plans.append(planner.plan(func_name))
        except LookupError as exc:
            raise RepairError(str(exc)) from exc
    if not plans:
        raise RepairError("no unlocked upstream mutator found to repair")
    return plans


def render_diff(plans: list[FunctionPlan]) -> str:
    """Unified diff for every planned edit, grouped per file."""
    chunks: list[str] = []
    by_file: dict[str, list[FunctionPlan]] = {}
    for plan in plans:
        by_file.setdefault(plan.file_path, []).append(plan)

    for file_path, file_plans in by_file.items():
        original = Path(file_path).read_text("utf-8")
        patched = _apply_to_source(original, [e for p in file_plans for e in p.edits])
        name = Path(file_path).name
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
            n=3,
        )
        chunks.append("".join(diff))
    return "".join(chunks)


def apply_incident(store: GraphStore, incident: Incident, dry_run: bool = False) -> RepairResult:
    """Plan, verify and (optionally) write the repair to disk."""
    plans = plan_incident(store, incident)
    result = RepairResult(incident_id=incident.incident_id, plans=plans)
    result.diff = render_diff(plans)

    # Prove the patched result is still valid Python before touching disk.
    for plan in plans:
        original = Path(plan.file_path).read_text("utf-8")
        patched = _apply_to_source(original, plan.edits)
        compile(patched, plan.file_path, "exec")

    if dry_run:
        result.summary = f"planned {len(plans)} function(s), {result.strategies}"
        return result

    for plan in plans:
        path = Path(plan.file_path)
        original = path.read_text("utf-8")
        patched = _apply_to_source(original, plan.edits)
        if patched == original:
            continue
        backup = path.with_suffix(path.suffix + ".orig")
        if not backup.exists():
            backup.write_text(original, encoding="utf-8")
        path.write_text(patched, encoding="utf-8")
        if str(path) not in result.files_changed:
            result.files_changed.append(str(path))

    result.applied = bool(result.files_changed)
    result.summary = (
        f"repaired {len(result.files_changed)} file(s) with "
        f"{', '.join(result.strategies) or 'no'} strategy"
    )
    return result


def revert(result: RepairResult, keep_backup: bool = False) -> list[str]:
    """Undo an applied repair and clean up after itself.

    The ``.orig`` backups are removed unless ``keep_backup`` is set: leaving
    them behind turns a temporary safety net into repo litter that gets
    committed and confused with real source.
    """
    restored: list[str] = []
    for file_path in result.files_changed:
        target = Path(file_path)
        backup = target.with_suffix(target.suffix + ".orig")
        if backup.exists():
            target.write_text(backup.read_text("utf-8"), encoding="utf-8")
            if not keep_backup:
                backup.unlink()
        restored.append(str(target))
    return restored


def verify_fix(store: GraphStore, incident: Incident, engine: TriageEngine) -> dict:
    """Re-read the graph after the repair and report the before/after diff."""
    collisions = store.find_collisions(incident.crash_span_id, engine.window_ns)
    return {
        "writers": len(collisions),
        "all_locked": all(c.lock_held for c in collisions),
        "unlocked": [c.mutator_function for c in collisions if not c.lock_held],
    }
