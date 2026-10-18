"""The end-to-end demonstration.

Runs the whole lifecycle against a live graph and prints the evidence at every
stage. Nothing here is mocked:

* the race is a real concurrent read-modify-write against a real database
* the graph is real FalkorDB (or the in-process graph, if it is not running)
* RED and GREEN are two real ``python -m pytest`` invocations
* the before/after governance proof re-runs the production workload with the
  patch applied and re-reads the graph, so the claim is measured

The patch is always reverted, including when a stage raises: a repair tool that
can leave a repository in a half-modified state is worse than one that does
nothing.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .analysis.triage import TriageEngine
from .contract import load_contract
from .graph.facade import open_graph
from .pipeline.gitlab import GitLabCIAdapter, GitLabClient, is_in_gitlab_ci
from .pipeline.heal import HealPipeline
from .pipeline.workload import purge_modules_under, reproduce
from .repair.patch import RepairResult, revert


def _rule(console, title: str) -> None:
    console.dim("─" * 78)
    console.head(title)
    console.dim("─" * 78)


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _stage_rule(console, ci_adapter, stage_id: str, title: str):
    if ci_adapter:
        return ci_adapter.section(stage_id, title)
    _rule(console, title)
    return _NullContext()



def _reload_target(repo: Path) -> None:
    """Re-import the target service so an on-disk patch is what runs.

    Python caches imported modules, so a file edited on disk stays invisible to
    anything already imported in this process. The GREEN run never sees this
    because it is a fresh ``python -m pytest``; the governance re-run does.
    """
    purge_modules_under(str(repo))


def _fixed_graph_url(graph_url: str, incident_id: str) -> str:
    for scheme in ("falkordb://", "memory://"):
        if graph_url.startswith(scheme):
            return f"{scheme}fixed_{incident_id}"
    return f"{graph_url}_fixed"


def _stray_backups(repo: Path) -> list[Path]:
    return sorted(repo.rglob("*.orig"))


def recover_interrupted_run(repo: Path, console) -> list[Path]:
    """Restore a repository left patched by a run that was killed outright.

    A crash inside the loop is covered by ``try/finally``. A ``SIGKILL``, a
    closed terminal, or a yanked power cable are not -- and the next run would
    then "heal" a repository that is already patched, and prove nothing. The
    ``.orig`` files are the only record of the pre-run state, so use them.
    """
    recovered: list[Path] = []
    for backup in _stray_backups(repo):
        target = backup.with_name(backup.name[: -len(".orig")])
        target.write_text(backup.read_text("utf-8"), encoding="utf-8")
        backup.unlink()
        recovered.append(target)
    if recovered:
        console.warn(
            "recovered an interrupted run: restored "
            + ", ".join(p.name for p in recovered)
        )
    return recovered


def run_demo(args, console) -> int:
    started = time.monotonic()
    repo = Path(args.repo).resolve()
    graph_url = args.graph
    spec = load_contract(repo)

    ci_mode = getattr(args, "ci", "auto")
    in_ci = (ci_mode == "gitlab") or (ci_mode == "auto" and is_in_gitlab_ci())
    ci_adapter = GitLabCIAdapter(console=console) if in_ci else None

    recover_interrupted_run(repo, console)

    store = open_graph(graph_url)
    store.clear()
    console.head(spec.title)
    console.dim(f"scenario   : {spec.key}")
    console.dim(f"graph      : {graph_url}")
    console.dim(f"backend    : {store.name}")
    console.dim(f"repo       : {repo}")
    console.dim(f"contract   : {spec.package} · {spec.entity_key} · "
                f"{len(spec.participants)} participants")
    if in_ci:
        console.dim("ci mode   : gitlab (collapsible sections & reports active)")

    pipeline = HealPipeline(
        store,
        repo,
        engine=TriageEngine(store),
        keep_fix=args.keep,
        ci_adapter=ci_adapter,
        export_reports=getattr(args, "export_reports", None),
    )
    repair: RepairResult | None = None
    diff: dict = {}
    after_status = 0
    after_state = ""
    green_ok = False
    healed = False
    incident = None
    red = None
    green = None
    compiled = None

    try:
        # ---------------- Stage 1 · Monitor ----------------------------
        with _stage_rule(console, ci_adapter, "stage_1_monitor", "Stage 1 · Monitor — reproduce the incident"):
            if ci_adapter:
                _rule(console, "Stage 1 · Monitor — reproduce the incident")
            outcome = reproduce(
                store,
                repo_root=str(repo),
                order_id=getattr(args, "order_id", None),
                background_traces=args.background,
            )
            console.out(f"  concurrent traces recorded : {outcome.background_traces + 3}")
            console.out(f"  spans in graph             : {store.span_count()}")
            console.red(f"  {outcome.crash_endpoint:<27} -> {outcome.http_status} "
                        f"{outcome.error_type}: {outcome.error_message}")
            console.dim(f"  final {spec.entity_key:<22}: {outcome.final_status}")

            if not outcome.crashed:
                console.warn("  the workload did not fail; nothing to heal")
                return 1

        # ---------------- Stage 2 · Plan -------------------------------
        with _stage_rule(console, ci_adapter, "stage_2_plan", "Stage 2 · Plan — cross-trace causal triage"):
            if ci_adapter:
                _rule(console, "Stage 2 · Plan — cross-trace causal triage")
            engine = TriageEngine(store)
            crash = engine.locate_crash(span_id=outcome.crash_span_id, error_type=outcome.error_type)
            if crash is None:
                crash = engine.locate_crash(error_type=outcome.error_type)
            if crash is not None:
                console.dim(f"  stack trace blames : {crash.function_name} "
                            f"({crash.file_path}:{crash.lineno})")
                console.dim(f"  crashing span      : {crash.span_id}")
            else:
                console.dim(f"  stack trace blames : {spec.crash_endpoint}")
                console.dim(f"  crashing span      : {outcome.crash_span_id}")

            incident = engine.triage(
                outcome.crash_span_id,
                incident_id=spec.incident_id,
                error_type=outcome.error_type,
                error_message=outcome.error_message,
            )
            console.out()
            console.out(f"  entity under contention : {incident.entity_key}")
            console.out("  causal window           : "
                        f"{max((c.race_window_ms for c in incident.interferences), default=0):.1f} ms")
            console.out()
            for c in incident.interferences:
                console.out(f"  trace {c.other_trace_id}  {c.other_endpoint:<24} wrote it "
                            f"{c.race_window_ms:6.1f}ms before the crash")
                console.red(f"      unlocked write in {c.mutator_function}()  "
                            f"[{Path(c.mutator_file).name}]")
                console.dim(f"      hazard={c.hazard}  lock_held={c.lock_held}")
            console.out()
            console.green(
                f"  pruned {incident.spans_pruned}/{incident.total_spans_considered} spans "
                f"({incident.pruning_ratio:.2%}) down to "
                f"{len(incident.replay_steps)} causal participants"
            )
            console.out()
            for s in incident.replay_steps:
                console.out(f"    +{s.offset_ms:8.1f}ms  {s.kind:<8} {s.endpoint:<26} "
                            f"{s.function_name}")

        # ---------------- Stage 3 · Verify RED -------------------------
        with _stage_rule(console, ci_adapter, "stage_3_verify_red", "Stage 3 · Verify — compile and run the reproduction (RED)"):
            if ci_adapter:
                _rule(console, "Stage 3 · Verify — compile and run the reproduction (RED)")
            compiled, installed = pipeline.compile_repro(incident)
            for path in installed:
                console.dim(f"  installed {Path(path).relative_to(repo)}")
            console.dim(f"  wrote     {Path(compiled.path).relative_to(repo)}")
            console.dim(f"  replaying {compiled.participants} participants "
                        f"at their measured offsets")
            console.out()

            red = pipeline.run_red(compiled)
            console.red(f"  pipeline #1  exit={red.exit_code}  {red.summary}")
            for line in _failure_lines(red.stdout):
                console.dim(f"      {line}")
            if red.passed:
                console.red("  the reproduction did not fail; the graph missed the bug")
                return 1

        # ---------------- Stage 4 · Create -----------------------------
        with _stage_rule(console, ci_adapter, "stage_4_create", "Stage 4 · Create — repair the upstream mutator"):
            if ci_adapter:
                _rule(console, "Stage 4 · Create — repair the upstream mutator")
            repair = pipeline.apply_fix(incident)
            console.out(f"  {repair.summary}")
            console.out()
            for line in repair.diff.splitlines():
                if line.startswith(("+++", "---")):
                    continue
                if line.startswith("+"):
                    console.green(f"  {line}")
                elif line.startswith("-"):
                    console.red(f"  {line}")
                elif line.startswith("@@"):
                    console.dim(f"  {line}")

        # ---------------- Stage 5 · Verify GREEN -----------------------
        with _stage_rule(console, ci_adapter, "stage_5_verify_green", "Stage 5 · Verify — re-run the same test (GREEN)"):
            if ci_adapter:
                _rule(console, "Stage 5 · Verify — re-run the same test (GREEN)")
            green = pipeline.run_green(compiled)
            green_ok = green.passed
            (console.green if green_ok else console.red)(
                f"  pipeline #2  exit={green.exit_code}  {green.summary}"
            )

        # ---------------- Stage 6 · Govern -----------------------------
        with _stage_rule(console, ci_adapter, "stage_6_govern", "Stage 6 · Govern — before/after graph diff"):
            if ci_adapter:
                _rule(console, "Stage 6 · Govern — before/after graph diff")
            console.dim("  reloading the target service so the patched source is what runs")
            _reload_target(repo)

            fixed_store = open_graph(_fixed_graph_url(graph_url, spec.incident_id))
            fixed_store.clear()
            after = reproduce(fixed_store, repo_root=str(repo),
                              order_id=getattr(args, "order_id", None),
                              background_traces=0)
            after_status, after_state = after.http_status, after.final_status
            diff = pipeline.graph_diff(incident, fixed_store)

            console.out(f"  writers before       : {diff['writers_before']}  "
                        f"lock_held={diff['lock_held_before']}")
            console.out(f"  writers after        : {diff['writers_after']}  "
                        f"lock_held={diff['lock_held_after']}")
            console.out(f"  contended writers    : {', '.join(diff['contended_writers'])}")
            console.out(f"  now locked           : {', '.join(diff['resolved_writers']) or 'none'}")
            console.out(f"  workload replay      : HTTP {after_status}  final state {after_state}")
            console.out(f"  race reproduced      : {diff['race_condition_reproduced']}  ->  "
                        f"resolved {diff['resolved']}")
            if diff["remaining_unlocked"]:
                console.warn(
                    "  advisory: still writes this row without a lock (outside the "
                    f"race window): {', '.join(diff['remaining_unlocked'])}"
                )
            healed = green_ok and diff["resolved"] and after_status < 400
    finally:
        if repair is not None and not args.keep:
            for path in revert(repair):
                console.dim(f"  reverted {Path(path).name}")
            _reload_target(repo)

    export_dir = getattr(args, "export_reports", None) or ("artifacts" if in_ci else None)
    if export_dir and incident and compiled and red:
        from .pipeline.heal import HealResult
        res = HealResult(
            incident=incident,
            compiled=compiled,
            red=red,
            green=green,
            repair=repair,
            graph_diff=diff,
        )
        exporter = ci_adapter or GitLabCIAdapter(console=console)
        exported = exporter.export_all(res, artifacts_dir=export_dir)
        console.dim(f"  exported GitLab CI artifacts to {export_dir}/: "
                    + ", ".join(p.name for p in exported.values()))

    if getattr(args, "create_mr", False) and incident and repair:
        try:
            client = GitLabClient()
            branch = f"heal/{incident.incident_id}"
            client.create_branch(branch, ref=client.env.default_branch)
            actions = [
                {
                    "action": "create",
                    "file_path": str(Path(compiled.path).relative_to(repo)).replace("\\", "/"),
                    "content": compiled.source,
                }
            ]
            client.commit_changes(branch, f"repro({incident.incident_id}): deterministic reproduction test", actions)
            desc = (ci_adapter or GitLabCIAdapter()).generate_mr_description(
                HealResult(incident=incident, compiled=compiled, red=red, green=green, repair=repair, graph_diff=diff)
            )
            mr = client.create_merge_request(
                source_branch=branch,
                target_branch=client.env.default_branch,
                title=f"Resolve Concurrency Incident {incident.incident_id}: Causal Self-Healing",
                description=desc,
            )
            console.green(f"  created GitLab Merge Request !{mr.get('iid')}: {mr.get('web_url')}")
        except Exception as exc:
            console.warn(f"  could not create GitLab Merge Request: {exc}")

    console.dim("─" * 78)
    (console.green if healed else console.red)(
        f"  {spec.incident_id} ({spec.key}) closed · "
        f"lock_held {diff.get('lock_held_before')} -> {diff.get('lock_held_after')} · "
        f"{'healed' if healed else 'NOT healed'} in {time.monotonic() - started:.2f}s"
    )
    console.out()


    if getattr(args, "json", False):
        console.data(json.dumps({
            "incident": spec.incident_id,
            "scenario": spec.key,
            "entity": spec.entity_key,
            "pruning_ratio": round(
                incident.pruning_ratio, 6) if incident is not None else None,
            "red": {"exit_code": red.exit_code, "summary": red.summary},
            "green": {"exit_code": green.exit_code, "summary": green.summary},
            "graph_diff": diff,
            "healed": healed,
        }, indent=2, default=str))

    return 0 if healed else 1


def _failure_lines(stdout: str, limit: int = 6) -> list[str]:
    keep: list[str] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(("AssertionError:", "E  ")) and len(stripped) > 12:
            keep.append(stripped)
    return keep[:limit]
