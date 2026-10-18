"""Wiring the incident into Oscar's real checkout.

When the demo is armed, completing a checkout in the browser is what triggers
the collision: Oscar places the order for real, then the three participants run
at their recorded offsets and the confirmation step refuses, which is the 500
the customer sees on a payment they had already completed.

The shop is not modified to do this -- Oscar sends ``order_placed`` after its
transaction has committed, and a signal receiver is a supported extension
point.
"""

from __future__ import annotations

import os

from oscar.apps.order.signals import order_placed

# The demo arms this explicitly; an ordinary shop never sets it.
ARMED = os.environ.get("TRACE_REPRO_ARMED") == "1"


def arm() -> None:
    """Run the collision whenever an order is placed in this process."""
    if not ARMED:
        return
    # connect() is idempotent per dispatch_uid. Checking `receivers` for
    # emptiness would be wrong: Oscar connects its own handlers, so the list is
    # never empty and this handler would silently never register.
    order_placed.connect(
        _on_order_placed, dispatch_uid="trace-repro-incident", weak=False
    )


def _on_order_placed(sender, order, **kwargs) -> None:
    """Start the collision against the order the customer just placed.

    Only the two racing writers run here. Oscar wraps this signal in a
    try/except that logs and continues, so anything raised from inside it would
    be swallowed and the customer would still be shown a thank-you page. The
    failure is raised later, from the checkout view, which sits outside that
    guard.
    """
    from storefront.scenario import run_race

    run_race(order.pk, amount_paise=int(order.total_incl_tax * 100))


__all__ = ["arm"]