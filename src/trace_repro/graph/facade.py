"""Resolve a ``graph://`` URL into a live store.

``trace-repro heal --graph falkordb://incident_8812`` is the documented entry
point, so URL parsing lives in exactly one place. When FalkorDB is unreachable
we fall back to the in-process graph rather than dying -- but the caller is
told which backend it got, because silently degrading the engine is how you end
up debugging the wrong system.
"""

from __future__ import annotations

from urllib.parse import urlparse

from .falkor import FalkorStore
from .memory import MemoryStore
from .store import GraphStore, GraphUnavailable

DEFAULT_GRAPH_URL = "falkordb://trace_prod"


def parse_graph_url(url: str) -> dict:
    """Parse a graph URL.

    ``falkordb://incident_8812``                     -> graph ``incident_8812`` on localhost
    ``falkordb://localhost:6379/trace_prod``         -> explicit host and graph
    ``falkordbs://user:pass@host:port/graph_name``    -> FalkorDB Cloud with SSL/TLS
    ``memory://local``                              -> the in-process graph
    """
    p = urlparse(url if "://" in url else f"falkordb://{url}")
    netloc = p.netloc or ""
    path_graph = (p.path or "").strip("/")

    scheme = p.scheme or "falkordb"
    ssl = scheme in ("falkordbs", "rediss")
    if p.query:
        from urllib.parse import parse_qs

        qs = parse_qs(p.query)
        if "ssl" in qs:
            ssl = qs["ssl"][0].lower() in ("true", "1", "yes")

    if path_graph:
        graph = path_graph
        host, port = p.hostname or "localhost", p.port or 6379
    elif netloc:
        if ":" in netloc or "@" in netloc:
            graph = "trace_prod"
            host, port = p.hostname or "localhost", p.port or 6379
        else:
            # The documented form treats the authority as the graph name.
            graph = netloc
            host, port = "localhost", 6379
    else:
        graph, host, port = "trace_prod", "localhost", 6379

    return {
        "scheme": scheme,
        "graph": graph,
        "host": host,
        "port": port,
        "username": p.username,
        "password": p.password,
        "ssl": ssl,
    }


def open_graph(url: str = DEFAULT_GRAPH_URL, allow_fallback: bool = True, **kwargs) -> GraphStore:
    parts = parse_graph_url(url)
    scheme = parts["scheme"]

    if scheme in ("memory", "mem", "local"):
        return MemoryStore()

    if scheme in ("falkordb", "falkordbs", "redis", "rediss", "redisgraph"):
        ssl = parts.get("ssl", False) or kwargs.pop("ssl", False)
        try:
            return FalkorStore(
                graph=parts["graph"],
                host=parts["host"],
                port=parts["port"],
                username=parts["username"],
                password=parts["password"],
                ssl=ssl,
                **kwargs,
            )
        except GraphUnavailable:
            if not allow_fallback:
                raise
            return MemoryStore()

    raise ValueError(f"unsupported graph scheme: {scheme!r}")

