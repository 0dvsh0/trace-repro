"""The graph store contract.

trace-repro refuses to work without a property graph: the product's value is
the *traversal*, not the storage. To make that claim falsifiable we implement
this contract twice -- once against a real FalkorDB server speaking real
Cypher, once against an in-process graph that runs the same traversal
algorithms. The analysis layer is written against this interface only, so
"remove the graph and the product stops" is a testable statement.
"""

from __future__ import annotations

import abc
from collections.abc import Iterable

from ..models import (
    ASTFunction,
    CausalStep,
    Interference,
    Span,
    StateEntity,
    StateMutation,
    Trace,
)


class GraphUnavailable(RuntimeError):
    """Raised when a requested graph backend cannot be reached."""


class GraphStore(abc.ABC):
    """A write/traverse interface over the causal property graph."""

    # -- identity ---------------------------------------------------------
    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Human readable backend name, e.g. ``falkordb`` or ``memory``."""

    # -- writes -----------------------------------------------------------
    @abc.abstractmethod
    def upsert_trace(self, trace: Trace) -> None: ...

    @abc.abstractmethod
    def upsert_span(self, span: Span) -> None: ...

    @abc.abstractmethod
    def upsert_entity(self, entity: StateEntity) -> None: ...

    @abc.abstractmethod
    def upsert_function(self, fn: ASTFunction) -> None: ...

    @abc.abstractmethod
    def link_trace_span(self, trace_id: str, span_id: str) -> None: ...

    @abc.abstractmethod
    def link_span_function(self, span_id: str, fn: ASTFunction) -> None: ...

    @abc.abstractmethod
    def link_entity_function(self, entity: StateEntity, fn: ASTFunction) -> None: ...

    @abc.abstractmethod
    def record_mutation(self, mutation: StateMutation) -> None:
        """Persist a StateMutation and wire its ``EMITTED``/``MUTATED`` edges."""

    @abc.abstractmethod
    def link_happened_before(
        self, prev_mutation_id: str, next_mutation_id: str, delta_ms: float, overlap: bool
    ) -> None: ...

    @abc.abstractmethod
    def link_caused_read(self, mutation_id: str, span_id: str) -> None: ...

    @abc.abstractmethod
    def latest_mutation_id(self, entity: StateEntity) -> str | None:
        """Most recent mutation for an entity, used to hang CAUSED_READ off."""

    # -- traversals -------------------------------------------------------
    @abc.abstractmethod
    def find_collisions(
        self, crash_span_id: str, window_ns: int, ignore_trace_id: str = ""
    ) -> list[Interference]:
        """Cross-trace collision query -- the core of the product.

        Given a crashing span, walk *out* of its trace through the shared
        ``(:StateEntity)`` node and back *into* every other trace that wrote
        that same row inside the causal window.
        """

    @abc.abstractmethod
    def causal_chain(
        self, entity: StateEntity, crash_span_id: str, max_depth: int = 12
    ) -> list[CausalStep]:
        """Minimal replay subgraph: ordered steps from first write to the crash."""

    @abc.abstractmethod
    def span_count(self) -> int: ...

    @abc.abstractmethod
    def trace_count(self) -> int: ...

    @abc.abstractmethod
    def snapshot_to(self, destination_graph: str) -> bool:
        """Copy the live graph into an isolated graph for Red/Green diffing."""

    @abc.abstractmethod
    def clear(self) -> None: ...


def entity_match(entity: StateEntity) -> dict:
    return {"entity_type": entity.entity_type, "primary_key": entity.primary_key}


def dedupe(items: Iterable) -> list:
    seen = set()
    out = []
    for it in items:
        key = repr(it)
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out
