"""Reproduce the production incident.

This is the "Stage 8 · Monitor" half of the loop: genuinely independent requests
hit one row concurrently, telemetry streams into the graph, and the failure
lands. Nothing about the race is scripted -- the interleaving falls out of the
recorded offsets in the repository's ``incident.json``, exactly as it does in
production.

The workload knows nothing about any particular service. Which entry points to
call, with which payloads, at which offsets, is the repository's contract to
publish; this module only supplies the barrier, the threads and the telemetry.
"""

from __future__ import annotations

import importlib
import os
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..contract import IncidentSpec, Participant, load_contract
from ..graph.store import GraphStore
from ..ingest.tracer import Telemetry
from ..models import StateEntity

# Anything at or above this is a failed request, whatever the status class.
#
# A lost update does not have to be a 500 to be a real incident: an inventory
# reservation that 409s because a reconciliation worker clobbered the stock row
# is a lost sale, and an APM that only alerts on 5xx never even hears about it.
FAILED_STATUS = 400


@dataclass(slots=True)
class IncidentOutcome:
    entity_key: str = ""
    crash_span_id: str = ""
    crash_trace_id: str = ""
    crash_endpoint: str = ""
    error_type: str = ""
    error_message: str = ""
    http_status: int = 200
    participants: dict[str, str] = field(default_factory=dict)
    background_spans: int = 0
    background_traces: int = 0
    final_status: str = ""

    @property
    def crashed(self) -> bool:
        return self.http_status >= FAILED_STATUS


def _install_repo_on_path(repo_root: str) -> None:
    path = os.path.abspath(repo_root)
    if path not in os.sys.path:
        os.sys.path.insert(0, path)


def _repo_top_level_names(repo_root: str) -> set[str]:
    """Importable package names that live directly under ``repo_root``.

    Packages only. A loose ``conftest.py`` or ``incident.py`` is not imported by
    name when the service is imported, so evicting it from ``sys.modules`` would
    only risk tearing down something the *host* process is using.
    """
    root = Path(repo_root)
    names: set[str] = set()
    if not root.is_dir():
        return names
    for entry in root.iterdir():
        if entry.is_dir() and (entry / "__init__.py").exists():
            names.add(entry.name)
    return names


def _modules_in_use() -> set[str]:
    """Every module name on the caller's frame chain, plus their packages.

    A target repository that has a ``tests`` package must not cost the host
    process its own ``tests`` package. The running code is the one thing whose
    eviction is never acceptable, so it is excluded by name before anything is
    dropped.
    """
    import sys

    names: set[str] = set()
    frame = sys._getframe(1)
    while frame is not None:
        name = frame.f_globals.get("__name__", "")
        if name:
            names.add(name)
            parts = name.split(".")
            for i in range(1, len(parts)):
                names.add(".".join(parts[:i]))
        frame = frame.f_back
    return names


def purge_modules_under(repo_root: str) -> list[str]:
    """Drop every cached module that could shadow ``repo_root``'s own code.

    ``reproduce()`` runs a specific repository's code, so anything already
    imported under one of that repository's package names must go -- whether it
    points at this checkout or at a *different* checkout of the same package
    (a temp copy from an earlier run, say). A stale module's ``__file__`` is
    what the tracer records, so leaving one in place makes the repair planner
    open a file that no longer exists, or worse, the wrong copy of a file that
    does.

    Anything on the caller's own frame chain is never dropped: see
    :func:`_modules_in_use`.
    """
    import sys

    root = os.path.abspath(repo_root)
    top_level = _repo_top_level_names(root)
    in_use = _modules_in_use()

    stale: list[str] = []
    for name, module in list(sys.modules.items()):
        if module is None or name in in_use:
            continue
        origin = getattr(module, "__file__", None) or ""
        under_repo = bool(origin) and os.path.abspath(origin).startswith(root + os.sep)
        shadows_repo = name.split(".")[0] in top_level and not under_repo
        if under_repo or shadows_repo:
            stale.append(name)

    # Drop children before parents so no partially-unloaded package survives.
    for name in sorted(stale, key=lambda n: -n.count(".")):
        sys.modules.pop(name, None)
    return stale


def _call(spec: IncidentSpec, participant: Participant, db):
    route = participant.route or spec.route_for(participant.kind)
    module_name, _, attribute = route.partition(":")
    entry = getattr(importlib.import_module(module_name), attribute)
    return entry(db, dict(participant.payload))


def reproduce(
    store: GraphStore,
    repo_root: str,
    order_id: int | None = None,
    offsets_ms: tuple[float, ...] | None = None,
    amount_paise: int | None = None,
    background_traces: int = 0,
    background_seed: int = 8812,
    timeout: float = 20.0,
) -> IncidentOutcome:
    """Run the colliding workload against ``store`` and return what happened.

    The contended row, the entry points and the participant offsets all come
    from the repository's contract. ``order_id`` / ``offsets_ms`` /
    ``amount_paise`` exist only so a caller can retune a run without editing a
    repository; by default the recorded production values are used verbatim.
    """
    _install_repo_on_path(repo_root)
    purge_modules_under(repo_root)

    spec = load_contract(repo_root)
    participants = _retune(spec.participants, order_id, offsets_ms)

    db_module = importlib.import_module(f"{spec.package}.db")
    observ = importlib.import_module(f"{spec.package}.observ")
    runtime = importlib.import_module(f"{spec.package}.runtime")

    telemetry = Telemetry(store, service=spec.service)
    observ.set_telemetry(telemetry)

    import tempfile

    handle, path = tempfile.mkstemp(prefix="incident-", suffix=".sqlite3")
    os.close(handle)
    db = db_module.Database(path)
    db.migrate()
    _seed(db, spec, order_id, amount_paise)

    if background_traces:
        _emit_background_traffic(telemetry, store, background_traces, background_seed)

    outcome = IncidentOutcome(entity_key=spec.entity_key)
    lock = threading.Lock()
    start = time.monotonic()
    http_error = getattr(runtime, "HttpError", RuntimeError)

    def run(participant: Participant) -> None:
        delay = (start + participant.offset_ms / 1000.0) - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        try:
            result = _call(spec, participant, db) or {}
            with lock:
                outcome.participants[participant.endpoint] = result.get("trace_id", "")
                if participant.endpoint == spec.crash_endpoint:
                    outcome.crash_trace_id = result.get("trace_id", "")
        except http_error as exc:
            span = observ.last_span()
            with lock:
                outcome.participants[participant.endpoint] = getattr(span, "trace_id", "")
                outcome.http_status = getattr(exc, "status", 500)
                outcome.error_type = getattr(exc, "error_type", type(exc).__name__)
                outcome.error_message = str(exc)
                outcome.crash_span_id = getattr(span, "id", "") or ""
                outcome.crash_trace_id = getattr(span, "trace_id", "") or ""
                outcome.crash_endpoint = participant.endpoint
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            with lock:
                outcome.participants[participant.endpoint] = f"error:{exc}"

    threads = [
        threading.Thread(target=run, args=(participant,), name=f"incident-{i}")
        for i, participant in enumerate(participants)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=timeout)

    telemetry.flush()
    outcome.final_status = _final_state(db, spec, order_id)
    db.close()
    try:
        os.remove(path)
    except OSError:
        pass

    outcome.background_spans = background_traces * 3
    outcome.background_traces = background_traces
    observ.set_telemetry(None)
    return outcome


def _retune(participants: tuple[Participant, ...], order_id: int | None,
            offsets_ms: tuple[float, ...] | None) -> tuple[Participant, ...]:
    """Override the primary key and/or the offsets, without touching the repo."""
    out = []
    for i, participant in enumerate(participants):
        payload = dict(participant.payload)
        if order_id is not None:
            for pk_field in ("order_id", "stock_id", "payment_id", "id"):
                if pk_field in payload:
                    payload[pk_field] = order_id
                    break
        offset = offsets_ms[i] if offsets_ms and i < len(offsets_ms) else participant.offset_ms
        out.append(
            Participant(
                kind=participant.kind,
                endpoint=participant.endpoint,
                payload=payload,
                offset_ms=float(offset),
                route=participant.route,
            )
        )
    return tuple(out)


def _seed(db, spec: IncidentSpec, order_id: int | None, amount_paise: int | None) -> None:
    """Put the contended row back into the state the incident started from.

    The contract owns the seed. ``order_id`` / ``amount_paise`` only override it
    when a caller explicitly asks, so a repository's own numbers survive a run.
    """
    with db.session() as session:
        for table, values in spec.seed.items():
            row = dict(values)
            if order_id is not None:
                row["id"] = order_id
            if amount_paise is not None and "amount_paise" in row:
                row["amount_paise"] = amount_paise
            session.conn.execute(f"DELETE FROM {table} WHERE id = ?", (row["id"],))
            session.query(table).create(**row)


def _final_state(db, spec: IncidentSpec, order_id: int | None) -> str:
    with db.session() as session:
        row = session.query(spec.entity_type).filter_by(id=order_id or spec.primary_key).one()
        return str(getattr(row, spec.outcome_field, ""))


def _emit_background_traffic(
    telemetry: Telemetry, store: GraphStore, count: int, seed: int
) -> None:
    """Unrelated requests, so pruning has something real to prune.

    These are recorded exactly like the incident traces -- same labels, same
    edges -- which is the point: the graph has no idea which traffic matters
    until the causal traversal runs.
    """
    rng = random.Random(seed)
    endpoints = [
        "GET /cart", "GET /products", "POST /search", "GET /recommendations",
        "GET /inventory", "POST /address/validate", "GET /coupons",
        "POST /wishlist", "GET /reviews", "POST /notify",
    ]
    from ..models import now_ns
    from ..schema import READ_ENTITY, WROTE_ENTITY

    for i in range(count):
        trace = telemetry.start_request(
            endpoints[i % len(endpoints)],
            {"session": f"s{i}", "cursor": rng.randint(0, 10_000)},
        )
        entity = StateEntity("carts", str(1000 + (i % 750)))
        with telemetry.span(f"{endpoints[i % len(endpoints)]} handler", trace) as span:
            if i % 3 == 0:
                telemetry.record_entity_access(span, entity, READ_ENTITY, now_ns(), False)
            elif i % 3 == 1:
                telemetry.record_entity_access(span, entity, WROTE_ENTITY, now_ns(), True)
    telemetry.flush()
