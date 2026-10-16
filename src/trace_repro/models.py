"""Plain dataclasses shared by every layer of trace-repro.

These are the wire format between the ingest side (telemetry), the graph side
(FalkorDB) and the action side (codegen / repair). Keeping them free of any
storage dependency is what lets the same analysis code run against a live
FalkorDB and against the in-process test double.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .schema import NS_PER_MS


def now_ns() -> int:
    """Monotonic-ish wall clock in nanoseconds."""
    return time.time_ns()


# --------------------------------------------------------------------------
# Telemetry side
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Trace:
    """One request's trace tree. Isolated from every other trace by design."""

    id: str
    entry_endpoint: str = ""
    service: str = ""
    payload_json: str = "{}"
    start_ns: int = field(default_factory=now_ns)
    end_ns: int = 0
    status: str = "OK"


@dataclass(slots=True)
class Span:
    """A unit of work inside a trace.

    ``function_name`` / ``file_path`` / ``lineno`` are captured from the live
    Python frame, not declared by hand -- that is what lets the graph point at
    the real mutator without a hardcoded allow-list of files.
    """

    id: str
    trace_id: str
    name: str
    start_ns: int = field(default_factory=now_ns)
    end_ns: int = 0
    status: str = "OK"
    function_name: str = ""
    file_path: str = ""
    lineno: int = 0
    parent_span_id: str = ""
    error_type: str = ""
    error_message: str = ""

    @property
    def duration_ms(self) -> float:
        return (self.end_ns - self.start_ns) / NS_PER_MS


@dataclass(slots=True)
class StateEntity:
    """The bridge node. Two traces become causally adjacent through one of these."""

    entity_type: str
    primary_key: str
    first_seen_ns: int = field(default_factory=now_ns)

    @property
    def key(self) -> str:
        return f"{self.entity_type.lower()}:{self.primary_key}"


@dataclass(slots=True)
class StateMutation:
    """A single field-level write to an entity, with the delta that was applied.

    ``lock_held`` is the whole ballgame: two consecutive mutations of the same
    row that both ran without a row lock are a lost update waiting to happen.
    """

    id: str
    trace_id: str
    span_id: str
    entity_type: str
    primary_key: str
    field: str
    old_val: str
    new_val: str
    ts_ns: int = field(default_factory=now_ns)
    seq_no: int = 0
    lock_held: bool = False
    function_name: str = ""


# --------------------------------------------------------------------------
# Code side
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ASTFunction:
    name: str
    file_path: str
    line_range: tuple[int, int]
    qualified_name: str = ""
    is_async: bool = False


@dataclass(slots=True)
class GitCommit:
    sha: str
    message: str = ""
    author: str = ""
    committed_at_ns: int = 0


# --------------------------------------------------------------------------
# Analysis side
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Interference:
    """One row of the cross-trace collision query output."""

    entity_key: str
    entity_type: str
    primary_key: str
    other_trace_id: str
    other_span_id: str
    other_endpoint: str
    other_payload: str
    mutator_function: str
    mutator_file: str
    crash_function: str
    crash_file: str
    write_ts_ns: int
    read_ts_ns: int
    lock_held: bool
    hazard: str

    @property
    def race_window_ms(self) -> float:
        return (self.read_ts_ns - self.write_ts_ns) / NS_PER_MS


@dataclass(slots=True)
class CausalStep:
    """One replayable step of the minimal subgraph, in causal order.

    ``ts_ns`` is when the write landed; ``span_start_ns`` is when the request
    that caused it actually began. A replay must reproduce the *start* times --
    replaying commit times would silently move the webhook's read-modify-write
    window and test an interleaving that never happened.
    """

    index: int
    ts_ns: int
    offset_ms: float
    trace_id: str
    endpoint: str
    kind: str          # "webhook" | "worker" | "request"
    payload_json: str
    field: str = ""
    old_val: str = ""
    new_val: str = ""
    lock_held: bool = False
    function_name: str = ""
    span_id: str = ""
    span_start_ns: int = 0


@dataclass(slots=True)
class Incident:
    """Everything triage learned about one production crash."""

    incident_id: str
    crash_trace_id: str
    crash_span_id: str
    crash_function: str
    crash_file: str
    crash_endpoint: str
    entity_key: str
    entity_type: str
    primary_key: str
    error_type: str = ""
    error_message: str = ""
    interferences: list[Interference] = field(default_factory=list)
    causal_steps: list[CausalStep] = field(default_factory=list)
    replay_steps: list[CausalStep] = field(default_factory=list)
    crash_ts_ns: int = 0
    crash_span_start_ns: int = 0
    crash_payload_json: str = "{}"
    upstream_mutator: str = ""
    upstream_file: str = ""
    total_spans_considered: int = 0
    spans_pruned: int = 0

    @property
    def pruning_ratio(self) -> float:
        if self.total_spans_considered == 0:
            return 0.0
        return self.spans_pruned / self.total_spans_considered


@dataclass(slots=True)
class Triage:
    """The result of the red/green heal loop."""

    incident: Incident
    red_result: dict[str, Any] = field(default_factory=dict)
    fix_diff: str = ""
    fix_summary: str = ""
    green_result: dict[str, Any] = field(default_factory=dict)
    healed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
