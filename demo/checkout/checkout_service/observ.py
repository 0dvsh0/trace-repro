"""Instrumentation seam for the checkout service.

A real service does not hand-write telemetry calls at every line. It exposes an
ambient observability context, and its data layer emits spans and state
mutations from inside -- exactly like the SQLAlchemy / redis-py integrations
trace-repro would normally hook.

This module is the "middleware" side of the service. It is deliberately thin:
``db`` does the recording, this only carries the current span.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import local

try:  # pragma: no cover - import shape depends on how the app is loaded
    from trace_repro.ingest.tracer import Telemetry
    from trace_repro.models import Span, Trace
except ImportError:  # pragma: no cover
    Telemetry = object  # type: ignore[assignment,misc]
    Span = object  # type: ignore[assignment,misc]
    Trace = object  # type: ignore[assignment,misc]


@dataclass
class ObsContext:
    span: object | None = None
    trace: object | None = None
    last_span: object | None = None


# Thread-local, not ContextVar: every request is served on its own thread and a
# ContextVar set on one thread is invisible (or, worse, shared via its mutable
# default) on another. One ObsContext per thread is the correct scope here.
_state = local()

# The telemetry sink itself is process-wide: it is installed once at startup
# and every worker thread must see the same graph connection.
_TELEMETRY = None


def _ctx() -> ObsContext:
    ctx = getattr(_state, "ctx", None)
    if ctx is None:
        ctx = ObsContext()
        _state.ctx = ctx
    return ctx


def set_telemetry(telemetry) -> None:
    """Install the graph-bound telemetry sink for the whole process."""
    global _TELEMETRY
    _TELEMETRY = telemetry


def get_telemetry():
    return _TELEMETRY


def current_span():
    return _ctx().span


def last_span():
    """The most recent span on this thread, even after it has been unwound.

    An alert handler runs after ``in_span`` has restored, so ``current_span()``
    is already ``None`` by the time anybody asks "which span blew up?".
    """
    return _ctx().last_span


def current_trace():
    return _ctx().trace


@contextmanager
def request_context(trace):
    ctx = _ctx()
    prev = ctx.trace
    ctx.trace = trace
    try:
        yield trace
    finally:
        ctx.trace = prev


def lock_held() -> bool:
    """True while a ``SELECT ... FOR UPDATE`` critical section is open."""
    return bool(getattr(_ctx(), "row_lock", False))


@contextmanager
def in_span(span):
    ctx = _ctx()
    prev = ctx.span
    ctx.span = span
    try:
        yield span
    except BaseException:
        ctx.last_span = span
        raise
    finally:
        ctx.span = prev


@contextmanager
def row_lock(active: bool = True):
    ctx = _ctx()
    prev = getattr(ctx, "row_lock", False)
    ctx.row_lock = active
    try:
        yield
    finally:
        ctx.row_lock = prev


def traced(name: str | None = None):
    """Auto-instrumentation: wrap a handler in a span attributed to itself.

    Function attribution is taken from the wrapped callable's own code object,
    so ``(:Span)-[:EXECUTED_FUNCTION]->(:ASTFunction)`` points at the function
    that actually ran, with no hand-maintained registry.
    """

    def decorate(fn):
        import functools

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            telemetry = get_telemetry()
            trace = current_trace()
            if telemetry is None or trace is None:
                return fn(*args, **kwargs)
            span_name = name or fn.__name__
            with telemetry.span(span_name, trace, capture_frame=False) as span:
                # Attribute the span to the decorated function itself.
                object.__setattr__(span, "function_name", fn.__qualname__)
                object.__setattr__(span, "file_path", fn.__code__.co_filename)
                object.__setattr__(span, "lineno", fn.__code__.co_firstlineno)
                telemetry.store.upsert_span(span)
                _attach_function(telemetry, span, fn)
                with in_span(span):
                    return fn(*args, **kwargs)

        return wrapper

    return decorate


def _attach_function(telemetry, span, fn) -> None:
    import os

    from trace_repro.ingest.tracer import resolve_function

    resolved = resolve_function(os.path.abspath(fn.__code__.co_filename), fn.__name__)
    if resolved is not None:
        telemetry.store.link_span_function(span.id, resolved)
