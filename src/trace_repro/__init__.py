"""trace-repro — autonomous cross-trace bug reproducer & causal self-healing engine.

The bug isn't in your stack trace. It's earlier, in another trace entirely.
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]


def __getattr__(name: str):
    if name == "main":
        from .cli import main

        return main
    raise AttributeError(name)
