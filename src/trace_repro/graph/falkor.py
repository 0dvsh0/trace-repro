"""FalkorDB backend -- real Cypher against a real server.

Writes are buffered and flushed with batched ``UNWIND ... MERGE`` statements,
which is how you get tens of thousands of spans into a property graph without
a round trip per span. Reads are the canonical queries from
:mod:`trace_repro.graph.queries`, unmodified.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
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
from ..schema import NS_PER_MS, kind_of
from . import queries as Q
from .store import GraphStore, GraphUnavailable

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 6379


def _as_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_bool(v: Any, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)


def _as_str(v: Any, default: str = "") -> str:
    if v is None:
        return default
    return str(v)


class FalkorStore(GraphStore):
    """Property graph living in FalkorDB (Redis + graph module)."""

    def __init__(
        self,
        graph: str = "trace_prod",
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        username: str | None = None,
        password: str | None = None,
        ssl: bool = False,
        batch_size: int = 500,
        flush_every: int = 1,
    ) -> None:
        self.graph_name = graph
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.ssl = ssl
        self.batch_size = batch_size
        self.flush_every = max(1, int(flush_every))
        self._pending = 0
        self._buffers: dict[str, list[dict]] = {}
        self._db = self._connect(username, password, ssl)
        self._g = self._db.select_graph(self.graph_name)
        self._apply_schema()

    # -- connection -------------------------------------------------------
    def _connect(self, username: str | None, password: str | None, ssl: bool = False):
        try:
            from falkordb import FalkorDB
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise GraphUnavailable(
                "the 'falkordb' client is not installed; "
                "run: pip install falkordb"
            ) from exc
        try:
            kwargs: dict[str, Any] = {}
            if username:
                kwargs["username"] = username
            if password:
                kwargs["password"] = password
            if ssl:
                kwargs["ssl"] = True
            return FalkorDB(host=self.host, port=self.port, **kwargs)
        except Exception as exc:  # pragma: no cover - environment guard
            raise GraphUnavailable(f"cannot reach FalkorDB at {self.host}:{self.port}: {exc}") from exc


    def ping(self) -> bool:
        try:
            self._db.execute_command("PING")
            return True
        except Exception:
            return False

    def _apply_schema(self) -> None:
        for stmt in Q.SCHEMA_STATEMENTS:
            try:
                self._db.execute_command(stmt)
            except Exception:
                # Indexes already exist, or the server build does not support
                # them. Neither is fatal for correctness.
                pass

    @property
    def name(self) -> str:
        return "falkordb"

    # -- buffered writes --------------------------------------------------
    def _buf(self, kind: str, row: dict) -> None:
        self._buffers.setdefault(kind, []).append(row)
        self._pending += 1
        if self._pending >= self.batch_size * self.flush_every:
            self.flush()

    # Writers must run in dependency order: a MATCH can only find a node that
    # an earlier statement already created. Getting this wrong silently drops
    # edges instead of raising, which is exactly the kind of bug that makes a
    # graph "sometimes empty".
    WRITE_ORDER = (
        "trace",
        "entity",
        "function",
        "span",
        "span_function",
        "entity_function",
        "entity_access",
        "mutation",
        "happened_before",
        "caused_read",
    )

    def flush(self) -> None:
        if not self._buffers:
            return
        buffers, self._buffers = self._buffers, {}
        self._pending = 0

        for kind in self.WRITE_ORDER:
            rows = buffers.get(kind)
            if not rows:
                continue
            for i in range(0, len(rows), self.batch_size):
                self._run_write(kind, rows[i : i + self.batch_size])

        unknown = set(buffers) - set(self.WRITE_ORDER)
        if unknown:  # pragma: no cover - guards against a typo'd buffer name
            raise KeyError(f"unknown write buffer kind(s): {sorted(unknown)}")

    def _run_write(self, kind: str, rows: list[dict]) -> None:
        if kind == "trace":
            self._g.query(
                "UNWIND $rows AS row MERGE (t:Trace {id: row.id}) "
                "SET t.entry_endpoint = row.entry_endpoint, t.service = row.service, "
                "t.payload_json = row.payload_json, t.start_ns = row.start_ns, "
                "t.end_ns = row.end_ns, t.status = row.status",
                {"rows": rows},
            )
        elif kind == "span":
            self._g.query(
                "UNWIND $rows AS row MATCH (t:Trace {id: row.trace_id}) "
                "MERGE (s:Span {id: row.id}) "
                "SET s.trace_id = row.trace_id, s.name = row.name, s.start_ns = row.start_ns, "
                "s.end_ns = row.end_ns, s.status = row.status, "
                "s.function_name = row.function_name, s.file_path = row.file_path, "
                "s.lineno = row.lineno, s.parent_span_id = row.parent_span_id, "
                "s.error_type = row.error_type, s.error_message = row.error_message "
                "MERGE (t)-[:HAS_SPAN]->(s)",
                {"rows": rows},
            )
        elif kind == "entity":
            self._g.query(
                "UNWIND $rows AS row MERGE (e:StateEntity {entity_type: row.entity_type, "
                "primary_key: row.primary_key}) SET e.key = row.key, e.first_seen_ns = row.first_seen_ns",
                {"rows": rows},
            )
        elif kind == "function":
            self._g.query(
                "UNWIND $rows AS row MERGE (f:ASTFunction {qualified_name: row.qualified_name}) "
                "SET f.name = row.name, f.file_path = row.file_path, "
                "f.line_range = row.line_range, f.is_async = row.is_async",
                {"rows": rows},
            )
        elif kind == "span_function":
            self._g.query(
                "UNWIND $rows AS row MATCH (s:Span {id: row.span_id}) "
                "MATCH (f:ASTFunction {qualified_name: row.qualified_name}) "
                "MERGE (s)-[:EXECUTED_FUNCTION]->(f)",
                {"rows": rows},
            )
        elif kind == "entity_function":
            self._g.query(
                "UNWIND $rows AS row MATCH (e:StateEntity {entity_type: row.entity_type, "
                "primary_key: row.primary_key}) MATCH (f:ASTFunction {qualified_name: row.qualified_name}) "
                "MERGE (e)-[:MUTATED_BY_FUNCTION]->(f)",
                {"rows": rows},
            )
        elif kind == "mutation":
            self._g.query(
                "UNWIND $rows AS row "
                "MATCH (s:Span {id: row.span_id}) "
                "MERGE (e:StateEntity {entity_type: row.entity_type, primary_key: row.primary_key}) "
                "MERGE (m:StateMutation {id: row.id}) "
                "SET m.trace_id = row.trace_id, m.span_id = row.span_id, "
                "m.entity_type = row.entity_type, m.primary_key = row.primary_key, "
                "m.field = row.field, m.old_val = row.old_val, m.new_val = row.new_val, "
                "m.ts_ns = row.ts_ns, m.seq_no = row.seq_no, m.lock_held = row.lock_held, "
                "m.function_name = row.function_name "
                "MERGE (s)-[:EMITTED]->(m) "
                "MERGE (m)-[r:MUTATED]->(e) SET r.ts_ns = row.ts_ns",
                {"rows": rows},
            )
        elif kind == "happened_before":
            self._g.query(
                "UNWIND $rows AS row "
                "MATCH (a:StateMutation {id: row.a}) MATCH (b:StateMutation {id: row.b}) "
                "MERGE (a)-[r:HAPPENED_BEFORE]->(b) "
                "SET r.delta_ms = row.delta_ms, r.concurrent_overlap = row.concurrent_overlap",
                {"rows": rows},
            )
        elif kind == "caused_read":
            self._g.query(
                "UNWIND $rows AS row MATCH (m:StateMutation {id: row.mutation_id}) "
                "MATCH (s:Span {id: row.span_id}) MERGE (m)-[:CAUSED_READ]->(s)",
                {"rows": rows},
            )
        elif kind == "entity_access":
            # READ_ENTITY / WROTE_ENTITY from a span onto an entity. This is the
            # bridge edge that makes cross-trace traversal possible at all.
            # Cypher cannot bind a relationship type from a parameter, so it is
            # interpolated -- validated against an allow-list, and the batch is
            # partitioned per type so a mixed batch cannot collapse into one
            # type and silently relabel every write as a read.
            allowed = {"READ_ENTITY", "WROTE_ENTITY"}
            groups: dict[str, list[dict]] = {}
            for row in rows:
                rel = row.get("rel")
                if rel not in allowed:
                    raise ValueError(f"unsupported entity access rel: {rel!r}")
                groups.setdefault(rel, []).append(row)
            for rel, group in groups.items():
                self._g.query(
                    "UNWIND $rows AS row MATCH (s:Span {id: row.span_id}) "
                    "MATCH (e:StateEntity {entity_type: row.entity_type, "
                    "primary_key: row.primary_key}) "
                    f"MERGE (s)-[r:{rel}]->(e) "
                    "SET r.ts_ns = row.ts_ns, r.lock_held = row.lock_held, "
                    "r.tx_isolation = row.tx_isolation, r.snapshot_version = row.snapshot_version",
                    {"rows": group},
                )

    # -- writes -----------------------------------------------------------
    def upsert_trace(self, trace: Trace) -> None:
        self._buf(
            "trace",
            {
                "id": trace.id,
                "entry_endpoint": trace.entry_endpoint,
                "service": trace.service,
                "payload_json": trace.payload_json,
                "start_ns": trace.start_ns,
                "end_ns": trace.end_ns,
                "status": trace.status,
            },
        )

    def upsert_span(self, span: Span) -> None:
        self._buf(
            "span",
            {
                "id": span.id,
                "trace_id": span.trace_id,
                "name": span.name,
                "start_ns": span.start_ns,
                "end_ns": span.end_ns,
                "status": span.status,
                "function_name": span.function_name,
                "file_path": span.file_path,
                "lineno": span.lineno,
                "parent_span_id": span.parent_span_id,
                "error_type": span.error_type,
                "error_message": span.error_message,
            },
        )

    def upsert_entity(self, entity: StateEntity) -> None:
        self._buf(
            "entity",
            {
                "entity_type": entity.entity_type,
                "primary_key": entity.primary_key,
                "key": entity.key,
                "first_seen_ns": entity.first_seen_ns,
            },
        )

    def upsert_function(self, fn: ASTFunction) -> None:
        self._buf(
            "function",
            {
                "qualified_name": self._qname(fn),
                "name": fn.name,
                "file_path": fn.file_path,
                "line_range": list(fn.line_range),
                "is_async": fn.is_async,
            },
        )

    @staticmethod
    def _qname(fn: ASTFunction) -> str:
        return fn.qualified_name or f"{fn.file_path}::{fn.name}"

    def link_trace_span(self, trace_id: str, span_id: str) -> None:
        self._g.query(
            "MATCH (t:Trace {id: $trace_id}) MERGE (s:Span {id: $span_id}) MERGE (t)-[:HAS_SPAN]->(s)",
            {"trace_id": trace_id, "span_id": span_id},
        )

    def link_span_function(self, span_id: str, fn: ASTFunction) -> None:
        self.upsert_function(fn)
        self._buf("span_function", {"span_id": span_id, "qualified_name": self._qname(fn)})

    def link_entity_function(self, entity: StateEntity, fn: ASTFunction) -> None:
        self.upsert_function(fn)
        self.upsert_entity(entity)
        self._buf(
            "entity_function",
            {
                "entity_type": entity.entity_type,
                "primary_key": entity.primary_key,
                "qualified_name": self._qname(fn),
            },
        )

    def record_mutation(self, mutation: StateMutation) -> None:
        self._buf(
            "mutation",
            {
                "id": mutation.id,
                "trace_id": mutation.trace_id,
                "span_id": mutation.span_id,
                "entity_type": mutation.entity_type,
                "primary_key": mutation.primary_key,
                "field": mutation.field,
                "old_val": mutation.old_val,
                "new_val": mutation.new_val,
                "ts_ns": mutation.ts_ns,
                "seq_no": mutation.seq_no,
                "lock_held": mutation.lock_held,
                "function_name": mutation.function_name,
            },
        )

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
        """Create the bridge edge ``(:Span)-[:READ_ENTITY|WROTE_ENTITY]->(:StateEntity)``."""
        self._buf(
            "entity_access",
            {
                "span_id": span_id,
                "entity_type": entity.entity_type,
                "primary_key": entity.primary_key,
                "rel": rel_type,
                "ts_ns": ts_ns,
                "lock_held": lock_held,
                "tx_isolation": tx_isolation,
                "snapshot_version": snapshot_version,
            },
        )

    def link_happened_before(
        self, prev_mutation_id: str, next_mutation_id: str, delta_ms: float, overlap: bool
    ) -> None:
        self._buf(
            "happened_before",
            {
                "a": prev_mutation_id,
                "b": next_mutation_id,
                "delta_ms": float(delta_ms),
                "concurrent_overlap": bool(overlap),
            },
        )

    def link_caused_read(self, mutation_id: str, span_id: str) -> None:
        self._buf("caused_read", {"mutation_id": mutation_id, "span_id": span_id})

    def latest_mutation_id(self, entity: StateEntity) -> str | None:
        rows = self._g.query(
            "MATCH (m:StateMutation {entity_type: $et, primary_key: $pk}) "
            "RETURN m.id AS id ORDER BY m.seq_no DESC, m.ts_ns DESC LIMIT 1",
            {"et": entity.entity_type, "pk": entity.primary_key},
        ).result_set
        return rows[0][0] if rows and rows[0][0] else None

    # -- traversals -------------------------------------------------------
    def find_collisions(
        self, crash_span_id: str, window_ns: int, ignore_trace_id: str = ""
    ) -> list[Interference]:
        if not ignore_trace_id:
            ignore_trace_id = self.trace_of_span(crash_span_id)
        res = self._g.query(
            Q.CROSS_TRACE_COLLISION,
            {
                "crash_span_id": crash_span_id,
                "ignore_trace_id": ignore_trace_id,
                "window_ns": int(window_ns),
            },
        )
        # One request that wrote three fields is one interfering writer, not
        # three. Collapse on (entity, writer trace, writer span) and keep the
        # earliest write, which is the one that opened the race window.
        merged: dict[tuple[str, str, str], Interference] = {}
        for r in res.result_set:
            (etype, pk, other_trace, other_span, other_ep, other_payload, up_fn, up_file,
             crash_fn, crash_file, w_ts, r_ts, locked, hazard) = r
            item = Interference(
                entity_key=f"{_as_str(etype).lower()}:{pk}",
                entity_type=_as_str(etype),
                primary_key=_as_str(pk),
                other_trace_id=_as_str(other_trace),
                other_span_id=_as_str(other_span),
                other_endpoint=_as_str(other_ep),
                other_payload=_as_str(other_payload, "{}"),
                mutator_function=_as_str(up_fn),
                mutator_file=_as_str(up_file),
                crash_function=_as_str(crash_fn),
                crash_file=_as_str(crash_file),
                write_ts_ns=_as_int(w_ts),
                read_ts_ns=_as_int(r_ts),
                lock_held=_as_bool(locked),
                hazard=_as_str(hazard, "RAW"),
            )
            key = (item.entity_key, item.other_trace_id, item.other_span_id)
            existing = merged.get(key)
            if existing is None or item.write_ts_ns < existing.write_ts_ns:
                merged[key] = item
        return sorted(merged.values(), key=lambda i: i.write_ts_ns)

    def trace_of_span(self, span_id: str) -> str:
        rows = self._g.query(
            "MATCH (s:Span {id: $sid}) RETURN s.trace_id AS tid LIMIT 1", {"sid": span_id}
        ).result_set
        return _as_str(rows[0][0]) if rows else ""

    def span_by_id(self, span_id: str) -> Span | None:
        rows = self._g.query(
            "MATCH (s:Span {id: $sid}) RETURN s.id, s.trace_id, s.name, s.start_ns, s.end_ns, "
            "s.status, s.function_name, s.file_path, s.lineno, s.parent_span_id, "
            "s.error_type, s.error_message LIMIT 1",
            {"sid": span_id},
        ).result_set
        if not rows:
            return None
        r = rows[0]
        return Span(
            id=_as_str(r[0]), trace_id=_as_str(r[1]), name=_as_str(r[2]),
            start_ns=_as_int(r[3]), end_ns=_as_int(r[4]), status=_as_str(r[5], "OK"),
            function_name=_as_str(r[6]), file_path=_as_str(r[7]), lineno=_as_int(r[8]),
            parent_span_id=_as_str(r[9]), error_type=_as_str(r[10]),
            error_message=_as_str(r[11]),
        )

    def trace_by_id(self, trace_id: str) -> Trace | None:
        rows = self._g.query(
            "MATCH (t:Trace {id: $tid}) RETURN t.id, t.entry_endpoint, t.service, "
            "t.payload_json, t.start_ns, t.end_ns, t.status LIMIT 1",
            {"tid": trace_id},
        ).result_set
        if not rows:
            return None
        r = rows[0]
        return Trace(
            id=_as_str(r[0]), entry_endpoint=_as_str(r[1]), service=_as_str(r[2]),
            payload_json=_as_str(r[3], "{}"), start_ns=_as_int(r[4]),
            end_ns=_as_int(r[5]), status=_as_str(r[6], "OK"),
        )

    def entry_endpoint_of_span(self, span_id: str) -> str:
        rows = self._g.query(
            "MATCH (t:Trace)-[:HAS_SPAN]->(s:Span {id: $sid}) RETURN t.entry_endpoint LIMIT 1",
            {"sid": span_id},
        ).result_set
        return _as_str(rows[0][0]) if rows else ""

    def error_spans(self) -> list[Span]:
        self.flush()
        rows = self._g.query(
            "MATCH (s:Span) WHERE s.status = 'ERROR' "
            "RETURN s.id, s.trace_id, s.name, s.start_ns, s.end_ns, s.function_name, "
            "s.file_path, s.lineno, s.error_type, s.error_message"
        ).result_set
        return [
            Span(
                id=_as_str(r[0]), trace_id=_as_str(r[1]), name=_as_str(r[2]),
                start_ns=_as_int(r[3]), end_ns=_as_int(r[4]), status="ERROR",
                function_name=_as_str(r[5]), file_path=_as_str(r[6]), lineno=_as_int(r[7]),
                error_type=_as_str(r[8]), error_message=_as_str(r[9]),
            )
            for r in rows
        ]

    def causal_chain(
        self, entity: StateEntity, crash_span_id: str, max_depth: int = 12
    ) -> list[CausalStep]:
        res = self._g.query(
            Q.minimal_replay_subgraph(max_depth),
            {
                "entity_type": entity.entity_type,
                "primary_key": entity.primary_key,
                "crash_span_id": crash_span_id,
            },
        )
        path_ids: list[str] = []
        for r in res.result_set:
            ids = r[0] or []
            if len(ids) > len(path_ids):
                path_ids = list(ids)

        mutation_ids = [i for i in path_ids if isinstance(i, str) and i]
        if not mutation_ids:
            # Crash span never linked into the HAPPENED_BEFORE chain -- fall
            # back to the raw per-entity ordering so the replay stays complete.
            rows = self._g.query(
                "MATCH (m:StateMutation {entity_type: $et, primary_key: $pk}) "
                "RETURN m.id AS id ORDER BY m.seq_no ASC",
                {"et": entity.entity_type, "pk": entity.primary_key},
            ).result_set
            mutation_ids = [_as_str(r[0]) for r in rows]

        if not mutation_ids:
            return []

        detail = self._g.query(Q.REPLAY_DETAIL, {"ids": mutation_ids}).result_set
        by_id: dict[str, dict] = {}
        for r in detail:
            rec = dict(zip(
                ["mutation_id", "seq_no", "ts_ns", "field", "old_val", "new_val",
                 "lock_held", "function_name", "trace_id", "entry_endpoint",
                 "payload_json", "span_id", "span_start_ns"],
                r,
                strict=False,  # FalkorDB may omit trailing optional columns
            ))
            by_id[_as_str(rec["mutation_id"])] = rec

        ordered = [by_id[i] for i in mutation_ids if i in by_id]
        if not ordered:
            return []
        base = _as_int(ordered[0]["ts_ns"])

        steps: list[CausalStep] = []
        for i, rec in enumerate(ordered):
            endpoint = _as_str(rec["entry_endpoint"])
            steps.append(
                CausalStep(
                    index=i,
                    ts_ns=_as_int(rec["ts_ns"]),
                    offset_ms=round((_as_int(rec["ts_ns"]) - base) / NS_PER_MS, 3),
                    trace_id=_as_str(rec["trace_id"]),
                    endpoint=endpoint,
                    kind=self._kind_of(endpoint),
                    payload_json=_as_str(rec["payload_json"], "{}"),
                    field=_as_str(rec["field"]),
                    old_val=_as_str(rec["old_val"]),
                    new_val=_as_str(rec["new_val"]),
                    lock_held=_as_bool(rec["lock_held"]),
                    function_name=_as_str(rec["function_name"]),
                    span_id=_as_str(rec["span_id"]),
                    span_start_ns=_as_int(rec["span_start_ns"], _as_int(rec["ts_ns"])),
                )
            )
        steps.sort(key=lambda s: (s.ts_ns, s.index))
        for i, s in enumerate(steps):
            s.index = i
        return steps

    @staticmethod
    def _kind_of(endpoint: str) -> str:
        return kind_of(endpoint)

    def colliding_trace_count(self, crash_span_id: str, ignore_trace_id: str = "") -> int:
        if not ignore_trace_id:
            ignore_trace_id = self.trace_of_span(crash_span_id)
        rows = self._g.query(
            Q.PRUNE_STATS,
            {"crash_span_id": crash_span_id, "ignore_trace_id": ignore_trace_id},
        ).result_set
        return _as_int(rows[0][0]) if rows else 0

    def entity_writers(self, entity: StateEntity) -> list[dict]:
        """Every span that wrote this entity, with its lock state."""
        self.flush()
        rows = self._g.query(
            Q.ENTITY_WRITERS,
            {"entity_type": entity.entity_type, "primary_key": entity.primary_key},
        ).result_set
        return [
            {
                "span_id": _as_str(r[0]),
                "trace_id": _as_str(r[1]),
                "entry_endpoint": _as_str(r[2]),
                "function_name": _as_str(r[3]),
                "ts_ns": _as_int(r[4]),
                "lock_held": _as_bool(r[5]),
            }
            for r in rows
        ]

    # -- introspection ----------------------------------------------------
    def span_count(self) -> int:
        rows = self._g.query("MATCH (s:Span) RETURN count(s) AS c").result_set
        return _as_int(rows[0][0]) if rows else 0

    def trace_count(self) -> int:
        rows = self._g.query("MATCH (t:Trace) RETURN count(t) AS c").result_set
        return _as_int(rows[0][0]) if rows else 0

    def mutation_count(self) -> int:
        rows = self._g.query("MATCH (m:StateMutation) RETURN count(m) AS c").result_set
        return _as_int(rows[0][0]) if rows else 0

    def stats(self) -> dict:
        rows = self._g.query(
            "MATCH (n) WITH labels(n)[0] AS l, count(n) AS c "
            "RETURN collect([l, c]) AS pairs"
        ).result_set
        counts: dict[str, int] = {}
        for pair in (rows[0][0] if rows and rows[0][0] else []) or []:
            counts[_as_str(pair[0])] = _as_int(pair[1])
        return counts

    def snapshot_to(self, destination_graph: str) -> bool:
        try:
            self._db.execute_command(f"GRAPH.COPY {self.graph_name} {destination_graph}")
            return True
        except Exception:
            return False

    def clear(self) -> None:
        self.flush()
        try:
            self._db.execute_command(f"GRAPH.DELETE {self.graph_name}")
        except Exception:
            pass

    def drop_if_exists(self, graph: str) -> None:
        try:
            self._db.execute_command(f"GRAPH.DELETE {graph}")
        except Exception:
            pass


def wait_until_ready(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 30.0) -> bool:
    """Poll until FalkorDB answers PING (used by the CLI and the demo runner)."""
    import socket

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def iter_batched(items: Iterable, size: int) -> Iterable[list]:
    batch: list = []
    for it in items:
        batch.append(it)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
