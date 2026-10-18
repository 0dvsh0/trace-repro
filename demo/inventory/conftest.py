"""Make this scenario's service package importable from its own tests.

This mirrors what a real target repository gets when trace-repro installs the
replay harness: the tests must run with nothing but the repository on
``sys.path``.
"""

from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
