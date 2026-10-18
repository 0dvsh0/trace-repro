"""URL configuration.

``oscar`` ships its own URLconf; this project only mounts it and adds the
Django admin so the incident can also be inspected the way a shop manager
would - through Oscar's own dashboard.
"""

from django.apps import apps
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

oscar_urls = apps.get_app_config("oscar").urls

urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    # the local incident worker + instrumentation introspection
    path("", include("storefront.urls")),
    # oscar's own shop: catalogue, basket, checkout, accounts, dashboard
    path("", include(oscar_urls[0])),
]

# Product images live in MEDIA_ROOT; Django only serves those in DEBUG.
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)