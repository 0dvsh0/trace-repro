"""Cross-trace triage.

Given a crashing span, the job is to answer the question a ``trace_id`` lookup
physically cannot: *which other requests, from entirely unrelated traces,
mutated the row this crash read?*

The traversal is three hops:

    (:Span crash) -[READ_ENTITY]-> (:StateEntity) <-[WROTE_ENTITY]- (:Span mutator)

The middle node is the bridge. Without it there is no edge between the two
spans and no amount of log grepping will find it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..graph.store import GraphStore
from ..models import CausalStep, Incident, Interference, StateEntity
from ..schema import DEFAULT_CAUSAL_WINDOW_MS, NS_PER_MS


def build_replay_plan(
    steps: list[CausalStep],
    crash_span_id: str,
    crash_trace_id: str,
    crash_endpoint: str,
    crash_ts_ns: int,
    crash_payload_json: str = "{}",
    crash_span_start_ns: int = 0,
    crash_function: str = "",
) -> list[CausalStep]:
    """Collapse mutation-level steps into one replay participant per trace.

    A single request writes several fields; replaying it once per field would
    fire the webhook twice and test a scenario that never occurred. Offsets are
    measured from each participant's **span start** (when the request began),
    not from its commit time -- otherwise a request with slow work in the middle
    gets replayed from the wrong point in time and the window it depended on
    disappears.

    The crashing request is appended last: it is the one whose failure the test
    is asserting against, so it has to be part of the replay.
    """
    by_trace: dict[str, CausalStep] = {}
    for step in steps:
        if not step.trace_id:
            continue
        current = by_trace.get(step.trace_id)
        if current is None or step.span_start_ns < current.span_start_ns:
            by_trace[step.trace_id] = step

    plan = sorted(by_trace.values(), key=lambda s: (s.span_start_ns, s.ts_ns))
    base = plan[0].span_start_ns if plan else crash_span_start_ns

    if crash_trace_id and crash_trace_id not in by_trace:
        plan.append(
            CausalStep(
                index=0,
                ts_ns=crash_ts_ns,
                offset_ms=0.0,
                trace_id=crash_trace_id,
                endpoint=crash_endpoint,
                kind="request",
                payload_json=crash_payload_json or "{}",
                function_name=crash_function,
                span_id=crash_span_id,
                span_start_ns=crash_span_start_ns or crash_ts_ns,
            )
        )

    for i, step in enumerate(plan):
        step.index = i
        step.offset_ms = round((step.span_start_ns - base) / NS_PER_MS, 3)
    return plan


@dataclass(slots=True)
class CrashCandidate:
    span_id: str
    trace_id: str
    function_name: str
    file_path: str
    error_type: str
    error_message: str
    lineno: int = 0


@dataclass(slots=True)
class PruneReport:
    """How much traffic the causal graph threw away."""

    total_spans: int
    total_traces: int
    retained_spans: int
    retained_traces: int
    detail: dict[str, int] = field(default_factory=dict)

    @property
    def prune_ratio(self) -> float:
        if self.total_spans == 0:
            return 0.0
        return 1.0 - (self.retained_spans / self.total_spans)


class TriageEngine:
    """Turns a graph into an :class:`~trace_repro.models.Incident`."""

    def __init__(
        self,
        store: GraphStore,
        causal_window_ms: int = DEFAULT_CAUSAL_WINDOW_MS,
        max_depth: int = 12,
    ) -> None:
        self.store = store
        self.window_ns = causal_window_ms * NS_PER_MS
        self.max_depth = max_depth

    # -- discovery --------------------------------------------------------
    def crash_spans(self) -> list[CrashCandidate]:
        """Every span in the graph whose status is ERROR."""
        finder = getattr(self.store, "error_spans", None)
        if finder is None:
            return []
        return [
            c if isinstance(c, CrashCandidate) else self._candidate(c)
            for c in finder()
        ]

    @staticmethod
    def _candidate(span) -> CrashCandidate:
        return CrashCandidate(
            span_id=span.id,
            trace_id=span.trace_id,
            function_name=span.function_name,
            file_path=span.file_path,
            error_type=span.error_type,
            error_message=span.error_message,
            lineno=span.lineno,
        )

    def locate_crash(
        self, span_id: str | None = None, error_type: str | None = None
    ) -> CrashCandidate | None:
        if span_id:
            getter = getattr(self.store, "span_by_id", None)
            span = getter(span_id) if getter else None
            return self._candidate(span) if span else None
        candidates = self.crash_spans()
        if error_type:
            candidates = [c for c in candidates if c.error_type == error_type]
        return candidates[0] if candidates else None

    # -- the traversal ----------------------------------------------------
    def triage(
        self,
        crash_span_id: str,
        incident_id: str | None = None,
        error_type: str = "",
        error_message: str = "",
    ) -> Incident:
        collisions = self.store.find_collisions(crash_span_id, self.window_ns)
        if not collisions:
            raise LookupError(
                f"no cross-trace collision found for span {crash_span_id!r}; "
                "this crash is not state-interference shaped"
            )

        primary = collisions[0]
        entity = StateEntity(primary.entity_type, primary.primary_key)
        steps = self.store.causal_chain(entity, crash_span_id, self.max_depth)
        crash_trace = self.store_span_trace(crash_span_id)
        crash_ts = max(c.read_ts_ns for c in collisions)
        crash_payload = self._crash_payload(crash_trace)
        crash_start = self._span_start(crash_span_id, crash_ts)
        replay = build_replay_plan(
            steps,
            crash_span_id=crash_span_id,
            crash_trace_id=crash_trace,
            crash_endpoint=self._crash_endpoint(crash_span_id),
            crash_function=primary.crash_function,
            crash_ts_ns=crash_ts,
            crash_payload_json=crash_payload,
            crash_span_start_ns=crash_start,
        )

        upstream = self._upstream_mutator(primary)
        pruning = self.pruning_report(crash_span_id, collisions)

        incident_id = incident_id or f"inc_{primary.primary_key}"
        return Incident(
            incident_id=incident_id,
            crash_trace_id=crash_trace,
            crash_span_id=crash_span_id,
            crash_function=primary.crash_function,
            crash_file=primary.crash_file,
            crash_endpoint=self._crash_endpoint(crash_span_id),
            entity_key=entity.key,
            entity_type=entity.entity_type,
            primary_key=entity.primary_key,
            error_type=error_type,
            error_message=error_message,
            interferences=collisions,
            causal_steps=steps,
            replay_steps=replay,
            crash_ts_ns=crash_ts,
            crash_span_start_ns=crash_start,
            crash_payload_json=crash_payload,
            upstream_mutator=upstream,
            upstream_file=primary.mutator_file,
            total_spans_considered=pruning.total_spans,
            spans_pruned=pruning.total_spans - pruning.retained_spans,
        )

    def _crash_payload(self, crash_trace_id: str) -> str:
        getter = getattr(self.store, "trace_by_id", None)
        trace = getter(crash_trace_id) if getter else None
        return trace.payload_json if trace else "{}"

    def _span_start(self, span_id: str, fallback: int) -> int:
        getter = getattr(self.store, "span_by_id", None)
        span = getter(span_id) if getter else None
        return span.start_ns if span else fallback

    def store_span_trace(self, crash_span_id: str) -> str:
        getter = getattr(self.store, "trace_of_span", None)
        if getter:
            return getter(crash_span_id)
        return ""

    def _crash_endpoint(self, crash_span_id: str) -> str:
        getter = getattr(self.store, "entry_endpoint_of_span", None)
        return getter(crash_span_id) if getter else ""

    @staticmethod
    def _upstream_mutator(primary: Interference) -> str:
        """The real culprit: the writer that ran *without* a lock, earliest first."""
        unlocked = [c for c in (primary,) if not c.lock_held]
        chosen = (unlocked or [primary])[0]
        return chosen.mutator_function

    # -- pruning ----------------------------------------------------------
    def pruning_report(
        self, crash_span_id: str, collisions: list[Interference]
    ) -> PruneReport:
        total_spans = self.store.span_count()
        total_traces = self.store.trace_count()
        retained_traces = {c.other_trace_id for c in collisions}
        trace_of_span = getattr(self.store, "trace_of_span", None)
        if trace_of_span:
            retained_traces.add(trace_of_span(crash_span_id))
        retained_traces.discard("")
        retained_spans = len(collisions) + 1
        return PruneReport(
            total_spans=total_spans,
            total_traces=total_traces,
            retained_spans=retained_spans,
            retained_traces=len(retained_traces),
            detail={
                "causal_traces": len(retained_traces),
                "causal_spans": retained_spans,
            },
        )
