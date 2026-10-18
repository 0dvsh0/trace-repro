"""The whole loop, end to end, against the real shop.

    python manage.py heal_oscar --graph falkordb://oscar_demo

1. replay the incident into FalkorDB and triage it across traces
2. repair every unlocked writer with the AST planner
3. re-run the compiled reproduction tests
4. revert, so the working tree is left exactly as it was found

Exit code is 0 only if the incident was reproduced *and* the same tests then
pass. A green run that never reproduced anything proves nothing, and is
treated as a failure here on purpose.
"""

import json
import subprocess
import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from storefront.scenario import duplicate_delivery, run_incident, seed_order

TESTS = "tests/test_incident_100012.py"
# shop/storefront/management/commands/heal_oscar.py -> shop/
SHOP_ROOT = Path(__file__).resolve().parents[3]


class Command(BaseCommand):
    help = "Reproduce, triage, repair and re-verify the oscar incident."

    def add_arguments(self, parser):
        parser.add_argument("--graph", default="falkordb://oscar_demo")
        parser.add_argument("--order", default="100012")
        parser.add_argument("--amount", type=int, default=450_000)
        parser.add_argument("--keep", action="store_true", help="keep the patch on disk")

    def handle(self, *args, **options):
        from trace_repro.analysis.triage import TriageEngine
        from trace_repro.graph.facade import open_graph
        from trace_repro.repair.patch import apply_incident, revert

        from storefront.instrumentation import get_telemetry

        telemetry = get_telemetry()
        if telemetry is None:
            self.stderr.write(
                self.style.ERROR(
                    "instrumentation is off; start with TRACE_REPRO_GRAPH set "
                    "or the graph will be empty and nothing can be triaged"
                )
            )
            raise CommandError("instrumentation is off; start with TRACE_REPRO_GRAPH set")
        store = telemetry.store

        self.stdout.write(self.style.MIGRATE_HEADING("1 · monitor — reproduce in production"))
        _reset(options["order"])
        order_pk = seed_order(options["order"], options["amount"])
        outcome = run_incident(order_pk, amount_paise=options["amount"])
        duplicate_delivery(order_pk, amount_paise=options["amount"])
        telemetry.flush()
        self.stdout.write(json.dumps(outcome, indent=2))
        reproduced = bool(outcome.get("crashed")) and outcome.get("final_status") == "Cancelled"
        if not reproduced:
            self.stderr.write(self.style.ERROR("the workload did not fail; nothing to heal"))
            raise CommandError("the workload did not fail; nothing to heal")

        self.stdout.write(self.style.MIGRATE_HEADING("2 · plan — cross-trace causal triage"))
        engine = TriageEngine(store)
        crash = engine.locate_crash(error_type="InvalidOrderStatus")
        if crash is None:
            raise CommandError("no crash span in the graph; nothing was recorded as failing")
        self.stdout.write(f"  stack trace blames : {crash.function_name} ({crash.file_path}:{crash.lineno})")
        incident = engine.triage(
            crash.span_id,
            incident_id="oscar_100012",
            error_type="InvalidOrderStatus",
            error_message=crash.error_message,
        )
        self.stdout.write(f"  entity under contention : {incident.entity_key}")
        self.stdout.write(f"  pruned {incident.spans_pruned}/{incident.total_spans_considered} spans")
        for i in incident.interferences:
            self.stdout.write(
                f"    trace {i.other_trace_id}  {i.other_endpoint:<28} wrote it "
                f"{i.race_window_ms:6.1f}ms before the crash  "
                f"hazard={i.hazard} lock_held={i.lock_held} fn={i.mutator_function}"
            )

        self.stdout.write(self.style.MIGRATE_HEADING("3 · verify — compiled reproduction (RED)"))
        red = _pytest()
        self.stdout.write(f"  exit={red.returncode}")
        for line in red.stdout.strip().splitlines()[-4:]:
            self.stdout.write(f"    {line}")

        self.stdout.write(self.style.MIGRATE_HEADING("4 · create — repair every unlocked writer"))
        result = apply_incident(store, incident, dry_run=False)
        for line in result.diff.splitlines():
            if line.startswith(("+++", "---", "@@")):
                continue
            self.stdout.write(("  + " + line) if line.startswith("+") else
                              ("  - " + line) if line.startswith("-") else
                              ("  " + line))
        self.stdout.write(f"  strategies: {', '.join(result.strategies)}")

        try:
            self.stdout.write(self.style.MIGRATE_HEADING("5 · verify — same tests after the fix (GREEN)"))
            green = _pytest()
            self.stdout.write(f"  exit={green.returncode}")
            for line in green.stdout.strip().splitlines()[-4:]:
                self.stdout.write(f"    {line}")
            healed = green.returncode == 0
        finally:
            if not options["keep"]:
                for path in revert(result):
                    self.stdout.write(f"  reverted {Path(path).name}")
                _drop_caches()

        self.stdout.write("")
        if healed and reproduced:
            self.stdout.write(self.style.SUCCESS(
                f"oscar_100012 (lost_update) closed · "
                f"{', '.join(result.strategies)} · reproduced then healed"
            ))
            return None
        self.stderr.write(self.style.ERROR("NOT healed"))
        raise CommandError("reproduced, repaired, and the tests still fail")


def _pytest():
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            TESTS,
            "-p",
            "no:cacheprovider",
            "-q",
            "-o",
            "django_db_use_migrations=false",
            "--override-ini=django_settings_module=shopfront.settings",
        ],
        cwd=SHOP_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode not in (0, 1):
        # 4 = pytest usage error: the run proved nothing, so surface why.
        proc.stdout = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return proc


def _reset(number: str) -> None:
    """Wipe order state; returns nothing, the caller seeds explicitly."""
    from oscar.core.loading import get_model

    for app, model in (("payment", "Transaction"), ("payment", "Source"),
                       ("order", "Line"), ("order", "Order"), ("basket", "Basket")):
        get_model(app, model).objects.all().delete()


def _drop_caches() -> None:
    """Forgets the repaired source so the next import reads it from disk."""
    import importlib
    import sys

    from trace_repro.ingest.tracer import clear_function_cache

    for name in [n for n in sys.modules if n.startswith("storefront.")]:
        importlib.reload(sys.modules[name])
    clear_function_cache()