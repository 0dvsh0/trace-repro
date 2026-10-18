"""Template context shared by the shop's own pages."""

from django.conf import settings


def shop_context(request):
    return {
        "incident_mode": getattr(settings, "TRACE_REPRO_INCIDENT_MODE", False),
    }