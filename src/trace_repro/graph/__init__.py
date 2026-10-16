"""Graph backends for trace-repro."""

from .facade import DEFAULT_GRAPH_URL, open_graph, parse_graph_url
from .memory import MemoryStore
from .store import GraphStore, GraphUnavailable

__all__ = [
    "DEFAULT_GRAPH_URL",
    "GraphStore",
    "GraphUnavailable",
    "MemoryStore",
    "open_graph",
    "parse_graph_url",
]


def __getattr__(name: str):
    # FalkorStore is imported lazily so that a machine without the falkordb
    # client can still use the in-process graph and run the test suite.
    if name == "FalkorStore":
        from .falkor import FalkorStore

        return FalkorStore
    raise AttributeError(name)
