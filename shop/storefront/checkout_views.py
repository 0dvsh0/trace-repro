"""The shop's payment step.

Oscar ships a deliberate placeholder here -- "this page needs implementing
within your project" -- because no payment is taken by default. A real shop
subclasses :class:`oscar.apps.checkout.views.PaymentDetailsView` and supplies
its own template and gateway; that is exactly what this is.

Nothing about order placement is reimplemented: the base view still builds the
submission and calls Oscar's own ``place_order``, so the order, its lines and
its totals are all written by the framework.
"""

from __future__ import annotations

from django.http import HttpResponseRedirect
from django.urls import reverse
from oscar.apps.checkout import views as oscar_checkout_views


class PaymentDetailsView(oscar_checkout_views.PaymentDetailsView):
    """One-click wallet payment against the shop's own gateway."""

    template_name = "storefront/payment_details.html"
    template_name_preview = "storefront/payment_details.html"

    # This gateway collects nothing sensitive (no card number ever reaches us),
    # so the form submits straight through. Oscar's base view rejects a POST to
    # the non-preview URL with a 400 and expects the two-step form instead.
    preview = True

    def get_success_url(self):
        # The base mixin stashes the order id in the session and redirects
        # here; overriding this to re-derive the number from a view attribute
        # that does not exist is how the first attempt broke order placement.
        return super().get_success_url()

    # Oscar routes a sensitive-details submission through the preview step.
    # This gateway collects nothing sensitive, so preview is the same page.
    def render_preview(self, request, **kwargs):
        return self.render_payment_details(request, **kwargs)

    def handle_successful_order(self, order):
        """Settle the order the customer just paid for.

        This is the victim step, and it runs here deliberately: it is outside
        the ``try/except`` Oscar wraps its ``order_placed`` signal in.

        Oscar's ``submit`` also catches everything raised while placing an
        order and quietly re-renders the payment step, so a refusal raised from
        here would never reach the customer. Returning the response directly is
        what makes this a real 500 rather than a silent log line.
        """
        from storefront.errors import payment_failed_response
        from storefront.payments import InvalidOrderStatus, finalise_order

        try:
            finalise_order(order.pk)
        except InvalidOrderStatus as exc:
            return payment_failed_response(self.request, order, exc)
        return super().handle_successful_order(order)


__all__ = ["PaymentDetailsView"]