"""The deterministic reproduction.

Compiled from the causal subgraph rather than written by hand: the three
participants, their payloads and their start offsets all come from what the
graph recorded in the live incident. Replaying them at those offsets against
the real services and the real PostgreSQL row reproduces the lost update every
time, and stops reproducing once every writer takes the row lock.

Run it with::

    python -m pytest shop/tests/test_incident_100012.py -v
"""

import os
import sys
import threading
import time
from decimal import Decimal

import pytest

pytestmark = pytest.mark.django_db(transaction=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "shopfront.settings")

import django  # noqa: E402

django.setup()

from storefront.payments import apply_authorisation, finalise_order  # noqa: E402
from storefront.scenario import _offset_ms, participants  # noqa: E402
from storefront.worker import close_stale_orders  # noqa: E402

ORDER_NUMBER = "100012"
ORDER_PK = "26"          # replaced per-test by the fixture
AMOUNT_PAISE = 450_000


def _barrier(parties: int, timeout: float = 10.0):
    """One-shot rendezvous so the interleaving starts from a known state."""
    condition = threading.Condition()
    state = {"n": 0, "gen": 0}

    def wait() -> None:
        with condition:
            state["n"] += 1
            if state["n"] >= parties:
                state["gen"] += 1
                condition.notify_all()
                return
            generation = state["gen"]
            deadline = time.monotonic() + timeout
            while generation == state["gen"]:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("replay barrier timed out")
                condition.wait(remaining)

    return wait


@pytest.fixture()
def order_row():
    from oscar.core.loading import get_model

    from storefront.scenario import seed_order

    Order = get_model("order", "Order")
    Transaction = get_model("payment", "Transaction")
    Source = get_model("payment", "Source")
    Line = get_model("order", "Line")
    Basket = get_model("basket", "Basket")

    Transaction.objects.all().delete()
    Source.objects.all().delete()
    Line.objects.all().delete()
    Order.objects.all().delete()
    Basket.objects.all().delete()

    yield seed_order(ORDER_NUMBER, AMOUNT_PAISE)


def test_a_paid_order_is_never_cancelled(order_row):
    """The invariant a customer actually cares about.

    The customer paid INR 4,500. Whatever else happens, the order they paid
    for must not end up cancelled by a background job that never saw the
    payment.
    """
    from oscar.core.loading import get_model

    Order = get_model("order", "Order")
    names = [p["name"] for p in participants()]
    rendezvous = _barrier(len(names))
    base = time.monotonic()
    results: dict[str, object] = {}

    def at(offset_ms: float) -> None:
        remaining = (base + offset_ms / 1000.0) - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    def participant(entry):
        def run():
            rendezvous()
            at(entry["offset_ms"])
            if entry["name"] == "gateway-authorisation":
                results["gateway"] = apply_authorisation(
                    order_row, entry["payload"]["event_id"], AMOUNT_PAISE
                )
            elif entry["name"] == "cart-expiry-reaper":
                results["reaper"] = close_stale_orders(order_row)
            else:
                try:
                    results["confirmation"] = finalise_order(order_row)
                except Exception as exc:  # noqa: BLE001 - the failure under test
                    results["confirmation"] = f"{type(exc).__name__}: {exc}"

        return run

    threads = [
        threading.Thread(target=participant(entry), name=entry["name"])
        for entry in participants()
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    final = Order.objects.get(pk=order_row)

    assert final.status != "Cancelled", (
        f"lost update still wins: order {ORDER_NUMBER} is Cancelled "
        f"after the customer paid (participants: {results})"
    )
    assert final.status in ("Placed", "Being placed", "Pending"), (
        f"unexpected final state: {final.status}"
    )


def test_one_authorisation_event_settles_once(order_row):
    """Payment providers retry. One event must move the money once."""
    from oscar.core.loading import get_model

    Transaction = get_model("payment", "Transaction")

    apply_authorisation(order_row, "evt_100012", AMOUNT_PAISE)
    apply_authorisation(order_row, "evt_100012", AMOUNT_PAISE)

    assert Transaction.objects.filter(reference="evt_100012").count() == 1, (
        "the same authorisation event was settled more than once"
    )