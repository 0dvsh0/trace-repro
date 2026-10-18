"""Collect the real evidence behind ``demo/demo.html``.

    python demo/capture.py

Nothing here invents a value. Every number, span, mutation, graph edge and
JSON-RPC response the demo page shows is read back out of a live
``trace-repro`` run:

1. **The product, as an engineer runs it** — ``trace-repro demo`` in a
   subprocess, captured stage by stage. The console text is what the terminal
   screenshot in ``demo/assets/`` shows, and this is the same text, so the
   page can quote and link it.
2. **The evidence underneath** — the same workload run in-process against a
   store this script owns, so the spans, the field-level state mutations, the
   causal graph and the MCP tools (what GitLab Duo Agent queries) can be read
   out of the graph rather than inferred from console output.

Output: ``demo/data/demo-data.js`` (loaded by the page, so it also works over
``file://``). Screenshots are captured separately by ``demo/screenshot.py`` —
this script draws nothing.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEMO = Path(__file__).resolve().parent
REPO_ROOT = DEMO.parent
ASSETS = DEMO / "assets"
DATA = DEMO / "data"
OUT_JS = DATA / "demo-data.js"

sys.path.insert(0, str(DEMO))
sys.path.insert(0, str(REPO_ROOT / "src"))

import showcase  # noqa: E402  (sibling module: the console parser)
from trace_repro.analysis.triage import TriageEngine  # noqa: E402
from trace_repro.contract import load_contract  # noqa: E402
from trace_repro.graph.memory import MemoryStore  # noqa: E402
from trace_repro.mcp.server import MCPServer  # noqa: E402
from trace_repro.pipeline.workload import reproduce  # noqa: E402
from trace_repro.schema import (  # noqa: E402
    EMITTED,
    MUTATED,
)

BACKGROUND_TRACES = 250


# --------------------------------------------------------------------------
# Pass 1 — the product console
# --------------------------------------------------------------------------


def capture_console(name: str, args) -> showcase.Parsed:
    """Run ``trace-repro demo`` and capture every stage it prints."""
    repo = DEMO / name
    cmd = [
        sys.executable, "-m", "trace_repro.cli", "demo",
        "--repo", str(repo),
        "--graph", f"memory://capture_{name}",
        "--background", str(args.background),
        "--export-reports", str(DEMO / "artifacts" / name),
    ]
    parsed = showcase.Parsed()
    started = time.monotonic()
    proc = subprocess.Popen(
        cmd, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    assert proc.stdout is not None
    for raw in proc.stdout:
        showcase._parse_line(raw.rstrip(), parsed)
    proc.wait()
    parsed.duration = time.monotonic() - started
    parsed.ok = proc.returncode == 0
    parsed.stages.setdefault("closed", []).append(
        ("green", f"  {parsed.closed_line or 'closed'}"))
    return parsed


# --------------------------------------------------------------------------
# Pass 2 — the evidence underneath
# --------------------------------------------------------------------------


def capture_deep(name: str) -> dict:
    """Re-run the workload in-process and read the store it wrote to."""
    repo = DEMO / name
    spec = load_contract(repo)
    store = MemoryStore()
    outcome = reproduce(store, repo_root=str(repo),
                        background_traces=BACKGROUND_TRACES)

    engine = TriageEngine(store)
    crash = engine.locate_crash()
    incident = engine.triage(
        crash.span_id,
        incident_id=spec.incident_id,
        error_type=outcome.error_type,
        error_message=outcome.error_message,
    )

    entity = store.entities[spec.entity_key]
    mutations = sorted(
        store.mutations_for(entity_type=spec.entity_type, primary_key=spec.primary_key),
        key=lambda m: m.seq_no,
    )
    writers = store.entity_writers(entity)
    base_ns = min((m.ts_ns for m in mutations), default=0)

    return {
        "outcome": {
            "crash_endpoint": outcome.crash_endpoint,
            "crash_span_id": outcome.crash_span_id,
            "crash_trace_id": outcome.crash_trace_id,
            "http_status": outcome.http_status,
            "error_type": outcome.error_type,
            "error_message": outcome.error_message,
            "final_state": outcome.final_status,
            "participants": outcome.participants,
            "background_traces": outcome.background_traces,
        },
        "counts": {
            "traces": store.trace_count(),
            "spans": store.span_count(),
            "mutations": len(store.mutations),
            "relations": store.rel_count(),
            "entities": len(store.entities),
        },
        "crash": {
            "span_id": crash.span_id,
            "trace_id": crash.trace_id,
            "function": crash.function_name,
            "file": crash.file_path,
            "lineno": crash.lineno,
            "error_type": crash.error_type,
        },
        "incident": {
            "incident_id": incident.incident_id,
            "entity_key": incident.entity_key,
            "upstream_mutator": incident.upstream_mutator,
            "spans_pruned": incident.spans_pruned,
            "total_spans": incident.total_spans_considered,
            "pruning_ratio": round(incident.pruning_ratio, 4),
            "interferences": [
                {
                    "trace_id": c.other_trace_id,
                    "endpoint": c.other_endpoint,
                    "function": c.mutator_function,
                    "file": c.mutator_file,
                    "window_ms": round(c.race_window_ms, 1),
                    "lock_held": c.lock_held,
                    "hazard": c.hazard,
                    "write_ts_ns": c.write_ts_ns,
                }
                for c in incident.interferences
            ],
        },
        "spans": [
            {
                "id": s.id,
                "trace_id": s.trace_id,
                "name": s.name,
                "function": s.function_name,
                "file": Path(s.file_path).name if s.file_path else "",
                "lineno": s.lineno,
                "start_ns": s.start_ns,
                "duration_ms": round(s.duration_ms, 2),
                "status": s.status,
                "error_type": s.error_type,
                "error_message": s.error_message,
            }
            for s in _participant_spans(store, outcome)
        ],
        "mutations": [
            {
                "seq_no": m.seq_no,
                "trace_id": m.trace_id,
                "span_id": m.span_id,
                "function": m.function_name,
                "field": m.field,
                "old": m.old_val or "—",
                "new": m.new_val or "—",
                "offset_ms": round((m.ts_ns - base_ns) / 1e6, 1),
                "lock_held": m.lock_held,
            }
            for m in mutations
        ],
        "writers": [
            {**w, "offset_ms": round((w["ts_ns"] - base_ns) / 1e6, 1)}
            for w in writers
        ],
        "graph": extract_graph(store, outcome, mutations),
        "mcp": run_mcp(store, crash.span_id, spec),
    }


def _participant_spans(store: MemoryStore, outcome) -> list:
    """The spans that belong to the incident traces, in time order."""
    trace_ids = set(outcome.participants.values())
    spans = [s for s in store.spans.values() if s.trace_id in trace_ids]
    return sorted(spans, key=lambda s: s.start_ns)


def extract_graph(store: MemoryStore, outcome, mutations: list) -> dict:
    """Real nodes and edges, read through the public traversal API.

    Node ids are stable and carry the raw identifiers the page needs to line
    a node up with a span, a trace or a mutation from the telemetry dump.
    """
    nodes: list[dict] = []
    edges: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    crash_trace = outcome.crash_trace_id

    def node(nid, label, kind, **props):
        if (nid, "", "n") in seen:
            return
        seen.add((nid, "", "n"))
        nodes.append({"id": nid, "label": label, "kind": kind, **props})

    def edge(src, dst, rel, **props):
        if (src, dst, rel) in seen:
            return
        seen.add((src, dst, rel))
        edges.append({"src": src, "dst": dst, "rel": rel, **props})

    ent_key = (f"{mutations[0].entity_type}:{mutations[0].primary_key}"
               if mutations else "")
    node("E", ent_key, "entity", entity_key=ent_key,
         primary_key=mutations[0].primary_key if mutations else "")

    span_of_trace: dict[str, str] = {}
    for trace_id in outcome.participants.values():
        trace = store.trace_by_id(trace_id)
        if trace is None:
            continue
        node(f"T:{trace_id}", trace.entry_endpoint,
             "victim_trace" if trace_id == crash_trace else "trace",
             trace_id=trace_id, payload=trace.payload_json)
        for span_id in store.neighbours("Trace", trace_id, "HAS_SPAN"):
            span = store.span_by_id(span_id)
            if span is None:
                continue
            sid = f"S:{span_id}"
            node(sid, span.function_name or span.name,
                 "victim_span" if trace_id == crash_trace else "span",
                 span_id=span_id, trace_id=trace_id,
                 trace=trace.entry_endpoint)
            span_of_trace[trace_id] = sid
            edge(f"T:{trace_id}", sid, "HAS_SPAN")

    for m in mutations:
        mid = f"M:{m.id}"
        node(mid, f"{m.field} → {m.new_val}", "mutation",
             mutation_id=m.id, span_id=m.span_id, trace_id=m.trace_id,
             seq=m.seq_no, lock_held=m.lock_held, offset_ms=round(
                 (m.ts_ns - mutations[0].ts_ns) / 1e6, 1) if mutations else 0)
        edge(f"S:{m.span_id}", mid, EMITTED)
        edge(mid, "E", MUTATED)

    return {"nodes": nodes, "edges": edges, "span_of_trace": span_of_trace}


def run_mcp(store: MemoryStore, crash_span_id: str, spec) -> list[dict]:
    """Drive the real MCP server GitLab Duo Agent talks to.

    ``memory://`` stores do not cross process boundaries, so the session is
    played against the same store the workload wrote to — same
    ``MCPServer.handle`` code path, same payloads, over the same JSON-RPC
    2.0 wire format.
    """
    server = MCPServer(store)
    transcript: list[dict] = []

    def call(req: dict) -> dict | None:
        resp = server.handle(req)
        transcript.append({"request": req, "response": resp})
        return resp

    call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                     "clientInfo": {"name": "gitlab-duo-agent", "version": "1.0"}}})
    server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    call({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
          "params": {"name": "locate_crash", "arguments": {}}})
    call({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
          "params": {"name": "get_cross_trace_collision_graph",
                     "arguments": {"crash_span_id": crash_span_id}}})
    call({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
          "params": {"name": "minimal_replay_subgraph",
                     "arguments": {"crash_span_id": crash_span_id}}})
    call({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
          "params": {"name": "upstream_mutator",
                     "arguments": {"entity_type": spec.entity_type,
                                   "primary_key": spec.primary_key}}})
    call({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
          "params": {"name": "graph_stats", "arguments": {}}})
    call({"jsonrpc": "2.0", "id": 8, "method": "shutdown"})
    return transcript


def _screenshots_for(name: str) -> list[str]:
    """Which real screenshots exist for this scenario right now."""
    folder = ASSETS / name
    if not folder.exists():
        return []
    return sorted(f"assets/{name}/{p.name}" for p in folder.glob("*.png"))


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="build the demo evidence pack")
    ap.add_argument("--background", type=int, default=BACKGROUND_TRACES,
                    help="unrelated traces recorded per scenario")
    ap.add_argument("--only", default=None, help="capture one scenario only")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

    names = [args.only] if args.only else list(showcase.SCENARIOS)
    incidents = []
    started = time.monotonic()

    for name in names:
        print(f"[{name}] pass 1 — the product console ...", flush=True)
        parsed = capture_console(name, args)
        print(f"[{name}] pass 2 — the evidence underneath ...", flush=True)
        deep = capture_deep(name)

        spec = showcase.SCENARIOS[name]
        incidents.append({
            "key": name,
            "sev": spec["sev"],
            "clock": spec["clock"],
            "service": spec["service"],
            "entity": spec["entity"],
            "hook": spec["hook"],
            "short": spec["short"],
            "story": spec["story"],
            "notes": _notes(name),
            "healed": parsed.ok,
            "duration": round(parsed.duration, 2),
            "metrics": {
                "spans": parsed.spans,
                "pruned": parsed.pruned,
                "pruned_pct": parsed.pruned_pct,
                "causal": parsed.causal,
                "window_ms": round(parsed.window_ms, 1),
                "red": parsed.red_summary,
                "green": parsed.green_summary,
                "lock_before": parsed.lock_before,
                "lock_after": parsed.lock_after,
                "replay_http": parsed.replay_http,
                "replay_state": parsed.replay_state,
                "resolved": parsed.resolved == "True",
            },
            "timeline": parsed.replay_steps,
            "collisions": parsed.collisions,
            "deep": deep,
            "shots": _screenshots_for(name),
        })
        print(f"[{name}] collected ({parsed.duration:.2f}s engine run)", flush=True)

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command": "python demo/capture.py",
        "showcase": "python demo/showcase.py",
        "capture_seconds": round(time.monotonic() - started, 1),
        "background_traces": args.background,
        "incidents": incidents,
    }
    DATA.mkdir(parents=True, exist_ok=True)
    OUT_JS.write_text(
        "/* Generated by demo/capture.py from real runs. Re-run to refresh. */\n"
        f"window.TR_DEMO = {json.dumps(payload, ensure_ascii=True, indent=2)};\n",
        encoding="utf-8",
    )
    print(f"\nwrote {OUT_JS.relative_to(REPO_ROOT)} "
          f"({len(incidents)} incidents, {payload['capture_seconds']}s)")
    return 0


def _notes(name: str) -> list[str]:
    """The bug class this scenario stands for — breadth, not just payments."""
    return {
        "checkout": [
            "Class: lost update (write-after-write) on a shared row.",
            "Why it matters: the payment was captured, then a worker overwrote the row — the customer paid for a cancelled order.",
            "Where else this lives: booking, seat reservation, wallet top-up, profile edits.",
        ],
        "inventory": [
            "Class: oversell (read-after-write against a stale snapshot).",
            "Why it matters: 53 released units were zeroed by a sweep that read the lot before the receipt committed; the API answered 409 the whole time.",
            "Where else this lives: ticket inventory, seat allocation, quota counters, coupon limits.",
        ],
        "payments": [
            "Class: duplicate delivery (at-least-once webhook) inside a lost update.",
            "Why it matters: the retry shares no trace id with the original — only the row links them, and the ledger ends up with two captures for one payment.",
            "Where else this lives: order webhooks, email/SMS dispatch, refund retries, ledger postings.",
        ],
    }[name]


if __name__ == "__main__":
    raise SystemExit(main())
