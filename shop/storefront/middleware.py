"""Customer-facing error pages.

A shop does not show a Django traceback to someone who just tried to pay. It
shows an apology and a support address, with a 500 status so monitoring and the
logs both still see the failure honestly.
"""

from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils.deprecation import MiddlewareMixin


class ShopErrorMiddleware(MiddlewareMixin):
    def process_exception(self, request, exception):
        from storefront.payments import InvalidOrderStatus

        if not isinstance(exception, InvalidOrderStatus):
            return None

        html = render_to_string(
            "storefront/payment_failed.html",
            {
                "order_number": getattr(exception, "order_number", ""),
                "reason": str(exception),
            },
            request=request,
        )
        return HttpResponse(html, status=500)