"""Shared fixtures.

The graph is the contract, so most tests build a small synthetic incident and
assert on traversal results. Synthetic rather than recorded, because a test that
depends on wall-clock timing is a test that will be flaky on someone's laptop.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
DEMO = ROOT / "demo"

#: Every scenario repository shipped under ``demo/``. Each is a real repository
#: that publishes an ``incident.json`` contract, so it can be driven end to end.
SCENARIOS = ("checkout", "inventory", "payments")

for path in (str(SRC), *(str(DEMO / name) for name in SCENARIOS)):
    if path not in sys.path:
        sys.path.insert(0, path)


def scenario_path(name: str) -> Path:
    return DEMO / name


def falkordb_available() -> bool:
    if os.environ.get("TRACE_REPRO_SKIP_FALKORDB"):
        return False
    try:
        from falkordb import FalkorDB  # noqa: F401
    except ImportError:
        return False
    from trace_repro.graph.store import GraphUnavailable

    try:
        store = _open_falkordb("pytest_probe")
        store.clear()
        return True
    except GraphUnavailable:
        return False


def _open_falkordb(graph: str):
    from trace_repro.graph.falkor import FalkorStore

    # CI runs FalkorDB as a service container (alias `falkordb`), so honour the
    # standard FALKORDB_HOST / FALKORDB_PORT variables. Local runs are unchanged:
    # localhost:6379, and the tests skip themselves when nothing is listening.
    host = os.environ.get("FALKORDB_HOST") or "localhost"
    port = int(os.environ.get("FALKORDB_PORT") or 6379)
    return FalkorStore(graph=graph, host=host, port=port)


@pytest.fixture(params=["memory", "falkordb"])
def any_store(request):
    """Both backends must produce identical traversal answers."""
    if request.param == "memory":
        from trace_repro.graph.memory import MemoryStore

        store = MemoryStore()
    else:
        if not falkordb_available():
            pytest.skip("FalkorDB is not reachable")
        store = _open_falkordb(f"pytest_{os.getpid()}_{request.node.name[:24]}")
    store.clear()
    yield store
    flush = getattr(store, "flush", None)
    if flush:
        flush()
    store.clear()


@pytest.fixture()
def memory_store():
    from trace_repro.graph.memory import MemoryStore

    return MemoryStore()


@pytest.fixture(params=SCENARIOS)
def demo_repo(request) -> Path:
    """Every scenario repository, one at a time."""
    repo = DEMO / request.param
    assert repo.exists(), f"the {request.param} scenario repository is missing"
    return repo


@pytest.fixture()
def scratch_repo(tmp_path: Path) -> Path:
    """A throwaway copy of the checkout scenario repository.

    The heal loop edits files on disk, so it must never run against the real
    demo tree: a failed test would leave the repository patched.
    """
    target = tmp_path / "checkout_service"
    shutil.copytree(DEMO / "checkout" / "checkout_service", target)
    (tmp_path / "tests").mkdir()
    conftest_src = DEMO / "checkout" / "conftest.py"
    if conftest_src.exists():
        shutil.copy(conftest_src, tmp_path / "conftest.py")
    return tmp_path
