"""The storefront app: trace-repro's observation layer around unmodified Oscar."""

import os

from django.apps import AppConfig
from django.db.backends.signals import connection_created


class StorefrontConfig(AppConfig):
    name = "storefront"
    verbose_name = "Storefront (incident instrumentation)"

    def ready(self) -> None:
        from storefront.checkout_hook import arm

        arm()

        # Instrumentation is off unless the demo asks for it, so the shop
        # behaves like an ordinary Oscar site the rest of the time -- and pays
        # no per-query cost when nobody is watching.
        graph_url = os.environ.get("TRACE_REPRO_GRAPH")
        if not graph_url:
            return

        from trace_repro.graph.facade import open_graph
        from trace_repro.ingest.tracer import Telemetry

        from storefront.instrumentation import observe, set_telemetry

        store = open_graph(graph_url)
        set_telemetry(Telemetry(store, service="vaayu-shop"), shared=True)

        # Django gives every thread its own connection, and execute wrappers
        # live on the connection. Registering once at startup would leave the
        # background worker's statements unwatched -- which is precisely the
        # traffic that causes the incident.
        connection_created.connect(_install_observer, dispatch_uid="trace-repro-observer")
        _install_observer(sender=None)


def _install_observer(sender, connection=None, **kwargs) -> None:
    from storefront.instrumentation import observe

    conn = connection
    if conn is None:
        from django.db import connections

        conn = connections["default"]
    if observe not in conn.execute_wrappers:
        conn.execute_wrappers.append(observe)