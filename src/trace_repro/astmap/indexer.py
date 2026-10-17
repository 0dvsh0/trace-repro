"""Source code -> (:ASTFunction) nodes.

We parse real Python with :mod:`ast` -- no regex, no LLM guessing -- so the
``(:Span)-[:EXECUTED_FUNCTION]->(:ASTFunction)`` edges carry a file path and a
real line range that an engineer can open and check.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

from ..models import ASTFunction

_SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".tox",
}


def _iter_files(root: str | Path, pattern: str = "*.py") -> list[Path]:
    root = Path(root).resolve()
    out: list[Path] = []
    for p in root.rglob(pattern):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        out.append(p)
    return sorted(out)


def iter_functions(root: str | Path) -> list[ASTFunction]:
    """Every function/method defined anywhere under ``root``."""
    found: list[ASTFunction] = []
    for path in _iter_files(root):
        found.extend(functions_in_file(str(path)))
    return found


def functions_in_file(file_path: str) -> list[ASTFunction]:
    """Every top-level and nested function defined in ``file_path``."""
    try:
        source = Path(file_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=file_path)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    out: list[ASTFunction] = []
    module = Path(file_path).stem
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = getattr(node, "lineno", 0)
            end = getattr(node, "end_lineno", start)
            out.append(
                ASTFunction(
                    name=node.name,
                    file_path=str(Path(file_path).resolve()),
                    line_range=(start, end),
                    qualified_name=f"{module}::{node.name}",
                    is_async=isinstance(node, ast.AsyncFunctionDef),
                )
            )
    return out


def locate_function(file_path: str, func_name: str) -> ASTFunction | None:
    """Find one function by name in one file. Used by the live tracer."""
    for fn in functions_in_file(file_path):
        if fn.name == func_name:
            return fn
    return None


def index_to_store(store, root: str | Path) -> list[ASTFunction]:
    """Write every function under ``root`` into the graph as an (:ASTFunction)."""
    fns = iter_functions(root)
    for fn in fns:
        store.upsert_function(fn)
    flush = getattr(store, "flush", None)
    if flush:
        flush()
    return fns


def snapshot(root: str | Path) -> dict[str, ASTFunction]:
    """Map ``qualified_name -> ASTFunction`` for fast repair lookups."""
    return {fn.qualified_name: fn for fn in iter_functions(root)}


def relative_display(file_path: str, root: str | Path | None = None) -> str:
    if not root:
        return file_path
    try:
        return os.path.relpath(file_path, str(root))
    except ValueError:
        return file_path
