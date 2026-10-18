"""Run the incident and print what the shop ended up with.

    python manage.py run_incident --graph falkordb://oscar_incident

Exit code is 0 when the customer got a good outcome and 1 when they did not,
which is what makes this usable as a CI check as well as a demo driver.
"""

import json

from django.core.management.base import BaseCommand

from storefront.scenario import duplicate_delivery, run_incident, seed_order


class Command(BaseCommand):
    help = "Run the lost-update incident against a freshly seeded order."

    def add_arguments(self, parser):
        parser.add_argument("--order", default="100012")
        parser.add_argument("--amount", type=int, default=450_000)
        parser.add_argument("--duplicate", action="store_true",
                            help="also replay the provider's at-least-once retry")
        parser.add_argument("--reseed", action="store_true", default=True)
        parser.add_argument("--no-reseed", dest="reseed", action="store_false")
        parser.add_argument("--debug-sql", action="store_true",
                            help="log statements and dump the tail if the run fails")

    def handle(self, *args, **options):
        number = options["order"]
        from django.conf import settings
        from django.db import connection

        if options["debug_sql"]:
            settings.DEBUG = True
            connection.force_debug_cursor = True
        try:
            self._run(number, options)
        except Exception as exc:
            # PostgreSQL reports the *next* statement after a failure as
            # "current transaction is aborted", which hides the real error.
            # Walk the cause chain so the actual violation is visible.
            chain, seen = [], set()
            node = exc
            while node is not None and id(node) not in seen:
                seen.add(id(node))
                chain.append(f"{type(node).__module__}.{type(node).__name__}: {node}")
                node = node.__cause__ or node.__context__
            self.stderr.write("--- exception chain ---")
            for link in chain:
                self.stderr.write(link[:400])
            if options["debug_sql"]:
                self.stderr.write("--- last statements ---")
                for q in connection.queries[-25:]:
                    self.stderr.write(f"{q['sql'][:200]}  {q.get('params')}")
            raise

    def _run(self, number, options):
        from storefront.instrumentation import get_telemetry

        telemetry = get_telemetry()
        self.stdout.write(f"instrumentation: {'ON ' + type(telemetry.store).__name__ if telemetry else 'OFF'}")
        if options["reseed"]:
            _clear(number)
        order_pk = seed_order(number, options["amount"])

        outcomes = run_incident(order_pk, amount_paise=options["amount"])
        if options["duplicate"]:
            outcomes["duplicate_delivery"] = duplicate_delivery(order_pk)
        if telemetry is not None:
            telemetry.flush()
            outcomes["graph"] = {
                "spans": telemetry.store.span_count(),
                "traces": telemetry.store.trace_count(),
            }

        self.stdout.write(json.dumps(outcomes, indent=2, default=str))
        ok = not outcomes.get("crashed") and outcomes.get("final_status") == "Placed"
        if ok:
            self.stdout.write(self.style.SUCCESS("customer outcome: PAID and PLACED"))
        else:
            self.stdout.write(
                self.style.ERROR(
                    f"customer outcome: {outcomes.get('final_status')} "
                    f"(crashed={outcomes.get('crashed')})"
                )
            )


def _clear(number: str) -> None:
    """Wipe the shop's order state so every run starts from the same place.

    Oscar's Transaction has no order FK, so settlements are matched by the
    event reference the provider sent.
    """
    from oscar.core.loading import get_model

    Order = get_model("order", "Order")
    Transaction = get_model("payment", "Transaction")
    Source = get_model("payment", "Source")
    Basket = get_model("basket", "Basket")
    Line = get_model("order", "Line")

    Transaction.objects.all().delete()
    Source.objects.all().delete()
    Line.objects.all().delete()
    Order.objects.all().delete()
    Basket.objects.all().delete()
