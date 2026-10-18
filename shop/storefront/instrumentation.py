"""trace-repro instrumentation for a Django/Oscar application.

The interesting property of this layer is that it observes the database, not
the application code. Oscar is third-party and its checkout is many views deep,
but every read-modify-write it performs -- and every one the background reaper
performs -- passes through one connection. Watching the SQL means:

* the contended row is identified by its real primary key, not by guesswork;
* writers are found even when they live in code trace-repro has never seen;
* an APM that only watches the failing request still misses the collision,
  because the reaper runs under a different trace and a different request.

``lock_held`` is read straight off the wire: PostgreSQL appends ``FOR UPDATE``
to a statement only when the queryset actually asked for a row lock, so the
graph records what the database really did rather than what the code intended.
"""

from __future__ import annotations

import re
import threading
from contextlib import contextmanager

from trace_repro.models import StateEntity, now_ns

# The contended table. Django derives it from the app label and model name,
# so Oscar's Order model lives in "order_order" -- not "oscar_order".
ORDER_TABLE = "order_order"

_state = threading.local()

# Django emits ``%s`` placeholders for every backend except sqlite3.
_IS_SELECT = re.compile(r"^\s*select\b", re.I)
_SET_COLUMNS = re.compile(r'"?([a-z_][a-z0-9_]*)"?\s*=\s*%s', re.I)
_PK_PREDICATE = re.compile(r'"?id"?\s*=\s*%s', re.I)
_HAS_FOR_UPDATE = re.compile(r"\bfor\s+update\b", re.I)

# Built from ORDER_TABLE so the two can never drift apart again. Django
# quotes identifiers, so the quotes are part of the pattern.
_TABLE = re.escape(ORDER_TABLE)
_IS_UPDATE = re.compile(rf'^\s*update\s+"?{_TABLE}"?\s+set\b', re.I)
_IS_INSERT = re.compile(rf'^\s*insert\s+into\s+"?{_TABLE}"?\b', re.I)

# Columns Oscar's order row actually owns, used to keep old-value lookups
# cheap and to avoid mistaking a WHERE clause for a SET clause.
_TRACKED = ("status", "number", "total", "total_incl_tax", "currency", "basket_id")


# --------------------------------------------------------------------------
# per-thread context
# --------------------------------------------------------------------------


_MISSING = object()

# Telemetry is a handle onto the graph, not per-request state, so it is
# installed once and shared by every worker thread the server spawns.
_global_telemetry = None


def get_telemetry():
    local = getattr(_state, "telemetry", _MISSING)
    return _global_telemetry if local is _MISSING else local


def set_telemetry(telemetry, *, shared: bool = False) -> None:
    global _global_telemetry
    if shared:
        _global_telemetry = telemetry
    else:
        _state.telemetry = telemetry


def current_trace():
    return getattr(_state, "trace", None)


def set_trace(trace) -> None:
    _state.trace = trace


def current_span():
    stack = getattr(_state, "spans", None)
    return stack[-1] if stack else None


@contextmanager
def pushed(span):
    """Make ``span`` the target of subsequent SQL observations."""
    stack = getattr(_state, "spans", None)
    if stack is None:
        stack = _state.spans = []
    stack.append(span)
    try:
        yield span
    finally:
        if stack:
            stack.pop()


@contextmanager
def tracing(span):
    """``pushed`` for the duration of a ``with telemetry.span(...)`` block."""
    with pushed(span):
        yield span


def reset_thread_state() -> None:
    _state.telemetry = None
    _state.trace = None
    _state.spans = []


# --------------------------------------------------------------------------
# SQL parsing
# --------------------------------------------------------------------------


def _clean(sql: str) -> str:
    return " ".join(sql.split())


def _pk_from_where(sql: str, params: tuple) -> str:
    """Primary key out of a ``WHERE ... id = %s`` predicate."""
    matches = list(_PK_PREDICATE.finditer(sql))
    if not matches or not params:
        return ""
    # The predicate's placeholder position is its index among all %s in order.
    index = _placeholder_index(sql, matches[-1])
    if index is None or index >= len(params):
        return ""
    value = params[index]
    return "" if value is None else str(value)


def _placeholder_index(sql: str, match: re.Match) -> int | None:
    """Which entry of ``params`` does this match's ``%s`` consume?

    Both patterns end with the placeholder, so its offset is simply the number
    of placeholders that appear before it.
    """
    token_start = match.end() - 2
    if not sql[token_start : match.end()] == "%s":
        return None
    return sql.count("%s", 0, token_start)


def _update_columns(sql: str, params: tuple) -> tuple[list[tuple[str, object]], str]:
    """``(column, new_value)`` pairs from an UPDATE, plus the row's pk."""
    head = sql.split(" WHERE ", 1)[0]
    assignments = list(_SET_COLUMNS.finditer(head))
    values: list[tuple[str, object]] = []
    for match in assignments:
        index = _placeholder_index(head, match)
        if index is None or index >= len(params):
            continue
        values.append((match.group(1), params[index]))
    return values, _pk_from_where(sql, params)


# --------------------------------------------------------------------------
# observation
# --------------------------------------------------------------------------


def observe(execute, sql: str, params, many: bool, context) -> object:
    """``connection.execute_wrapper`` hook.

    It returns the cursor untouched. Any failure inside is swallowed on
    purpose: instrumentation that can break the shop it is watching is worse
    than instrumentation that misses a row.
    """
    try:
        span = current_span()
        telemetry = get_telemetry()
        if span is None or telemetry is None:
            return execute(sql, params, many, context)
        statement = _clean(sql)
        is_locked = bool(_HAS_FOR_UPDATE.search(statement))
        lock_held = getattr(_state, "lock_held", False) or is_locked
        if is_locked:
            _state.lock_held = True

        if _IS_UPDATE.match(statement):
            values, pk = _update_columns(statement, _as_tuple(params))
            if pk:
                entity = StateEntity(ORDER_TABLE, pk)
                for column, new_value in values:
                    telemetry.record_write(
                        span, entity, column, None, new_value, lock_held=lock_held
                    )
        elif _IS_INSERT.match(statement):
            pk = _pk_from_where(statement, _as_tuple(params))
            if pk:
                entity = StateEntity(ORDER_TABLE, pk)
                for column, value in zip(_TRACKED, _as_tuple(params)):
                    telemetry.record_write(
                        span, entity, column, None, value, lock_held=lock_held
                    )
        elif _IS_SELECT.match(statement) and ORDER_TABLE in statement:
            pk = _pk_from_where(statement, _as_tuple(params))
            if pk:
                telemetry.record_read(
                    span, StateEntity(ORDER_TABLE, pk), lock_held=lock_held
                )
    except Exception:  # noqa: BLE001 - never break the request being observed
        pass
    return execute(sql, params, many, context)


def _as_tuple(params) -> tuple:
    if params is None:
        return ()
    if isinstance(params, (list, tuple)):
        return tuple(params)
    return (params,)


# --------------------------------------------------------------------------
# background jobs
# --------------------------------------------------------------------------

# Frames belonging to this module are never the answer to "which function
# touched the row"; they are the observer, not the actor. ``contextlib`` is in
# the same category: a ``with`` statement's machinery sits between the observer
# and the code being observed, and reporting it would attribute every span to
# ``__enter__``.
_SKIP_MODULES = ("storefront.instrumentation", "trace_repro", "contextlib")


def _business_frame(depth: int = 1):
    """The first frame outside the observer, walking the caller's chain."""
    import inspect

    frame = inspect.currentframe()
    try:
        while frame is not None:
            module = frame.f_globals.get("__name__", "")
            if not any(
                module == skip or module.startswith(skip + ".")
                for skip in _SKIP_MODULES
            ):
                break
            frame = frame.f_back
        for _ in range(max(0, depth - 1)):
            if frame is None:
                break
            frame = frame.f_back
        return frame
    finally:
        del frame


def _attribute(span) -> None:
    """Point ``span`` at the function that actually did the work.

    The span is created inside this module, so left alone it would be
    attributed to the observer rather than to the business code -- and the
    repair planner would then be pointed at the wrong file entirely.
    """
    import os

    frame = _business_frame(depth=1)
    if frame is None:
        return
    span.function_name = frame.f_code.co_name
    span.file_path = os.path.abspath(frame.f_code.co_filename)
    span.lineno = frame.f_lineno


class _WorkSpan:
    """A span for one unit of work.

    A class rather than a generator context manager on purpose: a generator's
    body resumes inside ``contextlib``, so reading the caller's frame from in
    there reports ``contextlib`` instead of the code under observation.
    """

    def __init__(self, endpoint: str, payload: dict | None):
        self.endpoint = endpoint
        self.payload = payload or {}
        self.telemetry = get_telemetry()
        self.own_trace = None
        self.span = None
        self._ctx = None
        self.pushed = False

    def __enter__(self):
        if self.telemetry is None:
            return None
        trace = current_trace()
        if trace is None:
            # A cron sweep has no request to join, so it opens a trace of its
            # own. That separate trace_id is what makes the collision
            # invisible from the failing request alone.
            trace = self.telemetry.start_request(self.endpoint, self.payload)
            self.own_trace = trace
            set_trace(trace)
        _state.lock_held = False
        # capture_frame=False because the tracer would otherwise attribute the
        # span to this module's own frame. The real attribution is applied
        # below, once we know which function is actually under observation.
        self._ctx = self.telemetry.span(self.endpoint, trace, capture_frame=False)
        self.span = self._ctx.__enter__()
        _attribute(self.span)
        self.telemetry.store.upsert_span(self.span)
        try:
            from trace_repro.astmap.indexer import locate_function

            fn = locate_function(self.span.file_path, self.span.function_name)
            if fn is not None:
                self.telemetry.store.link_span_function(self.span.id, fn)
        except Exception:  # noqa: BLE001 - attribution is best-effort
            pass
        stack = getattr(_state, "spans", None)
        if stack is None:
            stack = _state.spans = []
        stack.append(self.span)
        self.pushed = True
        return self.span

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._ctx is not None:
                self._ctx.__exit__(exc_type, exc, tb)
        finally:
            if self.pushed:
                _pop()
            if self.own_trace is not None:
                self.own_trace.status = "ERROR" if exc_type is not None else "OK"
                self.own_trace.end_ns = now_ns()
                self.telemetry.store.upsert_trace(self.own_trace)
                set_trace(None)
            _state.lock_held = False
        return False


def _pop() -> None:
    stack = getattr(_state, "spans", None)
    if stack:
        stack.pop()


def work_trace(endpoint: str, payload: dict | None = None):
    """Span for a unit of work, whichever trace already owns this thread."""
    return _WorkSpan(endpoint, payload)


# --------------------------------------------------------------------------
# request boundary
# --------------------------------------------------------------------------


class RequestTraceMiddleware:
    """Give every HTTP request its own trace, the way a real APM would.

    This is what makes the incident hard to see. The customer's checkout and
    the reaper's sweep are separate requests, on separate connections, with
    unrelated ``trace_id`` values. Nothing about the failing request hints that
    another trace touched the same row.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        telemetry = get_telemetry()
        if telemetry is None:
            return self.get_response(request)

        label = f"{request.method} {request.path}"
        trace = telemetry.start_request(label, {"path": request.path, "method": request.method})
        set_trace(trace)
        _state.lock_held = False
        try:
            with telemetry.span(label, trace) as span:
                with pushed(span):
                    response = self.get_response(request)
            trace.status = "ERROR" if response.status_code >= 500 else "OK"
            trace.end_ns = span.end_ns
        except Exception:
            trace.status = "ERROR"
            trace.end_ns = now_ns()
            telemetry.store.upsert_trace(trace)
            set_trace(None)
            _state.lock_held = False
            raise
        telemetry.store.upsert_trace(trace)
        set_trace(None)
        _state.lock_held = False
        return response


__all__ = [
    "RequestTraceMiddleware",
    "current_span",
    "current_trace",
    "get_telemetry",
    "job_trace",
    "observe",
    "pushed",
    "reset_thread_state",
    "set_telemetry",
    "set_trace",
    "tracing",
    "work_trace",
]