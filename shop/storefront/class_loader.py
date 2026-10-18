"""Override which view Oscar mounts at a checkout step.

Oscar resolves ``checkout.views.PaymentDetailsView`` through a pluggable class
loader (``OSCAR_DYNAMIC_CLASS_LOADER``). Its own loader prefers a *local*
app named ``checkout`` -- which would collide with the framework's app label --
so this shim is the supported way to point that one step at the shop's own
implementation while leaving every other step untouched.
"""

from django.utils.module_loading import import_string
from oscar.core.loading import default_class_loader

OVERRIDES: dict[tuple[str, str], str] = {
    ("checkout.views", "PaymentDetailsView"): "storefront.checkout_views.PaymentDetailsView",
}


def class_loader(module_label, classnames, module_prefix="oscar.apps"):
    if len(classnames) == 1:
        target = OVERRIDES.get((module_label, classnames[0]))
        if target:
            return [import_string(target)]
    return default_class_loader(module_label, classnames, module_prefix)


__all__ = ["class_loader"]