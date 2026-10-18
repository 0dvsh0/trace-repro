"""Maps a replayed :class:`~tests.repro.harness.Step` onto a real entry point.

This is the only file in the target repository that knows how to turn a graph
node back into an application call, so it is deliberately small and editable --
if your service dispatches differently, change it here and re-run.

Everything it needs comes from ``incident.json`` at the repository root, the
contract the repository publishes for trace-repro:

``routes``   ``kind -> "module:callable"``. Every entry point is called the same
             way, ``fn(db, payload)``, so a replayed step is a lookup followed
             by a call. ``webhook`` is an inbound callback from a third party,
             ``worker`` a scheduled job, ``request`` ordinary inbound HTTP.
``seed``     ``table -> row``, the state the replay starts from.

The module is import-safe with no contract present (collection of a freshly
compiled test must not explode), so everything is resolved lazily.
"""

from __future__ import annotations

import importlib
import json
import os
import tempfile
from pathlib import Path

from tests.repro.harness import Step

CONTRACT_FILENAME = "incident.json"

#: <repo>/tests/repro/adapters.py -> the repository root is two levels up.
REPO_ROOT = Path(__file__).resolve().parents[2]


class ContractMissing(RuntimeError):
    """The repository does not publish the contract trace-repro needs."""


def contract() -> dict:
    path = REPO_ROOT / CONTRACT_FILENAME
    if not path.exists():
        raise ContractMissing(
            f"{path} not found. trace-repro drives this repository through that "
            "file: it names the contended entity, the entry points and the seed row."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def dispatch(step: Step, db):
    """Execute one replayed step against ``db`` and return the service response.

    The route is resolved by **endpoint first**, kind second. The graph only
    knows an entry endpoint as a string and guesses its kind from the words in
    it, so a repository whose endpoints are named ``POST /inventory/received``
    would otherwise have its goods-receipt step dispatched to whichever handler
    the guess landed on. The contract knows the truth, so it goes first.
    """
    route = _route_for(step)
    module_name, _, attribute = route.partition(":")
    entry = getattr(importlib.import_module(module_name), attribute)
    return entry(db, dict(step.payload))


def _route_for(step: Step) -> str:
    document = contract()
    routes = document.get("routes", {})
    for group in ("participants", "duplicate_participants"):
        for participant in document.get(group) or []:
            if participant.get("endpoint") == step.endpoint:
                return participant.get("route") or routes.get(participant.get("kind"), "")
    route = routes.get(step.kind, "")
    if not route:
        raise KeyError(
            f"no route for step kind {step.kind!r}; incident.json declares {sorted(routes)}"
        )
    return route


def temp_database():
    """A file-backed database for the replay.

    File-backed, not ``:memory:``: an in-memory database is a single shared
    connection, which cannot express concurrency at all.
    """
    package = contract()["package"]
    database_module = importlib.import_module(f"{package}.db")

    handle, path = tempfile.mkstemp(prefix="repro-", suffix=".sqlite3")
    os.close(handle)
    db = database_module.Database(path)
    db.migrate()
    return db


def seed(db) -> None:
    """Put the contended row back into the state the incident started from."""
    with db.session() as session:
        for table, values in contract()["seed"].items():
            session.conn.execute(f"DELETE FROM {table} WHERE id = ?", (values["id"],))
            session.query(table).create(**values)


def field(db, table: str, primary_key, column: str, default=None):
    """One column of one row, read in its own unit of work.

    ``default`` is returned when the row does not exist at all -- which is the
    correct answer for a side effect that never happened, and the whole reason
    the assertion is worth making.
    """
    with db.session() as session:
        try:
            row = session.query(table).filter_by(id=primary_key).one()
        except LookupError:
            return default
        return getattr(row, column, default)


__all__ = [
    "CONTRACT_FILENAME",
    "ContractMissing",
    "REPO_ROOT",
    "contract",
    "dispatch",
    "field",
    "seed",
    "temp_database",
]
