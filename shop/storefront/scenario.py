"""Driving the incident.

The customer's click is the trigger. When Oscar finishes writing the order,
this module starts the three participants at the offsets recorded in
``incident.json``:

    +0ms    gateway authorisation   reads Pending, 260ms at the provider, writes Placed
    +190ms  cart-expiry reaper      reads Pending, 140ms of cleanup,     writes Cancelled
    +560ms  order confirmation      reads the row, finds Cancelled,      HTTP 500

The reaper's write lands last, so the payment the customer completed is
discarded by a decision made from a row it read 140ms earlier. The
confirmation step is not the bug -- it is correct code refusing to settle an
order that is no longer in a state it can settle.
"""

from __future__ import annotations

import json
import threading
import time
from decimal import Decimal
from pathlib import Path

from oscar.core.loading import get_model

from storefront.payments import apply_authorisation, finalise_order
from storefront.worker import close_stale_orders

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "incident.json"

Order = get_model("order", "Order")
Basket = get_model("basket", "Basket")
Transaction = get_model("payment", "Transaction")

# Filled in by load_contract().
CONTRACT: dict = {}


def load_contract(path: Path | None = None) -> dict:
    global CONTRACT
    CONTRACT = json.loads(Path(path or CONTRACT_PATH).read_text("utf-8"))
    return CONTRACT


def participants() -> list[dict]:
    return load_contract()["participants"]


def _offset_ms(name: str) -> float:
    for entry in participants():
        if entry["name"] == name:
            return float(entry["offset_ms"])
    raise LookupError(name)


# --------------------------------------------------------------------------
# the incident
# --------------------------------------------------------------------------


def run_race(order_pk: int, *, event_id: str = "evt_100012",
             amount_paise: int = 450_000) -> dict:
    """Run the two colliding writers. Raises nothing.

    Split out from :func:`run_incident` because Oscar wraps its
    ``order_placed`` signal in a try/except that logs and continues -- so a
    failure raised from inside the signal would be swallowed and the customer
    would still see a thank-you page. The collision has to happen here, but the
    *failure* has to be raised later, from outside that guard.
    """
    condition = threading.Condition()
    ready = [False]
    outcomes: dict[str, str] = {}
    base_time = [0.0]

    def sleep_until(offset_ms: float) -> None:
        remaining = (base_time[0] + offset_ms / 1000.0) - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    def reaper_sweep() -> None:
        with condition:
            ready[0] = True
            condition.notify_all()
        with condition:
            while base_time[0] == 0.0:
                condition.wait(0.1)
        sleep_until(_offset_ms("cart-expiry-reaper"))
        outcomes["reaper"] = close_stale_orders(order_pk)

    reaper = threading.Thread(target=reaper_sweep, name="cart-expiry-reaper")
    reaper.start()

    with condition:
        while not ready[0]:
            condition.wait(0.1)
        base_time[0] = time.monotonic()
        condition.notify_all()

    outcomes["gateway"] = apply_authorisation(order_pk, event_id, amount_paise)

    sleep_until(_offset_ms("order-confirmation"))
    reaper.join(timeout=5)
    return outcomes


def run_incident(order_pk: int, *, event_id: str = "evt_100012",
                 amount_paise: int = 450_000) -> dict:
    """Run the collision and return what the shop ended up with.

    The reaper runs on its own thread because it is a cron sweep: it has no
    request and no session in common with the customer. That is not a
    simulation detail, it is the reason this bug is invisible in production.
    """
    outcomes = run_race(order_pk, event_id=event_id, amount_paise=amount_paise)

    try:
        outcomes["confirmation"] = finalise_order(order_pk)
        outcomes["crashed"] = False
    except Exception as exc:  # noqa: BLE001 - the customer sees this
        outcomes["confirmation"] = f"{type(exc).__name__}: {exc}"
        outcomes["crashed"] = True

    order = Order.objects.get(pk=order_pk)
    outcomes["final_status"] = order.status
    return outcomes


def duplicate_delivery(order_pk: int, *, event_id: str = "evt_100012",
                       amount_paise: int = 450_000) -> dict:
    """The provider retries until it gets a 2xx, so events arrive twice."""
    apply_authorisation(order_pk, event_id, amount_paise)
    apply_authorisation(order_pk, event_id, amount_paise)
    return {
        "settlements_for_one_event": Transaction.objects.filter(reference=event_id).count(),
        "status": Order.objects.get(pk=order_pk).status,
    }


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def seed_order(number: str, amount_paise: int = 450_000, title: str = "Aurex Studio ANC Headphones"):
    """Create the order row the customer is about to pay for.

    The order is created directly rather than through a basket: the incident is
    entirely about this one row, and Oscar only assigns a strategy to a basket
    when it is built inside a request.

    Returns the primary key. Every participant addresses the row by pk so the
    graph has one entity for this order rather than one per natural key.
    """
    order = Order.objects.filter(number=number).first()
    if order is not None:
        return order.pk
    basket = Basket.objects.create()
    return Order.objects.create(
        number=number,
        basket=basket,
        status="Pending",
        currency="INR",
        total_incl_tax=Decimal(amount_paise) / 100,
        total_excl_tax=Decimal(amount_paise) / 100,
        guest_email="devansh@example.com",
    ).pk


__all__ = ["duplicate_delivery", "load_contract", "participants", "run_incident", "seed_order"]