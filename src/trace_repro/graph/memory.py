"""An in-process property graph.

This is not a stub. It stores nodes, typed relationships and relationship
properties, and it runs the *same* traversals the FalkorDB backend delegates to
Cypher: cross-trace collision search, breadth-limited shortest causal path, and
minimal-subgraph extraction.

It exists so that:
  * the unit tests can assert traversal semantics without a live server, and
  * ``trace-repro heal`` still works on a laptop with no FalkorDB running.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..models import (
    ASTFunction,
    CausalStep,
    Interference,
    Span,
    StateEntity,
    StateMutation,
    Trace,
)
from ..schema import (
    CAUSED_READ,
    EMITTED,
    EXECUTED_FUNCTION,
    HAPPENED_BEFORE,
    HAS_SPAN,
    MUTATED,
    MUTATED_BY_FUNCTION,
    NS_PER_MS,
    READ_ENTITY,
    WROTE_ENTITY,
    kind_of,
)
from .store import GraphStore


@dataclass(slots=True)
class Rel:
    src: str
    type: str
    dst: str
    label: str
    props: dict[str, Any] = field(default_factory=dict)


class MemoryStore(GraphStore):
    """Single-process implementation of the graph contract."""

    def __init__(self) -> None:
        self.traces: dict[str, Trace] = {}
        self.spans: dict[str, Span] = {}
        self.entities: dict[str, StateEntity] = {}
        self.functions: dict[str, ASTFunction] = {}
        self.mutations: dict[str, StateMutation] = {}
        self._out: dict[tuple[str, str], list[Rel]] = {}
        self._in: dict[tuple[str, str], list[Rel]] = {}
        self._rel_count = 0
        self._graph_name = "memory://trace_prod"

    # -- identity ---------------------------------------------------------
    @property
    def name(self) -> str:
        return "memory"

    # -- plumbing ---------------------------------------------------------
    def _rel(
        self,
        src: str,
        rtype: str,
        dst: str,
        src_label: str,
        dst_label: str,
        **props: Any,
    ) -> Rel:
        """Record a typed edge.

        Both endpoint labels are required: the outgoing index is keyed by the
        source node and the incoming index by the destination node, and in this
        graph those labels are almost always different (``Trace`` -> ``Span``).
        """
        r = Rel(src=src, type=rtype, dst=dst, label=src_label, props=props)
        self._out.setdefault((src_label, src), []).append(r)
        self._in.setdefault((dst_label, dst), []).append(r)
        self._rel_count += 1
        return r

    def _out_rels(self, label: str, src: str, rtype: str) -> list[Rel]:
        return [r for r in self._out.get((label, src), []) if r.type == rtype]

    def _in_rels(self, label: str, dst: str, rtype: str) -> list[Rel]:
        return [r for r in self._in.get((label, dst), []) if r.type == rtype]

    # -- writes -----------------------------------------------------------
    def upsert_trace(self, trace: Trace) -> None:
        self.traces[trace.id] = trace

    def upsert_span(self, span: Span) -> None:
        self.spans[span.id] = span

    def upsert_entity(self, entity: StateEntity) -> None:
        self.entities[entity.key] = entity

    def upsert_function(self, fn: ASTFunction) -> None:
        key = fn.qualified_name or f"{fn.file_path}::{fn.name}"
        if key not in self.functions:
            self.functions[key] = fn

    def link_trace_span(self, trace_id: str, span_id: str) -> None:
        if trace_id in self.traces and span_id in self.spans:
            self._rel(trace_id, HAS_SPAN, span_id, "Trace", "Span")

    def link_span_function(self, span_id: str, fn: ASTFunction) -> None:
        self.upsert_function(fn)
        key = fn.qualified_name or f"{fn.file_path}::{fn.name}"
        if not self._out_rels("Span", span_id, EXECUTED_FUNCTION):
            self._rel(span_id, EXECUTED_FUNCTION, key, "Span", "ASTFunction")

    def link_entity_function(self, entity: StateEntity, fn: ASTFunction) -> None:
        self.upsert_entity(entity)
        self.upsert_function(fn)
        key = fn.qualified_name or f"{fn.file_path}::{fn.name}"
        if not self._out_rels("StateEntity", entity.key, MUTATED_BY_FUNCTION):
            self._rel(entity.key, MUTATED_BY_FUNCTION, key, "StateEntity", "ASTFunction")

    def record_mutation(self, mutation: StateMutation) -> None:
        self.mutations[mutation.id] = mutation
        entity = StateEntity(mutation.entity_type, mutation.primary_key)
        self.upsert_entity(entity)
        self._rel(mutation.span_id, EMITTED, mutation.id, "Span", "StateMutation")
        self._rel(
            mutation.id, MUTATED, entity.key, "StateMutation", "StateEntity",
            ts_ns=mutation.ts_ns,
        )

    def link_happened_before(
        self, prev_mutation_id: str, next_mutation_id: str, delta_ms: float, overlap: bool
    ) -> None:
        self._rel(
            prev_mutation_id,
            HAPPENED_BEFORE,
            next_mutation_id,
            "StateMutation",
            "StateMutation",
            delta_ms=delta_ms,
            concurrent_overlap=overlap,
        )

    def link_caused_read(self, mutation_id: str, span_id: str) -> None:
        self._rel(mutation_id, CAUSED_READ, span_id, "StateMutation", "Span")

    def record_entity_access(
        self,
        span_id: str,
        entity: StateEntity,
        rel_type: str,
        ts_ns: int,
        lock_held: bool = False,
        tx_isolation: str = "READ COMMITTED",
        snapshot_version: int = 0,
    ) -> None:
        """The bridge edge ``(:Span)-[:READ_ENTITY|WROTE_ENTITY]->(:StateEntity)``."""
        self.upsert_entity(entity)
        self._rel(
            span_id,
            rel_type,
            entity.key,
            "Span",
            "StateEntity",
            ts_ns=ts_ns,
            lock_held=lock_held,
            tx_isolation=tx_isolation,
            snapshot_version=snapshot_version,
        )

    def latest_mutation_id(self, entity: StateEntity) -> str | None:
        cands = [
            m
            for m in self.mutations.values()
            if m.entity_type == entity.entity_type and m.primary_key == entity.primary_key
        ]
        if not cands:
            return None
        return max(cands, key=lambda m: (m.seq_no, m.ts_ns)).id

    # -- traversals -------------------------------------------------------
    def _span_function(self, span_id: str) -> tuple[str, str]:
        fn_key = None
        for r in self._out_rels("Span", span_id, EXECUTED_FUNCTION):
            fn_key = r.dst
            break
        if fn_key and fn_key in self.functions:
            fn = self.functions[fn_key]
            return fn.name, fn.file_path
        span = self.spans.get(span_id)
        return (span.function_name if span else "", span.file_path if span else "")

    def _entity_access(self, span_id: str) -> list[Rel]:
        return self._out_rels("Span", span_id, READ_ENTITY) + self._out_rels(
            "Span", span_id, WROTE_ENTITY
        )

    def _trace_of_span(self, span_id: str) -> str:
        span = self.spans.get(span_id)
        if span:
            return span.trace_id
        for r in self._in_rels("Span", span_id, HAS_SPAN):
            return r.src
        return ""

    def find_collisions(
        self, crash_span_id: str, window_ns: int, ignore_trace_id: str = ""
    ) -> list[Interference]:
        crash_trace = ignore_trace_id or self._trace_of_span(crash_span_id)
        crash_fn, crash_file = self._span_function(crash_span_id)

        found: dict[tuple, Interference] = {}
        for access in self._entity_access(crash_span_id):
            entity = self.entities.get(access.dst)
            if entity is None:
                continue
            read_ts = int(access.props.get("ts_ns", 0))
            # Hop BACKWARDS into every other trace that wrote this same row.
            for writer_rel in self._in_rels("StateEntity", entity.key, WROTE_ENTITY):
                if writer_rel.src == crash_span_id:
                    continue
                writer_trace = self._trace_of_span(writer_rel.src)
                if writer_trace == crash_trace:
                    continue
                write_ts = int(writer_rel.props.get("ts_ns", 0))
                if write_ts > read_ts or (read_ts - write_ts) >= window_ns:
                    continue
                fn_name, fn_file = self._span_function(writer_rel.src)
                trace = self.traces.get(writer_trace)
                lock_held = bool(writer_rel.props.get("lock_held", False))
                hazard = "RAW" if lock_held else "WAW"
                if fn_name and crash_fn and fn_name == crash_fn:
                    hazard = "RAW"
                interference = Interference(
                    entity_key=entity.key,
                    entity_type=entity.entity_type,
                    primary_key=entity.primary_key,
                    other_trace_id=writer_trace,
                    other_span_id=writer_rel.src,
                    other_endpoint=trace.entry_endpoint if trace else writer_trace,
                    other_payload=trace.payload_json if trace else "{}",
                    mutator_function=fn_name,
                    mutator_file=fn_file,
                    crash_function=crash_fn,
                    crash_file=crash_file,
                    write_ts_ns=write_ts,
                    read_ts_ns=read_ts,
                    lock_held=lock_held,
                    hazard=hazard,
                )
                # One request that wrote three fields is one interfering
                # writer, not three. Keep the earliest write for the report.
                key = (entity.key, writer_trace, writer_rel.src)
                existing = found.get(key)
                if existing is None or interference.write_ts_ns < existing.write_ts_ns:
                    found[key] = interference

        results = sorted(found.values(), key=lambda i: i.write_ts_ns)
        return results

    def _shortest_path(
        self, src: str, dst: str, labels: tuple[str, ...], rels: tuple[str, ...], max_depth: int
    ) -> list[str]:
        """Breadth-limited BFS over the union of several relationship types."""
        seen = {src: None}
        q: deque[tuple[str, int]] = deque([(src, 0)])
        while q:
            node, depth = q.popleft()
            if node == dst:
                break
            if depth >= max_depth:
                continue
            for label in labels:
                for r in self._out.get((label, node), []):
                    if r.type in rels and r.dst not in seen:
                        seen[r.dst] = node
                        q.append((r.dst, depth + 1))
        if dst not in seen:
            return []
        path = [dst]
        while seen[path[-1]] is not None:
            path.append(seen[path[-1]])
        return list(reversed(path))

    def causal_chain(
        self, entity: StateEntity, crash_span_id: str, max_depth: int = 12
    ) -> list[CausalStep]:
        seqs = sorted(
            (
                m
                for m in self.mutations.values()
                if m.entity_type == entity.entity_type
                and m.primary_key == entity.primary_key
            ),
            key=lambda m: (m.seq_no, m.ts_ns),
        )
        if not seqs:
            return []

        node_ids = [m.id for m in seqs]
        src = node_ids[0]
        path = self._shortest_path(
            src,
            crash_span_id,
            labels=("StateMutation", "Span"),
            rels=(HAPPENED_BEFORE, CAUSED_READ),
            max_depth=max_depth,
        )
        # Fall back to raw causal ordering when the crash span is not reachable
        # through the HAPPENED_BEFORE chain (e.g. read happened with no prior
        # write recorded). The replay must still be complete.
        on_path = [n for n in path if n in self.mutations]
        if len(on_path) < len(node_ids):
            on_path = node_ids

        by_id = {m.id: m for m in seqs}
        ordered = [by_id[n] for n in on_path]
        base = ordered[0].ts_ns

        steps: list[CausalStep] = []
        for i, m in enumerate(ordered):
            trace = self.traces.get(m.trace_id)
            span = self.spans.get(m.span_id)
            endpoint = trace.entry_endpoint if trace else m.trace_id
            steps.append(
                CausalStep(
                    index=i,
                    ts_ns=m.ts_ns,
                    offset_ms=round((m.ts_ns - base) / NS_PER_MS, 3),
                    trace_id=m.trace_id,
                    endpoint=endpoint,
                    kind=kind_of(endpoint),
                    payload_json=trace.payload_json if trace else "{}",
                    field=m.field,
                    old_val=m.old_val,
                    new_val=m.new_val,
                    lock_held=m.lock_held,
                    function_name=m.function_name,
                    span_id=m.span_id,
                    span_start_ns=span.start_ns if span else m.ts_ns,
                )
            )
        steps.sort(key=lambda s: s.ts_ns)
        for i, s in enumerate(steps):
            s.index = i
        return steps

    @staticmethod
    def _kind_of(endpoint: str) -> str:
        return kind_of(endpoint)

    def span_count(self) -> int:
        return len(self.spans)

    def trace_count(self) -> int:
        return len(self.traces)

    def snapshot_to(self, destination_graph: str) -> bool:
        # A copy of an in-process graph is a deep clone of the same structures.
        clone = MemoryStore()
        clone.traces = dict(self.traces)
        clone.spans = dict(self.spans)
        clone.entities = dict(self.entities)
        clone.functions = dict(self.functions)
        clone.mutations = dict(self.mutations)
        clone._out = {k: list(v) for k, v in self._out.items()}
        clone._in = {k: list(v) for k, v in self._in.items()}
        clone._rel_count = self._rel_count
        clone._graph_name = f"memory://{destination_graph}"
        self._snapshot = clone
        return True

    def clear(self) -> None:
        self.traces.clear()
        self.spans.clear()
        self.entities.clear()
        self.functions.clear()
        self.mutations.clear()
        self._out.clear()
        self._in.clear()
        self._rel_count = 0

    # -- introspection for tests -----------------------------------------
    def error_spans(self) -> list[Any]:
        return [s for s in self.spans.values() if s.status == "ERROR"]

    def span_by_id(self, span_id: str) -> Span | None:
        return self.spans.get(span_id)

    def trace_by_id(self, trace_id: str) -> Trace | None:
        return self.traces.get(trace_id)

    def trace_of_span(self, span_id: str) -> str:
        return self._trace_of_span(span_id)

    def entry_endpoint_of_span(self, span_id: str) -> str:
        trace = self.traces.get(self._trace_of_span(span_id))
        return trace.entry_endpoint if trace else ""

    def rel_count(self) -> int:
        return self._rel_count

    def neighbours(self, label: str, node_id: str, rel_type: str) -> list[str]:
        return [r.dst for r in self._out_rels(label, node_id, rel_type)]

    def incoming(self, label: str, node_id: str, rel_type: str) -> list[str]:
        return [r.src for r in self._in_rels(label, node_id, rel_type)]

    def mutations_for(self, **kwargs: Iterable) -> list[StateMutation]:
        out = list(self.mutations.values())
        for key, value in kwargs.items():
            out = [m for m in out if getattr(m, key) == value]
        return out

    def entity_writers(self, entity: StateEntity) -> list[dict]:
        """Every span that wrote this entity, one row per writer.

        A request that updated three fields wrote three edges but is one
        interfering writer. Reporting per-field here would disagree with the
        FalkorDB backend, where ``MERGE`` collapses the edges for the same
        (span, entity) pair.
        """
        best: dict[str, dict] = {}
        for rel in self._in_rels("StateEntity", entity.key, "WROTE_ENTITY"):
            fn, _path = self._span_function(rel.src)
            trace_id = self._trace_of_span(rel.src)
            trace = self.traces.get(trace_id)
            row = {
                "span_id": rel.src,
                "trace_id": trace_id,
                "entry_endpoint": trace.entry_endpoint if trace else trace_id,
                "function_name": fn,
                "ts_ns": int(rel.props.get("ts_ns", 0)),
                "lock_held": bool(rel.props.get("lock_held", False)),
            }
            existing = best.get(rel.src)
            if existing is None or row["ts_ns"] < existing["ts_ns"]:
                best[rel.src] = row
        return sorted(best.values(), key=lambda r: r["ts_ns"])
