"""Deterministic concurrency replay harness.

trace-repro installs this file into the target repository the first time it
compiles a reproduction. The generated test is plain ``pytest`` -- no plugin,
no tracing framework, no agent in the loop.

Nothing in this file knows what the service under test *is*. Two pieces make a
race reproducible:

``Barrier``
    Every replayed participant parks at the barrier and is released together,
    so the interleaving starts from a known state instead of whatever the thread
    scheduler felt like doing.

``ReplayPlan``
    Each participant then waits until its own recorded offset. Those offsets are
    not invented: they are the measured inter-arrival gaps from the causal
    subgraph, converted from nanoseconds to milliseconds and rebased so the
    first participant starts at 0.

Everything service-specific -- how to open a database, how to seed a row, how to
turn a step into a call -- lives in ``adapters.py`` beside this file, which the
target repository owns and may edit.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

DEFAULT_TIMEOUT_S = 15.0


@dataclass
class Step:
    """One replayed participant, derived from one node of the causal graph."""

    name: str
    kind: str                      # webhook | worker | request
    endpoint: str
    payload: dict = field(default_factory=dict)
    offset_ms: float = 0.0
    function: str = ""
    field_name: str = ""
    old_val: str = ""
    new_val: str = ""
    lock_held: bool = False


@dataclass
class Outcome:
    step: str
    status: int = 200
    error: BaseException | None = None
    trace_id: str = ""
    body: dict = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status >= 400


class Barrier:
    """A one-shot, deadline-bounded rendezvous for N participants."""

    def __init__(self, parties: int, timeout: float = DEFAULT_TIMEOUT_S):
        self.parties = parties
        self.timeout = timeout
        self._cond = threading.Condition(threading.Lock())
        self._waiting = 0
        self._generation = 0

    def wait(self) -> int:
        with self._cond:
            generation = self._generation
            self._waiting += 1
            if self._waiting >= self.parties:
                self._generation += 1
                self._waiting = 0
                self._cond.notify_all()
                return self._generation
            deadline = time.monotonic() + self.timeout
            while generation == self._generation:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._cond.notify_all()
                    raise TimeoutError(
                        f"barrier timed out with {self._waiting}/{self.parties} participants"
                    )
                self._cond.wait(remaining)
            return self._generation


class ReplayResult:
    def __init__(self, outcomes: list[Outcome]):
        self.outcomes = outcomes

    def __getitem__(self, name: str) -> Outcome:
        for outcome in self.outcomes:
            if outcome.step == name:
                return outcome
        raise KeyError(name)

    def __iter__(self):
        return iter(self.outcomes)

    def __len__(self) -> int:
        return len(self.outcomes)

    @property
    def failed(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.failed]

    @property
    def all_ok(self) -> bool:
        return not self.failed


def replay(steps: list[Step], runner, barrier: Barrier | None = None,
           timeout: float = DEFAULT_TIMEOUT_S) -> ReplayResult:
    """Run every step concurrently at its recorded offset and collect outcomes.

    ``runner(step) -> dict | None`` performs the actual call and may raise
    whatever the service raises to signal a failed request; the harness reads
    ``exc.status`` when there is one and treats anything else as a 500.
    """
    barrier = barrier or Barrier(len(steps), timeout=timeout)
    outcomes: list[Outcome] = []
    lock = threading.Lock()
    start = time.monotonic()

    def participant(step: Step) -> None:
        barrier.wait()
        target = start + step.offset_ms / 1000.0
        delay = target - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        outcome = Outcome(step=step.name, status=200)
        try:
            result = runner(step)
            if isinstance(result, dict):
                outcome.trace_id = result.get("trace_id", "")
                outcome.body = result.get("body", {})
                outcome.status = result.get("status", 200)
        except BaseException as exc:  # noqa: BLE001 - the harness reports anything
            outcome.status = getattr(exc, "status", 500)
            outcome.error = exc
        with lock:
            outcomes.append(outcome)

    threads = [
        threading.Thread(target=participant, args=(step,), name=f"replay-{step.name}")
        for step in steps
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=timeout)
    return ReplayResult(outcomes)


__all__ = [
    "Barrier",
    "DEFAULT_TIMEOUT_S",
    "Outcome",
    "ReplayResult",
    "Step",
    "replay",
]
