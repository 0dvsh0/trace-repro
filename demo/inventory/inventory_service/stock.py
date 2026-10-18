"""Stock lifecycle rules.

Tiny on purpose: a quarantined lot has to be released by a goods receipt before
it may be sold, and only a lot the goods receipt has not yet released is still
the expiry worker's to write off.
"""

from __future__ import annotations

QUARANTINED = "QUARANTINED"
AVAILABLE = "AVAILABLE"
SOLD_OUT = "SOLD_OUT"

WRITABLE_BY_EXPIRY = frozenset({QUARANTINED})


def may_write_off(status: str) -> bool:
    """Only a lot that is still quarantined may be zeroed by the expiry sweep."""
    return status in WRITABLE_BY_EXPIRY
