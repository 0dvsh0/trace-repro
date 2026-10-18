"""inventory-svc -- scenario 2: a time-of-check/time-of-use oversell.

This package is a real, runnable service with a real bug. It is not a mock: the
race is a genuine write-skew between two concurrent read-modify-write sequences
on the same stock row, and the repair trace-repro applies to it is a genuine
source patch.
"""

from .db import Database
from .expiry import expire_lot
from .receipts import receive_stock
from .reservations import InsufficientStock, reserve_stock
from .runtime import HttpError, receive_stock_endpoint, reserve_endpoint, stock_expiry_job
from .stock import AVAILABLE, QUARANTINED, SOLD_OUT

__all__ = [
    "AVAILABLE",
    "QUARANTINED",
    "SOLD_OUT",
    "Database",
    "HttpError",
    "InsufficientStock",
    "expire_lot",
    "receive_stock",
    "receive_stock_endpoint",
    "reserve_endpoint",
    "reserve_stock",
    "stock_expiry_job",
]
