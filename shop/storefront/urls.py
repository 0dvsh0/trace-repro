"""Local shop endpoints.

Deliberately small: everything a customer touches lives in Oscar's own URLconf.
These routes only expose the demo's helpers, so the shop itself stays
third-party.
"""

from storefront.views import urlpatterns as storefront_urls

urlpatterns = list(storefront_urls)