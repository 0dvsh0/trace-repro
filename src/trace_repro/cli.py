"""``trace-repro`` command line interface.

Subcommands mirror the lifecycle rather than the modules, because the point of
the tool is the loop, not the API:

``graph``     open a graph and report what is in it
``triage``    given a crashing span, find the cross-trace collisions
``compile``   extract the minimal subgraph and write the reproduction test
``repair``    plan (or apply) the upstream fix
``heal``      run the whole loop: red -> patch -> green
``demo``      drive one scenario repository end to end
``mcp``       serve the graph over MCP (stdio)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .analysis.triage import TriageEngine
from .codegen.repro import TargetProfile, install_harness, write_test
from .graph.facade import DEFAULT_GRAPH_URL, open_graph
from .models import Incident
from .repair.patch import RepairError, apply_incident
from .schema import DEFAULT_CAUSAL_WINDOW_MS

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------


class Console:
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    def __init__(self, stream=None, colour: bool | None = None, quiet: bool = False):
        # Resolve sys.stdout lazily: a test harness that swaps the stream after
        # import would otherwise write into the real terminal.
        self._stream = stream
        self._colour = colour
        # --json must emit exactly one parseable document, with no banner.
        self.quiet = quiet

    @property
    def stream(self):
        return _utf8(self._stream if self._stream is not None else sys.stdout)

    @property
    def colour(self) -> bool:
        if self._colour is not None:
            return self._colour
        return self.stream.isatty()

    def _c(self, text: str, *codes: str) -> str:
        if not self.colour or not codes:
            return text
        return "".join(codes) + text + self.RESET

    def out(self, text: str = "") -> None:
        if not self.quiet:
            print(text, file=self.stream)

    def data(self, text: str) -> None:
        """Write a machine-readable payload even in quiet mode."""
        print(text, file=self.stream)

    def dim(self, text: str) -> None:
        self.out(self._c(text, self.DIM))

    def red(self, text: str) -> None:
        self.out(self._c(text, self.RED))

    def green(self, text: str) -> None:
        self.out(self._c(text, self.GREEN))

    def warn(self, text: str) -> None:
        self.out(self._c(text, self.YELLOW))

    def head(self, text: str) -> None:
        self.out(self._c(text, self.BOLD, self.CYAN))

    def err(self, text: str) -> None:
        print(text, file=_utf8(sys.stderr))


def _utf8(stream):
    """Force UTF-8 output.

    Windows consoles still default to a legacy code page, which turns the box
    drawing characters into a UnicodeEncodeError and kills the run. Replacing
    unencodable characters is better than refusing to print a report.
    """
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass
    return stream


def incident_from_graph(args, console: Console) -> tuple:
    """Open the graph, locate the crash and triage it."""
    store = open_graph(args.graph)
    engine = TriageEngine(store, causal_window_ms=args.window_ms)
    console.dim(f"graph     : {args.graph}  (backend={store.name})")
    console.dim(f"nodes     : {store.span_count()} spans / {store.trace_count()} traces")

    if args.crash_span:
        crash_span_id = args.crash_span
    else:
        candidate = engine.locate_crash(error_type=args.error_type)
        if candidate is None:
            raise SystemExit("no ERROR span found in the graph; nothing to triage")
        crash_span_id = candidate.span_id
        console.dim(f"crash     : {candidate.span_id} {candidate.function_name} "
                    f"{candidate.error_type}")

    incident = engine.triage(
        crash_span_id,
        incident_id=args.incident,
        error_type=args.error_type or "",
        error_message=args.error_message or "",
    )
    return store, engine, incident


def print_incident(incident: Incident, console: Console) -> None:
    console.head("incident")
    console.out(f"  id            : {incident.incident_id}")
    console.out(f"  entity        : {incident.entity_key}")
    console.out(f"  crash         : {incident.crash_function} ({incident.crash_file})")
    console.out(f"  upstream      : {incident.upstream_mutator}")
    console.out(f"  spans pruned  : {incident.spans_pruned}/{incident.total_spans_considered} "
                f"({incident.pruning_ratio:.2%})")

    console.head("cross-trace collisions")
    if not incident.interferences:
        console.out("  none")
    for c in incident.interferences:
        console.out(
            f"  {console.DIM}{c.other_trace_id}{console.RESET if console.colour else ''} "
            f"{c.other_endpoint:<24} wrote {c.entity_key} "
            f"{c.race_window_ms:6.1f}ms before the crash  "
            f"hazard={c.hazard} lock_held={c.lock_held} fn={c.mutator_function}"
        )

    console.head("replay plan")
    for s in incident.replay_steps:
        console.out(f"  +{s.offset_ms:8.1f}ms  {s.kind:<8} {s.endpoint:<26} {s.function_name}")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_graph(args, console: Console) -> int:
    store = open_graph(args.graph)
    if args.reset:
        store.clear()
        console.dim("graph cleared")
    stats = store.stats() if hasattr(store, "stats") else {}
    console.head(store.name)
    console.out(f"  spans        : {store.span_count()}")
    console.out(f"  traces       : {store.trace_count()}")
    if hasattr(store, "mutation_count"):
        console.out(f"  mutations    : {store.mutation_count()}")
    for label, count in sorted(stats.items()):
        console.out(f"  {label:<13}: {count}")
    if args.json:
        console.data(json.dumps(
            {"backend": store.name, "spans": store.span_count(),
             "traces": store.trace_count(), "nodes": stats}, indent=2))
    return EXIT_OK


def cmd_triage(args, console: Console) -> int:
    _store, _engine, incident = incident_from_graph(args, console)
    print_incident(incident, console)
    if args.json:
        console.data(json.dumps(_incident_dict(incident), indent=2, default=str))
    return EXIT_OK


def cmd_compile(args, console: Console) -> int:
    store, _engine, incident = incident_from_graph(args, console)
    print_incident(incident, console)

    profile = TargetProfile.for_repo(args.repo, incident)
    installed = install_harness(profile, force=args.force)
    for path in installed:
        console.dim(f"installed {path}")

    compiled = write_test(incident, profile)
    console.head("compiled")
    console.out(f"  test     : {compiled.path}")
    console.out(f"  replays  : {compiled.participants} participants")
    if args.print_source:
        console.out()
        console.out(compiled.source)
    if args.json:
        console.data(json.dumps({"path": str(compiled.path),
                                "participants": compiled.participants}, indent=2))
    return EXIT_OK


def cmd_repair(args, console: Console) -> int:
    store, _engine, incident = incident_from_graph(args, console)
    try:
        result = apply_incident(store, incident, dry_run=args.dry_run)
    except RepairError as exc:
        console.red(f"repair failed: {exc}")
        return EXIT_FAIL
    console.head("repair plan")
    for plan in result.plans:
        console.out(f"  {plan.function_name}  ({plan.file_path})")
        for note in plan.notes:
            console.dim(f"    - {note}")
        for edit in plan.edits:
            console.out(f"    [{edit.strategy}] line {edit.line}")
            console.dim(f"      - {edit.before.strip()}")
            console.green(f"      + {edit.after.strip().replace(chr(10), chr(10) + '        ')}")
            console.dim(f"      why: {edit.rationale}")
    if result.diff:
        console.head("diff")
        for line in result.diff.splitlines():
            if line.startswith("+"):
                console.green(line)
            elif line.startswith("-"):
                console.red(line)
            else:
                console.dim(line)
    console.head("result")
    console.out(f"  {result.summary}")
    if result.files_changed:
        console.out("  changed: " + ", ".join(result.files_changed))
    return EXIT_OK


def cmd_heal(args, console: Console) -> int:
    from .pipeline.gitlab import GitLabCIAdapter, GitLabClient, is_in_gitlab_ci
    from .pipeline.heal import HealPipeline

    ci_mode = getattr(args, "ci", "auto")
    in_ci = (ci_mode == "gitlab") or (ci_mode == "auto" and is_in_gitlab_ci())
    ci_adapter = GitLabCIAdapter(console=console) if in_ci else None

    store, engine, incident = incident_from_graph(args, console)
    print_incident(incident, console)

    export_dir = getattr(args, "export_reports", None) or ("artifacts" if in_ci else None)
    pipeline = HealPipeline(
        store,
        args.repo,
        engine=engine,
        keep_fix=args.keep,
        ci_adapter=ci_adapter,
        export_reports=export_dir,
    )
    result = pipeline.run(incident, skip_repair=args.skip_repair)

    console.head("pipeline #1  RED")
    console.red(f"  exit={result.red.exit_code if result.red else '-'}  {result.red.summary if result.red else '-'}")
    console.head("repair")
    console.out(f"  {result.repair.summary if result.repair else 'skipped'}")
    if result.repair and result.repair.diff:
        for line in result.repair.diff.splitlines():
            if line.startswith("+"):
                console.green(line)
            elif line.startswith("-"):
                console.red(line)
    console.head("pipeline #2  GREEN")
    (console.green if result.healed else console.red)(
        f"  exit={result.green.exit_code if result.green else '-'}  {result.green.summary if result.green else '-'}"
    )
    if result.reverted:
        console.dim("  reverted: " + ", ".join(result.reverted))

    if export_dir and result:
        exported = (ci_adapter or GitLabCIAdapter(console=console)).export_all(result, artifacts_dir=export_dir)
        console.dim(f"  exported GitLab CI artifacts to {export_dir}/: " + ", ".join(p.name for p in exported.values()))

    if getattr(args, "create_mr", False) and result and result.repair:
        try:
            client = GitLabClient()
            branch = f"heal/{incident.incident_id}"
            client.create_branch(branch, ref=client.env.default_branch)
            actions = [
                {
                    "action": "create",
                    "file_path": str(Path(result.compiled.path).relative_to(args.repo)).replace("\\", "/"),
                    "content": result.compiled.source,
                }
            ]
            client.commit_changes(branch, f"repro({incident.incident_id}): deterministic reproduction test", actions)
            desc = (ci_adapter or GitLabCIAdapter()).generate_mr_description(result)
            mr = client.create_merge_request(
                source_branch=branch,
                target_branch=client.env.default_branch,
                title=f"Resolve Concurrency Incident {incident.incident_id}: Causal Self-Healing",
                description=desc,
            )
            console.green(f"  created GitLab Merge Request !{mr.get('iid')}: {mr.get('web_url')}")
        except Exception as exc:
            console.warn(f"  could not create GitLab Merge Request: {exc}")

    console.head("result")
    console.out(f"  reproduced : {result.reproduced}")
    console.out(f"  healed     : {result.healed}")
    return EXIT_OK if result.success else EXIT_FAIL



def cmd_demo(args, console: Console) -> int:
    from .demo import run_demo

    return run_demo(args, console)


def cmd_mcp(args, console: Console) -> int:
    from .mcp.server import serve

    store = open_graph(args.graph)
    serve(store)
    return EXIT_OK


def _incident_dict(incident: Incident) -> dict:
    return {
        "incident_id": incident.incident_id,
        "entity_key": incident.entity_key,
        "crash_function": incident.crash_function,
        "crash_file": incident.crash_file,
        "upstream_mutator": incident.upstream_mutator,
        "pruning": {
            "total_spans": incident.total_spans_considered,
            "pruned": incident.spans_pruned,
            "ratio": round(incident.pruning_ratio, 6),
        },
        "interferences": [
            {
                "trace_id": c.other_trace_id,
                "endpoint": c.other_endpoint,
                "function": c.mutator_function,
                "file": c.mutator_file,
                "race_window_ms": round(c.race_window_ms, 3),
                "lock_held": c.lock_held,
                "hazard": c.hazard,
            }
            for c in incident.interferences
        ],
        "replay": [
            {"offset_ms": s.offset_ms, "kind": s.kind, "endpoint": s.endpoint}
            for s in incident.replay_steps
        ],
    }


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trace-repro",
        description="Autonomous cross-trace bug reproducer & causal self-healing engine",
    )
    parser.add_argument("--version", action="version", version=f"trace-repro {__version__}")
    parser.add_argument("--no-colour", action="store_true", help="disable ANSI output")
    sub = parser.add_subparsers(dest="command", required=True)

    # Accepted before *and* after the subcommand; nobody should have to remember
    # argparse's ordering rules to turn colour off.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--no-colour", action="store_true", default=argparse.SUPPRESS,
                        help="disable ANSI output")

    def graph_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--graph", default=DEFAULT_GRAPH_URL,
                       help="graph URL, e.g. falkordb://incident_8812 or memory://local")

    def triage_args(p: argparse.ArgumentParser) -> None:
        graph_args(p)
        p.add_argument("--incident", default=None, help="incident id (default inc_<pk>)")
        p.add_argument("--crash-span", default=None, help="explicit crashing span id")
        p.add_argument("--error-type", default=None, help="match the error class")
        p.add_argument("--error-message", default=None)
        p.add_argument("--window-ms", type=int, default=DEFAULT_CAUSAL_WINDOW_MS,
                       help="causal window in milliseconds")
        p.add_argument("--repo", default=".", help="target repository root")
        p.add_argument("--json", action="store_true")

    def ci_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--ci", choices=["auto", "local", "gitlab"], default="auto",
                       help="CI adapter to use (auto detects GitLab CI)")
        p.add_argument("--export-reports", nargs="?", const="artifacts", default=None,
                       help="export JUnit and Code Quality reports to directory (default: artifacts)")
        p.add_argument("--create-mr", action="store_true",
                       help="open a GitLab Merge Request with the reproduction test and AST fix")

    p_graph = sub.add_parser("graph", parents=[common], help="inspect a graph")
    graph_args(p_graph)
    p_graph.add_argument("--reset", action="store_true", help="delete the graph first")
    p_graph.add_argument("--json", action="store_true")
    p_graph.set_defaults(func=cmd_graph)

    p_triage = sub.add_parser("triage", parents=[common], help="find cross-trace collisions for a crash")
    triage_args(p_triage)
    p_triage.set_defaults(func=cmd_triage)

    p_compile = sub.add_parser("compile", parents=[common], help="write the reproduction test")
    triage_args(p_compile)
    p_compile.add_argument("--print-source", action="store_true")
    p_compile.add_argument("--force", action="store_true", help="overwrite the installed harness")
    p_compile.set_defaults(func=cmd_compile)

    p_repair = sub.add_parser("repair", parents=[common], help="plan or apply the upstream fix")
    triage_args(p_repair)
    p_repair.add_argument("--dry-run", action="store_true", default=True)
    p_repair.add_argument("--apply", dest="dry_run", action="store_false")
    p_repair.set_defaults(func=cmd_repair)

    p_heal = sub.add_parser("heal", parents=[common], help="run red -> fix -> green")
    triage_args(p_heal)
    ci_args(p_heal)
    p_heal.add_argument("--keep", action="store_true", help="keep the patch on disk")
    p_heal.add_argument("--skip-repair", action="store_true")
    p_heal.set_defaults(func=cmd_heal)

    p_demo = sub.add_parser("demo", parents=[common],
                            help="run one scenario repository end to end")
    graph_args(p_demo)
    ci_args(p_demo)
    p_demo.add_argument("--repo", default="demo/checkout",
                        help="scenario repository (any directory publishing incident.json)")
    p_demo.add_argument("--order-id", type=int, default=None,
                        help="override the contended primary key from incident.json")
    p_demo.add_argument("--background", type=int, default=250,
                        help="unrelated traces to record (measures pruning)")
    p_demo.add_argument("--keep", action="store_true", help="keep the patch on disk")
    p_demo.add_argument("--json", action="store_true")
    p_demo.set_defaults(func=cmd_demo)

    p_mcp = sub.add_parser("mcp", parents=[common], help="serve graph tools over MCP (stdio)")
    graph_args(p_mcp)
    p_mcp.set_defaults(func=cmd_mcp)


    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console(
        colour=False if getattr(args, "no_colour", False) else None,
        quiet=bool(getattr(args, "json", False)),
    )
    try:
        return int(args.func(args, console))
    except KeyboardInterrupt:
        console.err("interrupted")
        return 130
    except (RepairError, LookupError, ValueError) as exc:
        console.err(f"trace-repro: {exc}")
        return EXIT_FAIL


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
