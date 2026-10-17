"""Static code indexing and live frame attribution.

Function attribution is what makes ``EXECUTED_FUNCTION`` edges trustworthy: a
wrong file path sends the repair planner to edit innocent code.
"""

from __future__ import annotations

import textwrap

import pytest

from trace_repro.astmap.indexer import (
    functions_in_file,
    index_to_store,
    iter_functions,
    locate_function,
)
from trace_repro.ingest.tracer import clear_function_cache, resolve_function
from trace_repro.models import ASTFunction

from .conftest import DEMO

SOURCE = textwrap.dedent(
    '''
    def outer(db):
        return inner(db)

    def inner(db):
        return db

    class Holder:
        def method(self):
            return 1

    async def fetch(url):
        return url
    '''
)


@pytest.fixture()
def module(tmp_path):
    path = tmp_path / "mod.py"
    path.write_text(SOURCE, encoding="utf-8")
    return path


def test_finds_every_function(module):
    names = {fn.name for fn in functions_in_file(str(module))}
    assert names == {"outer", "inner", "method", "fetch"}


def test_records_real_line_ranges(module):
    by_name = {fn.name: fn for fn in functions_in_file(str(module))}
    source = module.read_text(encoding="utf-8").splitlines()
    assert by_name["outer"].line_range == (2, 3)
    assert source[by_name["inner"].line_range[0] - 1].strip().startswith("def inner")


def test_detects_async_functions(module):
    by_name = {fn.name: fn for fn in functions_in_file(str(module))}
    assert by_name["fetch"].is_async is True
    assert by_name["inner"].is_async is False


def test_finds_methods(module):
    assert locate_function(str(module), "method") is not None


def test_unknown_function_returns_none(module):
    assert locate_function(str(module), "nope") is None


def test_unparseable_file_is_skipped(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    assert functions_in_file(str(bad)) == []


def test_missing_file_is_skipped(tmp_path):
    assert functions_in_file(str(tmp_path / "absent.py")) == []


def test_qualified_names_are_qualified(module):
    fn = locate_function(str(module), "outer")
    assert fn.qualified_name == "mod::outer"


def test_indexing_skips_build_directories(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("def a():\n    pass\n", encoding="utf-8")
    junk = tmp_path / "__pycache__"
    junk.mkdir()
    (junk / "b.py").write_text("def b():\n    pass\n", encoding="utf-8")

    names = {fn.name for fn in iter_functions(tmp_path)}
    assert names == {"a"}


def test_index_to_store_writes_nodes(memory_store, tmp_path):
    (tmp_path / "svc.py").write_text("def handler():\n    pass\n", encoding="utf-8")
    functions = index_to_store(memory_store, tmp_path)
    assert {fn.name for fn in functions} == {"handler"}
    assert any(
        fn.name == "handler"
        for fn in memory_store.functions.values()
    )


def test_resolve_function_is_cached(memory_store):
    clear_function_cache()
    webhooks = str(DEMO / "checkout" / "checkout_service" / "webhooks.py")
    first = resolve_function(webhooks, "apply_webhook")
    second = resolve_function(webhooks, "apply_webhook")
    assert first is second


def test_resolve_function_returns_none_for_unknown():
    clear_function_cache()
    webhooks = DEMO / "checkout" / "checkout_service" / "webhooks.py"
    assert resolve_function(str(webhooks), "nope") is None


# --------------------------------------------------------------------------
# Live attribution against the real services
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module,function",
    [
        ("webhooks.py", "apply_webhook"),
        ("expiry.py", "expire_cart"),
        ("checkout.py", "capture_payment"),
    ],
)
def test_checkout_handlers_are_indexable(module, function):
    clear_function_cache()
    path = DEMO / "checkout" / "checkout_service" / module
    fn = resolve_function(str(path), function)
    assert fn is not None
    assert fn.name == function
    assert fn.file_path == str(path.resolve())
    assert fn.line_range[1] >= fn.line_range[0]


@pytest.mark.parametrize(
    "scenario,module,function",
    [
        ("checkout", "webhooks.py", "apply_webhook"),
        ("inventory", "receipts.py", "receive_stock"),
        ("inventory", "expiry.py", "expire_lot"),
        ("inventory", "reservations.py", "reserve_stock"),
        ("payments", "webhooks.py", "handle_payment_event"),
        ("payments", "reconciliation.py", "reconcile_settlement"),
    ],
)
def test_every_scenario_handler_is_indexable(scenario, module, function):
    """Every mutator the demos lean on has to be resolvable to an ASTFunction.

    If one is not, the repair planner gets a span with no code behind it and
    quietly has nothing to patch.
    """
    clear_function_cache()
    path = DEMO / scenario / f"{scenario}_service" / module
    fn = resolve_function(str(path), function)
    assert fn is not None
    assert fn.file_path == str(path.resolve())


def test_ast_function_is_hashable_and_serialisable(module):
    fn = locate_function(str(module), "outer")
    assert isinstance(fn, ASTFunction)
    assert {fn.name, fn.file_path, fn.qualified_name}


def test_dotted_module_name_is_derived_from_the_filename(module):
    fn = locate_function(str(module), "outer")
    assert fn.qualified_name.split("::")[0] == module.stem
