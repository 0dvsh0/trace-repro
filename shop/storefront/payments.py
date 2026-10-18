"""The shop's payment integration.

Two real behaviours of a real payment provider are modelled here, and both are
the source of the incident:

* **the gateway is slow** -- an authorisation round-trip is real latency, and
  the shop must not hold a stale copy of the order across it;
* **delivery is at-least-once** -- providers retry until they get a 2xx, so
  the same ``event_id`` can arrive twice and must not be applied twice.

``apply_authorisation`` is the read-modify-write that gets clobbered.
``finalise_order`` is the victim: it is correct code refusing to settle an
order that is no longer in a state it can settle.
"""

from __future__ import annotations

import time
from decimal import Decimal

from django.db import transaction
from oscar.core.loading import get_model

from storefront.instrumentation import work_trace

Order = get_model("order", "Order")
Transaction = get_model("payment", "Transaction")
Source = get_model("payment", "Source")
SourceType = get_model("payment", "SourceType")

GATEWAY_CODE = "vaayu-gateway"


def gateway_source_type() -> "SourceType":
    """The merchant account this shop settles through."""
    source_type, _ = SourceType.objects.get_or_create(
        code=GATEWAY_CODE, defaults={"name": "Vaayu Gateway"}
    )
    return source_type


def source_for(order) -> "Source":
    """The payment source recorded against one order.

    Oscar requires every transaction to hang off a source, and a source
    belongs to the order it is funding -- which is also what makes the
    duplicate-delivery bug visible: two settlements for one event are two
    money movements on one order.
    """
    source, _ = Source.objects.get_or_create(
        order=order, defaults={"source_type": gateway_source_type(), "currency": order.currency}
    )
    return source

# The window in which a concurrent writer can observe a stale order and clobber
# it. Real gateways are routinely this slow; it is the size of the window that
# decides whether the bug reproduces or merely happens sometimes.
GATEWAY_LATENCY_MS = 260.0

# States the shop is willing to settle. A cancelled order is not one of them.
SETTLEABLE_STATUSES = ("Pending", "Being placed", "Placed")

# Orders with a payment in flight. The reaper consults this so it never closes
# an order the shop is actively charging for.
_IN_FLIGHT: set[str] = set()


def mark_in_flight(order_pk: int) -> None:
    _IN_FLIGHT.add(order_pk)


def orders_in_flight() -> set[int]:
    return set(_IN_FLIGHT)


class InvalidOrderStatus(RuntimeError):
    """The order is not in a state this step is allowed to settle."""


def apply_authorisation(order_pk: int, event_id: str, amount_paise: int) -> dict:
    """Apply the provider's authorisation to an order.

    Reads the order, spends ``GATEWAY_LATENCY_MS`` talking to the provider,
    then writes the result back. There is no row lock across that gap, which
    is exactly the window the reaper slips through.
    """
    with work_trace("POST /payments/authorisation", {"order_pk": order_pk,
                                                    "event_id": event_id}):
        mark_in_flight(order_pk)
        # A transaction, but no row lock -- so it serialises nothing against
        # the reaper. READ COMMITTED means the copy this reads can still be
        # stale by the time it is written back.
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order_pk)
            _call_provider(amount_paise)
            order.status = "Placed"
            order.save()
        # At-least-once delivery: the provider retries until it gets a 2xx, so the
        # same reference can legitimately arrive more than once. This records a
        # settlement per delivery, which is what most integrations do -- and
        # which moves the customer's money twice for one authorisation.
        Transaction.objects.get_or_create(source=source_for(order), reference=event_id, defaults=dict(amount=Decimal(amount_paise) / 100, status='settled', txn_type='settle'))
        _IN_FLIGHT.discard(order_pk)
        return order.status


def finalise_order(order_pk: int) -> dict:
    """Settle the order once the payment has come back.

    This is the victim. It reads the row the reaper clobbered and refuses,
    which is what the customer sees as a 500 on a payment they completed.
    """
    with work_trace("POST /checkout/confirm", {"order_pk": order_pk}):
        order = Order.objects.get(pk=order_pk)
        if order.status not in SETTLEABLE_STATUSES:
            raise InvalidOrderStatus(
                f"Order #{order_pk} is {order.status}; it cannot be settled"
            )
        _send_confirmation(order)
        return order.status


def _call_provider(amount_paise: int) -> str:
    time.sleep(GATEWAY_LATENCY_MS / 1000.0)
    return "authorised" if amount_paise > 0 else "declined"


def _send_confirmation(order) -> None:
    # Real shops mail a confirmation and allocate stock here; both are slow
    # enough to widen the window between reading the order and trusting it.
    time.sleep(0.05)


__all__ = [
    "GATEWAY_LATENCY_MS",
    "InvalidOrderStatus",
    "apply_authorisation",
    "finalise_order",
]

