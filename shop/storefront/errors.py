"""The page a customer sees when the shop cannot settle their payment.

Oscar's checkout catches every exception raised while placing an order and
re-renders the payment step with a message, which is the right behaviour for a
shop and the wrong one for a demo: the customer would never learn their payment
failed. This renders the same page a real shop would show, with a 500 status so
monitoring and the logs still see the failure honestly.
"""

from django.http import HttpResponse
from django.template.loader import render_to_string


def payment_failed_response(request, order, reason):
    html = render_to_string(
        "storefront/payment_failed.html",
        {"order_number": getattr(order, "number", ""), "reason": str(reason)},
        request=request,
    )
    return HttpResponse(html, status=500)


__all__ = ["payment_failed_response"]