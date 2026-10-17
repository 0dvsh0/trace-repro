"""MCP server exposing the causal graph.

Implements the Model Context Protocol JSON-RPC surface over stdio, by hand and
without a dependency, because the whole point is that the agent gets the graph
and nothing else: no source files, no shell, no ability to invent a fix. The
agent can *see* the collision and *read* the ordered replay; the repair itself
is produced by the AST planner, not by the model.

Supported methods: ``initialize``, ``tools/list``, ``tools/call``, ``ping``,
``shutdown``.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from ..analysis.triage import TriageEngine
from ..graph.store import GraphStore
from ..schema import DEFAULT_CAUSAL_WINDOW_MS

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "trace-repro"
SERVER_VERSION = "0.1.0"

INSTRUCTIONS = (
    "Cross-trace debugging over a FalkorDB property graph. Spans are grouped by "
    "trace_id, so a stateful race is invisible to trace-scoped tools; these "
    "tools traverse (:StateEntity) bridges to find which OTHER traces mutated "
    "the same row. Never propose a fix from a stack trace alone -- call "
    "get_cross_trace_collision_graph first, then compile_reproduction_test, and "
    "only repair the function the graph names."
)


def _text(content: str, **extra: Any) -> dict:
    return {"content": [{"type": "text", "text": content}], **extra}


def _json(payload: Any) -> dict:
    return _text(json.dumps(payload, indent=2, default=str))


class ToolError(RuntimeError):
    pass


class MCPServer:
    """A very small MCP stdio server bound to one graph."""

    def __init__(self, store: GraphStore, causal_window_ms: int = DEFAULT_CAUSAL_WINDOW_MS):
        self.store = store
        self.engine = TriageEngine(store, causal_window_ms=causal_window_ms)
        self.handlers: dict[str, Callable[[dict], dict]] = {
            "get_cross_trace_collision_graph": self._collision,
            "minimal_replay_subgraph": self._replay,
            "locate_crash": self._locate_crash,
            "upstream_mutator": self._upstream,
            "graph_stats": self._stats,
            "index_code": self._index_code,
        }
        self.tool_schemas = _build_tool_schemas()

    # -- MCP plumbing -----------------------------------------------------
    def handle(self, message: dict) -> dict | None:
        method = message.get("method")
        mid = message.get("id")
        try:
            if method == "initialize":
                result: dict = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    "instructions": INSTRUCTIONS,
                }
            elif method in ("notifications/initialized", "initialized"):
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.tool_schemas}
            elif method == "tools/call":
                result = self._call_tool(message.get("params") or {})
            elif method == "shutdown":
                result = {}
            else:
                raise ToolError(f"unknown method: {method!r}")
        except ToolError as exc:
            return _error(mid, -32601, str(exc))
        except Exception as exc:  # noqa: BLE001 - surface anything to the client
            return _error(mid, -32603, f"{type(exc).__name__}: {exc}")

        if mid is None:
            return None
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def _call_tool(self, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = self.handlers.get(name)
        if handler is None:
            raise ToolError(f"unknown tool: {name!r}")
        return handler(arguments)

    def serve_forever(self, stdin=None, stdout=None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                stdout.write(json.dumps(_error(None, -32700, "parse error")) + "\n")
                stdout.flush()
                continue
            response = self.handle(message)
            if response is not None:
                stdout.write(json.dumps(response) + "\n")
                stdout.flush()

    # -- tools ------------------------------------------------------------
    def _collision(self, args: dict) -> dict:
        span_id = _require(args, "crash_span_id")
        self.store.flush() if hasattr(self.store, "flush") else None
        crash_trace = self.store.trace_of_span(span_id)  # type: ignore[attr-defined]
        collisions = self.store.find_collisions(
            span_id, self.engine.window_ns, ignore_trace_id=crash_trace
        )
        if not collisions:
            return _json({
                "crash_span_id": span_id,
                "collisions": [],
                "verdict": "no cross-trace collision; this crash is not state-interference shaped",
            })
        return _json({
            "crash_span_id": span_id,
            "verdict": (
                f"{len(collisions)} independent trace(s) mutated "
                f"{collisions[0].entity_key} before this crash"
            ),
            "collisions": [
                {
                    "entity": c.entity_key,
                    "other_trace_id": c.other_trace_id,
                    "other_endpoint": c.other_endpoint,
                    "other_payload": json.loads(c.other_payload or "{}"),
                    "upstream_mutator": c.mutator_function,
                    "upstream_mutator_file": c.mutator_file,
                    "downstream_crash_symptom": c.crash_function,
                    "race_window_ms": round(c.race_window_ms, 3),
                    "lock_held": c.lock_held,
                    "hazard": c.hazard,
                }
                for c in collisions
            ],
            "next": "call compile_reproduction_test to get a failing pytest",
        })

    def _replay(self, args: dict) -> dict:
        span_id = _require(args, "crash_span_id")
        self.store.flush() if hasattr(self.store, "flush") else None
        engine = self.engine
        collisions = self.store.find_collisions(span_id, engine.window_ns)
        if not collisions:
            raise ToolError(f"no collision found for span {span_id!r}")
        from ..models import StateEntity
        from .triage_helpers import build_replay  # local import keeps the module light

        entity = StateEntity(collisions[0].entity_type, collisions[0].primary_key)
        crash_trace = self.store.trace_of_span(span_id)  # type: ignore[attr-defined]
        steps, crash_ts, payload = build_replay(
            self.store, entity, span_id, crash_trace, engine.max_depth
        )
        return _json({
            "entity": entity.key,
            "crash_span_id": span_id,
            "replay": [
                {
                    "offset_ms": s.offset_ms,
                    "kind": s.kind,
                    "endpoint": s.endpoint,
                    "function": s.function_name,
                    "payload": json.loads(s.payload_json or "{}"),
                    "writes": f"{s.field}: {s.old_val or '-'} -> {s.new_val or '-'}",
                    "lock_held": s.lock_held,
                }
                for s in steps
            ],
            "crash_ts_ns": crash_ts,
            "crash_payload": json.loads(payload or "{}"),
        })

    def _locate_crash(self, args: dict) -> dict:
        self.store.flush() if hasattr(self.store, "flush") else None
        candidates = self.engine.crash_spans()
        if args.get("error_type"):
            candidates = [c for c in candidates if c.error_type == args["error_type"]]
        return _json({
            "error_spans": [
                {
                    "span_id": c.span_id,
                    "trace_id": c.trace_id,
                    "function": c.function_name,
                    "file": c.file_path,
                    "error_type": c.error_type,
                    "error_message": c.error_message,
                }
                for c in candidates
            ],
            "count": len(candidates),
        })

    def _upstream(self, args: dict) -> dict:
        from ..models import StateEntity

        entity = StateEntity(_require(args, "entity_type"), _require(args, "primary_key"))
        span_id = args.get("crash_span_id")
        collisions = self.store.find_collisions(span_id, self.engine.window_ns) if span_id else []
        writers = [
            {"function": c.mutator_function, "file": c.mutator_file, "lock_held": c.lock_held}
            for c in collisions
        ]
        unlocked = sorted({w["function"] for w in writers if not w["lock_held"] and w["function"]})
        return _json({
            "entity": entity.key,
            "writers": writers,
            "must_repair_all_unlocked_writers": unlocked,
            "note": (
                "A lost update is only fixed when every writer of the row takes "
                "the lock. Repairing only the first one leaves the others free to clobber it."
            ),
        })

    def _stats(self, args: dict) -> dict:
        self.store.flush() if hasattr(self.store, "flush") else None
        payload = {
            "backend": self.store.name,
            "spans": self.store.span_count(),
            "traces": self.store.trace_count(),
        }
        if hasattr(self.store, "mutation_count"):
            payload["mutations"] = self.store.mutation_count()
        if hasattr(self.store, "stats"):
            payload["nodes"] = self.store.stats()
        return _json(payload)

    def _index_code(self, args: dict) -> dict:
        from ..astmap.indexer import index_to_store

        root = args.get("root", ".")
        functions = index_to_store(self.store, root)
        return _json({
            "root": root,
            "indexed_functions": len(functions),
            "note": "EXECUTED_FUNCTION edges can now resolve spans to source lines",
        })


def _require(args: dict, key: str) -> str:
    value = args.get(key)
    if not value:
        raise ToolError(f"missing required argument: {key!r}")
    return str(value)


def _error(mid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _build_tool_schemas() -> list[dict]:
    def tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
        return {
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        }

    span = {"type": "string", "description": "Span id of the crashing operation"}
    return [
        tool(
            "get_cross_trace_collision_graph",
            "Given a crashing span, find every OTHER independent trace that mutated "
            "the same stateful entity inside the causal window. This is the question "
            "a trace_id lookup cannot answer.",
            {"crash_span_id": span},
            ["crash_span_id"],
        ),
        tool(
            "minimal_replay_subgraph",
            "shortestPath over HAPPENED_BEFORE | CAUSED_READ: the ordered participants, "
            "payloads and millisecond offsets needed to reproduce the race.",
            {"crash_span_id": span},
            ["crash_span_id"],
        ),
        tool(
            "locate_crash",
            "List spans whose status is ERROR, optionally filtered by error class.",
            {
                "error_type": {"type": "string", "description": "e.g. InvalidStateError"},
            },
            [],
        ),
        tool(
            "upstream_mutator",
            "Which unlocked functions wrote this entity. Every one of them must be "
            "repaired, not just the first.",
            {
                "entity_type": {"type": "string", "description": 'e.g. "orders"'},
                "primary_key": {"type": "string", "description": 'e.g. "8812"'},
                "crash_span_id": span,
            },
            ["entity_type", "primary_key"],
        ),
        tool(
            "graph_stats",
            "Node counts for the connected graph.",
            {},
            [],
        ),
        tool(
            "index_code",
            "Index a source tree into (:ASTFunction) nodes so spans resolve to files.",
            {"root": {"type": "string", "description": "repository root", "default": "."}},
            [],
        ),
    ]


def serve(store: GraphStore) -> None:  # pragma: no cover - process entry point
    MCPServer(store).serve_forever()
