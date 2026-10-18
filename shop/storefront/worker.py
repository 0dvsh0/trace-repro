"""The cart-expiry reaper.

Every real shop runs a sweep like this: find orders that have been sitting
unpaid past their window and close them so the stock goes back on sale. It is
a plain cron job, so it has no request, no session and no ``trace_id`` in
common with the customer's checkout.

It is also the bug. It reads the order, spends ``REHYDRATE_MS`` tidying up the
basket session, and only then writes its decision -- from the row it read
before any of that time passed. If anyone moves the order in between, this
sweep overwrites their write with a decision made on a row that no longer
exists.
"""

from __future__ import annotations

import time

from django.db import transaction
from oscar.core.loading import get_model

from storefront.instrumentation import work_trace

Order = get_model("order", "Order")
Transaction = get_model("payment", "Transaction")

# Rehydrating the basket from the catalogue and releasing the reservation.
# This has to be slow relative to the gateway call in payments.py: the worker
# starts at +190ms and the gateway returns at +260ms, so a 140ms cleanup puts
# the worker's write at +330ms -- after the authorisation it never saw.
REHYDRATE_MS = 140.0

# What the sweep considers abandoned.
STALE_STATUSES = ("Pending",)


def close_stale_orders(order_pk: int) -> dict:
    """Cancel one order that has been sitting unpaid.

    Read-modify-write with no lock. The read and the write are separated by
    real work, so the value written is decided from a row that may already be
    stale.
    """
    with work_trace("cart-expiry reaper", {"job": "cart-expiry", "order_pk": order_pk}):
        # The transaction wraps the slow cleanup as well as the decision, which
        # is the shape that produces this bug: the row was read before the
        # work started and is written after it finished.
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order_pk)
            _rehydrate_basket(order_pk)
            if order.status not in STALE_STATUSES:
                return order.status
            order.status = "Cancelled"
            order.save()
            return order.status


def _rehydrate_basket(order_pk: int) -> None:
    time.sleep(REHYDRATE_MS / 1000.0)


def sweep_stale_orders(limit: int = 50) -> list[str]:
    """What the cron entry point actually calls.

    Finds every order that still looks abandoned and hands each one to
    :func:`close_stale_orders`.
    """
    pks = list(
        Order.objects.filter(status__in=STALE_STATUSES)
        .exclude(pk__in=_orders_being_paid())
        .values_list("pk", flat=True)[:limit]
    )
    return [close_stale_orders(pk) for pk in pks]


def _orders_being_paid() -> set[str]:
    """Orders with a live payment in flight are not abandoned.

    This is the check that is supposed to stop the sweep racing a customer,
    and it is exactly the kind of guard that a timing bug makes useless: it is
    consulted before the slow cleanup work, and by the time the sweep writes,
    the payment it was protecting has already started.
    """
    from storefront.payments import orders_in_flight

    return orders_in_flight()


__all__ = ["REHYDRATE_MS", "STALE_STATUSES", "close_stale_orders", "sweep_stale_orders"]
