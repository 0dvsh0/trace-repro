"""Files trace-repro installs into a target repository.

These are shipped as package data (not as strings in Python) so they stay
readable, lintable and reviewable. ``tests/test_assets.py`` asserts the copies
inside ``demo/`` are byte-identical to the templates, so the demo repository
can never quietly drift from what the generator would install.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent / "templates"

# Destination (relative to the target repo root) -> source file name.
INSTALL_MAP = {
    "tests/repro/harness.py": "harness.py",
    "tests/repro/adapters.py": "adapters.py",
}


@cache
def read_template(name: str) -> str:
    path = TEMPLATE_DIR / name
    if not path.exists():  # pragma: no cover - packaging guard
        raise FileNotFoundError(f"missing packaged template: {path}")
    return path.read_text(encoding="utf-8")


def harness_files() -> dict[str, str]:
    """Destination path -> file content, ready to install."""
    return {dest: read_template(src) for dest, src in INSTALL_MAP.items()}


def available() -> list[str]:
    return sorted(INSTALL_MAP)
