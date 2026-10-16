"""Telemetry capture.

Two things are captured, and the second one is the one that matters:

1. spans (OpenTelemetry-shaped: trace_id / span_id / start_ns / end_ns / status)
2. **state mutations** -- every field-level write to a stateful row, with the
   value before, the value after, and whether a row lock was held

(2) is the bridge material. Without it you have N isolated trace trees and no
way to know that three of them were fighting over one row.

Function attribution is read from the live Python frame with ``inspect`` --
nothing is hand-declared, so the graph's ``(:ASTFunction)`` edges point at the
function that actually ran.
"""

from __future__ import annotations

import inspect
import json
import os
import time
import uuid
from typing import Any

from ..graph.store import GraphStore
from ..models import ASTFunction, Span, StateEntity, StateMutation, Trace, now_ns
from ..schema import READ_ENTITY, WROTE_ENTITY

# Two writes to the same row this close together without a lock are considered
# to have overlapped in time.
OVERLAP_THRESHOLD_MS = 50.0


def short_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:12]
    return f"{prefix}{raw}" if prefix else raw


OWN_PACKAGE = __name__.split(".")[0]


def caller_frame(skip_prefixes: tuple[str, ...] = (OWN_PACKAGE,)):
    """The first frame outside trace-repro itself.

    Counting frames (``f_back`` N times) is brittle: add a helper, refactor,
    or switch a contextmanager to a class and every depth is off by one.
    Walking out of the package by module name cannot drift.
    """
    frame = inspect.currentframe()
    try:
        while frame is not None:
            module = frame.f_globals.get("__name__", "")
            if not any(
                module == prefix or module.startswith(prefix + ".")
                for prefix in skip_prefixes
            ):
                return frame
            frame = frame.f_back
        return None
    finally:
        del frame


def frame_info(
    depth: int = 2, skip_prefixes: tuple[str, ...] = (OWN_PACKAGE,)
) -> tuple[str, str, int]:
    """``(function_name, file_path, lineno)`` of the caller's caller.

    ``depth`` counts *outside* ``skip_prefixes``: 1 is the immediate caller,
    2 is its caller, and so on.
    """
    frame = caller_frame(skip_prefixes)
    try:
        for _ in range(max(0, depth - 1)):
            if frame is None:
                break
            frame = frame.f_back
        if frame is None:
            return ("<unknown>", "<unknown>", 0)
        return (frame.f_code.co_name, os.path.abspath(frame.f_code.co_filename), frame.f_lineno)
    finally:
        del frame


# -- spans ------------------------------------------------------------
class _SpanContext:
    """Context manager for one span.

    Deliberately a class rather than ``@contextmanager``: a generator-based
    context manager resumes inside ``contextlib.__enter__``, so frame
    inspection from inside it reports *contextlib* as the caller. Capturing in
    ``__init__`` runs in the real caller's frame.
    """

    __slots__ = ("telemetry", "span", "status")

    def __init__(self, telemetry: Telemetry, name: str, trace: Trace,
                 status: str, capture_frame: bool) -> None:
        self.telemetry = telemetry
        self.status = status
        fn, path, lineno = frame_info(depth=1) if capture_frame else ("", "", 0)
        telemetry._span_seq += 1
        self.span = Span(
            id=short_id("s"),
            trace_id=trace.id,
            name=name,
            start_ns=now_ns(),
            function_name=fn,
            file_path=path,
            lineno=lineno,
        )

    def __enter__(self) -> Span:
        telemetry = self.telemetry
        span = self.span
        telemetry.store.upsert_span(span)
        telemetry.store.link_trace_span(span.trace_id, span.id)
        telemetry.counts["spans"] += 1
        telemetry._link_function(span, span.function_name, span.file_path)
        return span

    def __exit__(self, exc_type, exc, tb) -> bool:
        span = self.span
        span.end_ns = now_ns()
        if exc_type is None:
            span.status = self.status
        else:
            span.status = "ERROR"
            span.error_type = getattr(exc_type, "__name__", str(exc_type))
            span.error_message = str(exc)
            self.telemetry.errors.append((span.id, span.error_type, span.error_message))
        self.telemetry.store.upsert_span(span)
        return False


class Telemetry:
    """Write-through telemetry sink bound to one graph store."""

    def __init__(
        self,
        store: GraphStore,
        service: str = "unknown",
        max_backlog: int = 200_000,
    ) -> None:
        self.store = store
        self.service = service
        self._seq: dict[tuple[str, str], int] = {}
        self._last_mutation: dict[tuple[str, str], str] = {}
        self._last_ts: dict[tuple[str, str], int] = {}
        self._span_seq = 0
        self.counts = {"traces": 0, "spans": 0, "mutations": 0, "reads": 0}
        self.max_backlog = max_backlog
        self.errors: list[tuple[str, str, str]] = []

    # -- lifecycle --------------------------------------------------------
    def flush(self) -> None:
        flush = getattr(self.store, "flush", None)
        if flush:
            flush()

    def close(self) -> None:
        self.flush()

    # -- traces -----------------------------------------------------------
    def start_request(
        self, endpoint: str, payload: dict | None = None, trace_id: str | None = None
    ) -> Trace:
        trace = Trace(
            id=trace_id or short_id(),
            entry_endpoint=endpoint,
            service=self.service,
            payload_json=json.dumps(payload or {}, separators=(",", ":"), sort_keys=True),
            start_ns=now_ns(),
        )
        self.store.upsert_trace(trace)
        self.counts["traces"] += 1
        return trace

    # -- spans ------------------------------------------------------------
    def span(
        self, name: str, trace: Trace, status: str = "OK", capture_frame: bool = True
    ) -> _SpanContext:
        return _SpanContext(self, name, trace, status, capture_frame)

    def _link_function(self, span: Span, fn_name: str, path: str) -> None:
        if not fn_name or not path or path.endswith("trace_repro/ingest/tracer.py"):
            return
        try:
            fn = resolve_function(path, fn_name)
        except Exception:
            return
        if fn is not None:
            self.store.link_span_function(span.id, fn)

    # -- state ------------------------------------------------------------
    def record_read(self, span: Span, entity: StateEntity, lock_held: bool = False) -> None:
        ts = now_ns()
        self.store.upsert_entity(entity)
        self.record_entity_access(span, entity, READ_ENTITY, ts, lock_held)
        prev = self._last_mutation.get((entity.entity_type, entity.primary_key))
        if prev:
            # The crash span is only reachable by shortestPath if some write
            # is explicitly linked as having caused it.
            self.store.link_caused_read(prev, span.id)
        self.counts["reads"] += 1

    def record_write(
        self,
        span: Span,
        entity: StateEntity,
        field: str,
        old_val: Any,
        new_val: Any,
        lock_held: bool = False,
    ) -> StateMutation:
        ts = now_ns()
        key = (entity.entity_type, entity.primary_key)
        seq_no = self._seq.get(key, 0)
        self._seq[key] = seq_no + 1

        mutation = StateMutation(
            id=short_id("m"),
            trace_id=span.trace_id,
            span_id=span.id,
            entity_type=entity.entity_type,
            primary_key=entity.primary_key,
            field=field,
            old_val="" if old_val is None else str(old_val),
            new_val="" if new_val is None else str(new_val),
            ts_ns=ts,
            seq_no=seq_no,
            lock_held=lock_held,
            function_name=span.function_name,
        )
        self.store.upsert_entity(entity)
        self.store.record_mutation(mutation)
        self.record_entity_access(span, entity, WROTE_ENTITY, ts, lock_held)

        prev_id = self._last_mutation.get(key)
        if prev_id:
            prev_ts = self._last_ts.get(key, ts)
            delta_ms = (ts - prev_ts) / 1_000_000.0
            self.store.link_happened_before(
                prev_id, mutation.id, delta_ms, delta_ms < OVERLAP_THRESHOLD_MS
            )
        self._last_mutation[key] = mutation.id
        self._last_ts[key] = ts
        self.counts["mutations"] += 1
        return mutation

    def record_entity_access(
        self, span: Span, entity: StateEntity, rel_type: str, ts_ns: int, lock_held: bool
    ) -> None:
        # The entity node must exist before a bridge edge can MATCH onto it.
        self.store.upsert_entity(entity)
        self.store.record_entity_access(
            span.id,
            entity,
            rel_type,
            ts_ns,
            lock_held=lock_held,
            tx_isolation="READ COMMITTED",
        )


# --------------------------------------------------------------------------
# Source -> (:ASTFunction)
# --------------------------------------------------------------------------

_function_cache: dict[tuple[str, str], ASTFunction] = {}


def resolve_function(file_path: str, func_name: str) -> ASTFunction | None:
    """Locate ``func_name`` inside ``file_path`` and describe it as a node."""
    key = (os.path.normcase(os.path.abspath(file_path)), func_name)
    if key in _function_cache:
        return _function_cache[key]
    from ..astmap.indexer import locate_function

    fn = locate_function(file_path, func_name)
    if fn is not None:
        _function_cache[key] = fn
    return fn


def clear_function_cache() -> None:
    _function_cache.clear()


def now_ms() -> float:
    return time.time() * 1000.0
