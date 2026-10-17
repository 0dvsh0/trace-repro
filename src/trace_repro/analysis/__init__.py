"""Cross-trace analysis: collision detection, causal replay, pruning."""

from .triage import CrashCandidate, PruneReport, TriageEngine

__all__ = ["CrashCandidate", "PruneReport", "TriageEngine"]
