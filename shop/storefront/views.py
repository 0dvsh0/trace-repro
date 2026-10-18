"""Local shop endpoints.

Everything a customer touches lives in Oscar's own URLconf. These routes exist
only to run the demo: reset the shop between takes, rebuild the in-process
search index, and expose the incident's own state.

They are deliberately plain GETs so the recorded demo can drive them with a
single navigation, and they are idempotent.
"""

from django.http import JsonResponse
from django.urls import path
from oscar.core.loading import get_model

Order = get_model("order", "Order")
Product = get_model("catalogue", "Product")
Basket = get_model("basket", "Basket")


def health(request):
    return JsonResponse({"ok": True, "service": "vaayu-shop"})


def reset(request):
    """Return the shop to a known-good starting state.

    Oscar's default search backend keeps its index in memory, so a catalogue
    created before the server booted is invisible to browse until something
    inside the server process reindexes it. Doing it here -- rather than from a
    management command in a second process -- is the only way to warm it.
    """
    Order = get_model("order", "Order")
    Line = get_model("order", "Line")
    Basket = get_model("basket", "Basket")
    Transaction = get_model("payment", "Transaction")
    Source = get_model("payment", "Source")

    removed = {
        "orders": Order.objects.all().delete()[0],
        "lines": Line.objects.all().delete()[0],
        "baskets": Basket.objects.all().delete()[0],
        "txns": Transaction.objects.all().delete()[0],
        "sources": Source.objects.all().delete()[0],
    }

    StockRecord = get_model("partner", "StockRecord")
    StockRecord.objects.all().update(num_in_stock=1000, num_allocated=0)

    indexed = _reindex_catalogue()

    return JsonResponse({"ok": True, "cleared": removed, "reindexed": indexed})


def _reindex_catalogue() -> int:
    from haystack import connections
    from oscar.apps.search.search_indexes import ProductIndex

    Product = get_model("catalogue", "Product")
    index = ProductIndex()
    count = 0
    for product in Product.objects.all():
        index.update_object(product, using="default")
        count += 1
    # Touch the backend so the in-memory index is definitely materialised.
    try:
        connections["default"].get_backend().search("")
    except Exception:  # noqa: BLE001 - warming the index must never 500
        pass
    return count


def demo_basket(request):
    """Put the flagship product in the basket, using Oscar's own basket.

    ``BasketMiddleware`` has already attached ``request.basket`` with a live
    strategy, so this is the same object every Oscar view would see. The
    customer then drives the real checkout from here.
    """
    from django.http import HttpResponseRedirect

    product = Product.objects.filter(slug="aurex-studio-anc-headphones").first()
    basket = getattr(request, "basket", None)
    if product is not None and basket is not None and basket.is_empty:
        basket.add_product(product, quantity=1)
    return HttpResponseRedirect("/basket/")


def reset_payment_method(request):
    """Forget whatever the last customer chose, so a take starts clean."""
    from django.http import HttpResponseRedirect

    from storefront.payments import gateway_source_type

    gateway_source_type()
    return HttpResponseRedirect("/shop-reset/")


urlpatterns = [
    path("shop-health/", health, name="shop-health"),
    path("shop-reset/", reset, name="shop-reset"),
    path("demo/basket/", demo_basket, name="demo-basket"),
    path("demo/payment-method/", reset_payment_method, name="demo-payment-method"),
]