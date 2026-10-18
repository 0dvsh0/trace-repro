"""Settings for the oscar storefront used as trace-repro's production victim.

Oscar itself is unmodified third-party code (django-oscar 4.2.1). Everything in
``storefront`` is local scaffolding: SQL-level instrumentation, the
abandoned-order worker a real shop would schedule, and the trace-repro
adapters.

``INSTALLED_APPS`` is Oscar's own list, copied verbatim from
``oscar.INSTALLED_APPS``, with one addition. It is spelled out rather than
imported because two details matter and are easy to get wrong:

* the core app is ``oscar.config.Shop``, not ``"oscar"`` -- Oscar's loader
  refuses to import from an app config that is not an ``OscarConfig``;
* every app config is named explicitly, because Oscar's ``apps.py`` modules
  import their own base config and Django's auto-discovery then picks the
  wrong class (``oscar.apps.dashboard.catalogue`` silently loads as
  ``catalogue`` and collides with the real catalogue app).

PostgreSQL is not a style choice. ``select_for_update()`` is silently ignored
on SQLite, so a lost-update repair proved on SQLite would prove nothing.
"""

import os
from pathlib import Path

from oscar.defaults import *  # noqa: F401,F403 - third-party defaults first

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SHOP_SECRET_KEY", "demo-only-not-a-real-secret")
DEBUG = os.environ.get("SHOP_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.contrib.flatpages",
    "oscar.config.Shop",
    "oscar.apps.analytics.apps.AnalyticsConfig",
    "oscar.apps.checkout.apps.CheckoutConfig",
    "oscar.apps.address.apps.AddressConfig",
    "oscar.apps.shipping.apps.ShippingConfig",
    "oscar.apps.catalogue.apps.CatalogueConfig",
    "oscar.apps.catalogue.reviews.apps.CatalogueReviewsConfig",
    "oscar.apps.communication.apps.CommunicationConfig",
    "oscar.apps.partner.apps.PartnerConfig",
    "oscar.apps.basket.apps.BasketConfig",
    "oscar.apps.payment.apps.PaymentConfig",
    "oscar.apps.offer.apps.OfferConfig",
    "oscar.apps.order.apps.OrderConfig",
    "oscar.apps.customer.apps.CustomerConfig",
    "oscar.apps.search.apps.SearchConfig",
    "oscar.apps.voucher.apps.VoucherConfig",
    "oscar.apps.wishlists.apps.WishlistsConfig",
    "oscar.apps.dashboard.apps.DashboardConfig",
    "oscar.apps.dashboard.reports.apps.ReportsDashboardConfig",
    "oscar.apps.dashboard.users.apps.UsersDashboardConfig",
    "oscar.apps.dashboard.orders.apps.OrdersDashboardConfig",
    "oscar.apps.dashboard.catalogue.apps.CatalogueDashboardConfig",
    "oscar.apps.dashboard.offers.apps.OffersDashboardConfig",
    "oscar.apps.dashboard.partners.apps.PartnersDashboardConfig",
    "oscar.apps.dashboard.pages.apps.PagesDashboardConfig",
    "oscar.apps.dashboard.ranges.apps.RangesDashboardConfig",
    "oscar.apps.dashboard.reviews.apps.ReviewsDashboardConfig",
    "oscar.apps.dashboard.vouchers.apps.VouchersDashboardConfig",
    "oscar.apps.dashboard.communications.apps.CommunicationsDashboardConfig",
    "oscar.apps.dashboard.shipping.apps.ShippingDashboardConfig",
    # third-party apps oscar depends on
    "widget_tweaks",
    "haystack",
    "treebeard",
    "django_tables2",
    "sorl.thumbnail",
    # local: the incident worker and the instrumentation that observes it
    "storefront",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.contrib.sites.middleware.CurrentSiteMiddleware",
    "oscar.apps.basket.middleware.BasketMiddleware",
    # Renders a customer-facing page instead of a traceback, with a real 500
    # status so monitoring and the logs still see the failure.
    "storefront.middleware.ShopErrorMiddleware",
    # trace-repro SQL instrumentation - must wrap everything that touches the
    # database, so it goes last.
    "storefront.instrumentation.RequestTraceMiddleware",
]

ROOT_URLCONF = "shopfront.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "OPTIONS": {
            "context_processors": [
                "django.contrib.auth.context_processors.auth",
                "django.template.context_processors.request",
                "django.template.context_processors.debug",
                "django.template.context_processors.i18n",
                "django.template.context_processors.media",
                "django.template.context_processors.static",
                "django.contrib.messages.context_processors.messages",
                # Django 6 removed django.contrib.sites.context_processors;
                # CurrentSiteMiddleware in MIDDLEWARE still sets request.site.
                "oscar.core.context_processors.metadata",
                "oscar.apps.search.context_processors.search_form",
                "oscar.apps.checkout.context_processors.checkout",
                "oscar.apps.communication.notifications.context_processors.notifications",
                "storefront.context_processors.shop_context",
            ],
            "loaders": [
                (
                    "django.template.loaders.cached.Loader",
                    [
                        "django.template.loaders.filesystem.Loader",
                        "django.template.loaders.app_directories.Loader",
                    ],
                ),
            ],
        },
    },
]

WSGI_APPLICATION = "shopfront.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("SHOP_DB_NAME", "shop"),
        "USER": os.environ.get("SHOP_DB_USER", "postgres"),
        "PASSWORD": os.environ.get("SHOP_DB_PASSWORD", "postgres"),
        "HOST": os.environ.get("SHOP_DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("SHOP_DB_PORT", "5432"),
        "ATOMIC_REQUESTS": False,
        "CONN_MAX_AGE": 0,
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# No SMTP server on a demo machine. Oscar mails the customer when an order is
# placed, and a connection-refused aborts order placement, which looks exactly
# like a checkout bug. The messages still land in memory.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
DEFAULT_FROM_EMAIL = "orders@vaayu.example"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Oscar's ProductImage stores names like "images/products/2026/10/…png", so
# MEDIA_ROOT is the project directory: MEDIA_ROOT + name lands on the file.
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
}

HAYSTACK_CONNECTIONS = {
    # Oscar's browse page is a Haystack search, not a queryset. The bundled
    # simple backend's update() is a no-op stub, so it never returns a single
    # product; Whoosh is a real on-disk index that every process can share.
    "default": {
        "ENGINE": "haystack.backends.whoosh_backend.WhooshEngine",
        "PATH": os.environ.get("SHOP_INDEX_PATH", str(BASE_DIR / "whoosh_index")),
        "INCLUDE_SPELLING": False,
    }
}

SITE_ID = 1

# ---------------------------------------------------------------- oscar config
OSCAR_SHOP_NAME = "Vaayu Electronics"
OSCAR_SHOP_TAGLINE = "Sound, without the drama."
OSCAR_DEFAULT_CURRENCY = "INR"
OSCAR_ALLOW_ANON_CHECKOUT = True
OSCAR_INITIAL_ORDER_STATUS = "Pending"
OSCAR_INITIAL_LINE_STATUS = "Pending"
OSCAR_ORDER_STATUS_PIPELINE = {
    "Pending": ("Being placed", "Cancelled"),
    "Being placed": ("Placed", "Cancelled"),
    "Placed": ("Shipped", "Cancelled"),
    "Shipped": ("Returned",),
}
OSCAR_CANCELLATION_LINE_STATUSES = ("Cancelled",)
OSCAR_SEND_ORDER_STATUS_CONFIRMATION_EMAIL = False
OSCAR_SEND_ORDER_STATUS_CHANGE_EMAIL = False

# Oscar ships a placeholder payment page -- no payment is taken by default.
# Its class loader prefers a local app named "checkout", which would collide
# with the framework's own label, so the override is registered through the
# loader hook instead.
OSCAR_DYNAMIC_CLASS_LOADER = "storefront.class_loader.class_loader"

LOGIN_REDIRECT_URL = "/"

TRACE_REPRO_INCIDENT_MODE = True