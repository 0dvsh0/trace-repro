"""Stock expiry worker.

Runs on a schedule, entirely outside any user request. Its trace never shares a
``trace_id`` with the reservation that later fails -- which is exactly why an APM
looking at the failing trace alone will never find it.
"""

from __future__ import annotations

from .db import Database, sleep_ms
from .observ import traced
from .stock import may_write_off

# Rehydrating the lot from the catalogue before deciding its fate.
#
# This has to be SLOW relative to the supplier call in receipts.py. With the
# receipt at offset 0 taking G ms end to end and the sweep starting at x for
# R ms, the sweep's stale write is the one that survives when:
#
#     x          < G            (sweep reads before the receipt commits)
#     x + R      > G            (sweep commits after the receipt commits)
#
# The slack on each side is G - x and x + R - G. G=260, x=200, R=140 leaves
# 60 ms and 80 ms, which survives a loaded CI scheduler.
REHYDRATE_MS = 140.0


@traced()
def expire_lot(db: Database, stock_id: int) -> dict:
    """Write off the sellable units of a lot that has not been released yet."""
    with db.session() as session:
        stock = session.query("stocks").filter_by(id=stock_id).one()

        rehydrate_catalogue(stock_id)          # slow work, BEFORE the write

        if not may_write_off(stock.status):
            # The lot was released while we were rehydrating; leave it alone.
            return stock.as_dict()
        stock.available = 0
        session.commit()
        return stock.as_dict()


def rehydrate_catalogue(stock_id: int) -> None:
    sleep_ms(REHYDRATE_MS)


__all__ = ["REHYDRATE_MS", "expire_lot", "rehydrate_catalogue"]
