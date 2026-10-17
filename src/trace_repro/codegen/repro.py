"""Compile a causal subgraph into a deterministic failing test.

The generator emits plain ``pytest`` with no plugin dependency, because the
artefact has to survive being committed to somebody else's repository. The only
things it carries across from the graph are facts, not assumptions:

* which participants ran, and in what kind (``webhook`` / ``worker`` / ``request``)
* the payloads they ran with
* the **measured millisecond offsets** between them
* the corrupt value the crash observed, so the test can assert it never happens
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..contract import IncidentSpec, Participant, spec_for
from ..models import Incident

HARNESS_MODULE = "tests.repro.harness"
ADAPTER_MODULE = "tests.repro.adapters"

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_PY_STEP_VAR = "CAUSAL_STEPS"
_DUP_STEP_VAR = "DUPLICATE_DELIVERY_STEPS"

#: Applied when a repository publishes no ``incident.json``. A generated test
#: still has to be valid, collectable Python for a target that has not adopted
#: the contract yet, so it asserts what it can -- that the contended row still
#: exists -- and says out loud that the rest is unknown.
_ROW_STILL_THERE = (
    'assert field(db, ENTITY.split(":")[0], PRIMARY_KEY, "status") is not None, (',
    '    "the contended row must survive the replay; publish an incident.json to "',
    '    "assert what correct means for this domain"',
    ")",
)
DEFAULT_ASSERTIONS: tuple[str, ...] = _ROW_STILL_THERE
DEFAULT_DUPLICATE_ASSERTIONS: tuple[str, ...] = _ROW_STILL_THERE


@dataclass(slots=True)
class CompiledTest:
    path: Path
    source: str
    participants: int
    incident_id: str
    entity_key: str
    notes: list[str] = field(default_factory=list)

    @property
    def test_name(self) -> str:
        return f"test_{self.incident_id}_cross_trace_race"

    @property
    def duplicate_test_name(self) -> str:
        return f"test_{self.incident_id}_duplicate_delivery_is_idempotent"


@dataclass(slots=True)
class TargetProfile:
    """How this repository is reached from the outside.

    Everything service-specific comes from the repository's ``incident.json``
    contract. Without one the profile still renders a valid test -- it just
    cannot assert anything about the domain, because nothing told it what the
    domain looks like.
    """

    repo_root: Path
    order_id: int = 8812
    entity_type: str = "orders"
    corrupt_value: str = ""
    expected_value: str = ""
    assertions: tuple[str, ...] = DEFAULT_ASSERTIONS
    duplicate_steps: tuple[Participant, ...] = ()
    duplicate_assertions: tuple[str, ...] = DEFAULT_DUPLICATE_ASSERTIONS
    spec: IncidentSpec | None = None

    @classmethod
    def for_repo(cls, repo_root: str | Path, incident: Incident | None = None,
                 order_id: int | None = None) -> TargetProfile:
        root = Path(repo_root).resolve()
        profile = cls(repo_root=root)
        spec = spec_for(root)
        if spec is not None:
            profile.spec = spec
            profile.entity_type = spec.entity_type
            profile.order_id = int(spec.primary_key)
            profile.assertions = spec.assertions or DEFAULT_ASSERTIONS
            profile.duplicate_assertions = (
                spec.duplicate_assertions or DEFAULT_DUPLICATE_ASSERTIONS
            )
            profile.duplicate_steps = spec.duplicate_participants
        if incident is not None:
            profile.entity_type = incident.entity_type
            profile.order_id = int(incident.primary_key)
            corrupt = _first_corrupt_value(incident)
            if corrupt:
                profile.corrupt_value = corrupt
            expected = _expected_value(incident)
            if expected:
                profile.expected_value = expected
        elif order_id is not None:
            profile.order_id = order_id
        if not profile.duplicate_steps:
            profile.duplicate_steps = _default_duplicate_steps(incident)
        return profile


def _default_duplicate_steps(incident: Incident | None) -> tuple[Participant, ...]:
    """Re-deliver the first participant, one millisecond later.

    That is the at-least-once case every queueing system eventually meets, and
    it is the cheapest possible probe of whether the handler is idempotent.
    """
    if incident is None or not incident.replay_steps:
        return ()
    step = incident.replay_steps[0]
    payload = _payload_dict(step.payload_json)
    first = Participant(kind=step.kind, endpoint=step.endpoint, payload=payload,
                        offset_ms=0.0)
    second = Participant(kind=step.kind, endpoint=step.endpoint, payload=dict(payload),
                         offset_ms=1.0)
    return (first, second)


def _first_corrupt_value(incident: Incident) -> str:
    """The value the losing write left behind -- the thing that must not persist.

    Three ways a write can be the losing one, in the order they are evidence:

    1. a scheduled worker's stale snapshot is what lands last;
    2. the same ``field -> value`` transition applied **twice** -- the signature
       of a handler that ran an event it had already applied;
    3. failing both, simply the most recent write on the contended row.
    """
    expected = _expected_value(incident)
    steps = [s for s in incident.causal_steps if s.new_val]

    for step in steps:
        if step.kind == "worker" and step.new_val != expected:
            return step.new_val

    applied: dict[tuple[str, str], int] = {}
    for step in steps:
        applied[(step.field, step.new_val)] = applied.get((step.field, step.new_val), 0) + 1
    for step in steps:
        if applied[(step.field, step.new_val)] > 1 and step.new_val != expected:
            return step.new_val

    for step in reversed(steps):
        if step.new_val != expected:
            return step.new_val
    return ""


def _expected_value(incident: Incident) -> str:
    for step in incident.causal_steps:
        if step.kind == "webhook" and step.new_val:
            return step.new_val
    return ""


# --------------------------------------------------------------------------
# Emission
# --------------------------------------------------------------------------


def _py_repr(value: object) -> str:
    return repr(value)


def _payload_dict(payload_json: str) -> dict:
    try:
        data = json.loads(payload_json or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {"value": data}


def _step_literal(step) -> str:
    """One ``Step(...)`` literal, from either a graph step or a contract one.

    A graph step carries ``payload_json`` (it came out of the store); a contract
    participant carries a plain ``payload`` dict.
    """
    payload = getattr(step, "payload", None)
    if not isinstance(payload, dict):
        payload = _payload_dict(getattr(step, "payload_json", ""))
    return (
        "    Step(\n"
        f"        name={_py_repr(step.endpoint)},\n"
        f"        kind={_py_repr(step.kind)},\n"
        f"        endpoint={_py_repr(step.endpoint)},\n"
        f"        payload={_py_repr(payload)},\n"
        f"        offset_ms={step.offset_ms!r},\n"
        f"        function={_py_repr(getattr(step, 'function_name', '') or '')},\n"
        f"        field_name={_py_repr(getattr(step, 'field', '') or '')},\n"
        f"        old_val={_py_repr(getattr(step, 'old_val', '') or '')},\n"
        f"        new_val={_py_repr(getattr(step, 'new_val', '') or '')},\n"
        f"        lock_held={getattr(step, 'lock_held', False)!r},\n"
        "    ),"
    )


def _steps_literal(incident: Incident) -> str:
    return "\n".join(_step_literal(step) for step in incident.replay_steps)


def _duplicate_steps_literal(participants: tuple[Participant, ...]) -> str:
    return "\n".join(_step_literal(participant) for participant in participants)


def _indent_block(statements: tuple[str, ...], indent: str = "    ") -> str:
    """Render repository-supplied assertions as a function body.

    The assertions are Python written by whoever owns the repository, so they
    are rendered verbatim at a fixed indent -- and the result is re-parsed with
    the rest of the file before anything is written to disk.
    """
    return "\n".join(indent + line for line in statements) if statements else ""


def _timeline_comment(incident: Incident) -> str:
    lines = [
        "# Minimal causal subgraph (shortestPath over HAPPENED_BEFORE | CAUSED_READ).",
        "# Offsets are request start times measured from the production trace.",
        "#",
        "# Replay participants:",
    ]
    for step in incident.replay_steps:
        lines.append(
            f"#   +{step.offset_ms:>8.1f}ms  {step.kind:<8} {step.endpoint:<26} "
            f"{step.function_name or '-':<16} lock_held={step.lock_held}"
        )
    lines.append("#")
    lines.append("# State transitions on the contended row:")
    for i, step in enumerate(incident.causal_steps):
        lines.append(
            f"#   at {i:>2}  {step.field}: {step.old_val or '-'} -> {step.new_val or '-'}  "
            f"by {step.function_name or '-'}  (lock_held={step.lock_held})"
        )
    lines.append("#")
    lines.append("# Cross-trace interference found by the collision query:")
    for interference in incident.interferences:
        lines.append(
            f"#   trace {interference.other_trace_id} "
            f"{interference.other_endpoint} wrote it "
            f"{interference.race_window_ms:.1f}ms before the crash  "
            f"hazard={interference.hazard}  lock_held={interference.lock_held}"
        )
    return "\n".join(lines)


TEMPLATE = (_TEMPLATE_DIR / "test_incident.py.tmpl").read_text(encoding="utf-8")


def compile_test(incident: Incident, profile: TargetProfile,
                 out_dir: str | Path | None = None) -> CompiledTest:
    """Render the reproduction test and return it without writing anything."""
    out_dir = Path(out_dir) if out_dir else profile.repo_root / "tests" / "repro"
    filename = f"test_incident_{incident.primary_key}.py"
    source = _render(incident, profile)
    # The assertions come from the target repository, so the one guarantee
    # trace-repro can make about them is that the artefact still parses.
    try:
        ast.parse(source, filename=filename)
    except SyntaxError as exc:
        raise SyntaxError(
            f"the rendered reproduction test is not valid Python ({exc}); "
            "check the assertions in incident.json"
        ) from exc
    return CompiledTest(
        path=out_dir / filename,
        source=source,
        participants=len(incident.replay_steps),
        incident_id=incident.incident_id,
        entity_key=incident.entity_key,
    )


def _rel(path: str, root: Path) -> str:
    """Repo-relative POSIX path.

    Absolute paths leak the build machine's layout into a committed artefact and
    break the generated docstring on Windows (``D:\\c...`` is an escape).
    """
    if not path:
        return "unknown"
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except ValueError:
        return Path(path).name


def _render(incident: Incident, profile: TargetProfile) -> str:
    root = profile.repo_root
    source = TEMPLATE.format(
        incident_id=incident.incident_id,
        entity_key=incident.entity_key,
        crash_function=incident.crash_function or "unknown",
        crash_file=_rel(incident.crash_file, root),
        upstream=incident.upstream_mutator or "unknown",
        harness=HARNESS_MODULE,
        adapter=ADAPTER_MODULE,
        order_id=profile.order_id,
        parties=max(1, len(incident.replay_steps)),
        steps_var=_PY_STEP_VAR,
        steps=_steps_literal(incident),
        timeline=_timeline_comment(incident),
        corrupt=_py_repr(profile.corrupt_value),
        expected=_py_repr(profile.expected_value),
        assertions=_indent_block(profile.assertions),
        dup_steps_var=_DUP_STEP_VAR,
        dup_steps=_duplicate_steps_literal(profile.duplicate_steps),
        dup_assertions=_indent_block(profile.duplicate_assertions),
    )
    # The generated file lands in someone else's repository and will be linted
    # there, so it has to be clean here.
    return source if source.endswith("\n") else source + "\n"


def write_test(incident: Incident, profile: TargetProfile,
               out_dir: str | Path | None = None) -> CompiledTest:
    """Compile and write the reproduction test, creating the harness if needed."""
    compiled = compile_test(incident, profile, out_dir)
    compiled.path.parent.mkdir(parents=True, exist_ok=True)
    compiled.path.write_text(compiled.source, encoding="utf-8")
    return compiled


def install_harness(profile: TargetProfile, force: bool = False) -> list[Path]:
    """Copy the replay harness + adapter into the target repository.

    The harness ships as package data so the target repo needs nothing from
    trace-repro at test time.
    """
    from .assets import harness_files

    installed: list[Path] = []
    for rel, content in harness_files().items():
        target = profile.repo_root / rel
        if target.exists() and not force:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        installed.append(target)
    repro_dir = profile.repo_root / "tests" / "repro"
    repro_dir.mkdir(parents=True, exist_ok=True)
    init = repro_dir / "__init__.py"
    if not init.exists():
        init.write_text(
            '"""Reproduction tests generated by trace-repro."""\n', encoding="utf-8"
        )
    return installed
