"""Throwaway probe: create one order and print exactly what the database says."""

from decimal import Decimal

from django.core.management.base import BaseCommand
from oscar.core.loading import get_model


class Command(BaseCommand):
    help = "Create a single order, printing the real database error."

    def handle(self, *args, **options):
        Order = get_model("order", "Order")
        Basket = get_model("basket", "Basket")
        for name in ("payment.Source", "payment.SourceType", "payment.Transaction"):
            app, model = name.split(".")
            print(name, [f.name for f in get_model(app, model)._meta.get_fields()])
        print("non-null fields without defaults:")
        for f in Order._meta.get_fields():
            if hasattr(f, "null") and not f.is_relation:
                print(f"   {f.name:24s} null={f.null} default={getattr(f, 'default', '-')!r}")
        try:
            basket = Basket.objects.create()
            print("basket ok", basket.pk)
            order = Order.objects.create(
                number="100012",
                basket=basket,
                status="Pending",
                currency="INR",
                total_incl_tax=Decimal("4500.00"),
                total_excl_tax=Decimal("4500.00"),
                guest_email="devansh@example.com",
            )
            print("order ok", order.pk)
        except Exception as exc:
            node, seen = exc, set()
            while node is not None and id(node) not in seen:
                seen.add(id(node))
                print(f"  {type(node).__module__}.{type(node).__name__}: {str(node)[:300]}")
                node = node.__cause__ or node.__context__