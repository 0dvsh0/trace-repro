"""The trace-repro graph schema.

Every label and relationship the engine creates lives here so that the
Cypher shipped to operators, the MCP tool surface and the writers can never
drift apart.

The graph is deliberately shaped around one idea: a *trace* is not the unit of
bug, a *stateful entity* is. Two unrelated ``trace_id`` trees are connected only
because they touched the same primary key.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Node labels
# --------------------------------------------------------------------------

TRACE = "Trace"
SPAN = "Span"
STATE_ENTITY = "StateEntity"
STATE_MUTATION = "StateMutation"
AST_FUNCTION = "ASTFunction"
GIT_COMMIT = "GitCommit"
GIT_MERGE_REQUEST = "GitMergeRequest"

NODE_LABELS = (
    TRACE,
    SPAN,
    STATE_ENTITY,
    STATE_MUTATION,
    AST_FUNCTION,
    GIT_COMMIT,
    GIT_MERGE_REQUEST,
)

# --------------------------------------------------------------------------
# Relationship types
# --------------------------------------------------------------------------

HAS_SPAN = "HAS_SPAN"                      # (:Trace) -> (:Span)
PARENT_SPAN = "PARENT_SPAN"                # (:Span)  -> (:Span)
WROTE_ENTITY = "WROTE_ENTITY"              # (:Span)  -> (:StateEntity)
READ_ENTITY = "READ_ENTITY"                # (:Span)  -> (:StateEntity)
EMITTED = "EMITTED"                        # (:Span)  -> (:StateMutation)
MUTATED = "MUTATED"                        # (:StateMutation) -> (:StateEntity)
HAPPENED_BEFORE = "HAPPENED_BEFORE"        # (:StateMutation) -> (:StateMutation)
CAUSED_READ = "CAUSED_READ"                # (:StateMutation) -> (:Span)
EXECUTED_FUNCTION = "EXECUTED_FUNCTION"    # (:Span) -> (:ASTFunction)
MUTATED_BY_FUNCTION = "MUTATED_BY_FUNCTION"  # (:StateEntity) -> (:ASTFunction)
MODIFIED_BY_COMMIT = "MODIFIED_BY_COMMIT"  # (:ASTFunction) -> (:GitCommit)

REL_TYPES = (
    HAS_SPAN,
    PARENT_SPAN,
    WROTE_ENTITY,
    READ_ENTITY,
    EMITTED,
    MUTATED,
    HAPPENED_BEFORE,
    CAUSED_READ,
    EXECUTED_FUNCTION,
    MUTATED_BY_FUNCTION,
    MODIFIED_BY_COMMIT,
)

# Edges that mean "this trace touched this row".
ENTITY_ACCESS_RELS = (READ_ENTITY, WROTE_ENTITY)

# --------------------------------------------------------------------------
# Causal windowing
# --------------------------------------------------------------------------

NS_PER_MS = 1_000_000
DEFAULT_CAUSAL_WINDOW_MS = 60_000

# Concurrency hazards we detect across traces.
HAZARD_WAW = "WAW"  # write-after-write, both writers had no lock
HAZARD_RAW = "RAW"  # read-after-write against a stale snapshot
HAZARD_WAR = "WAR"  # write-after-read

# How an entry endpoint is classified into a replay kind.
#
# The graph has no other signal for whether a trace began as an inbound
# callback or as a scheduled job, so this is a substring guess over the
# endpoint. It decides how a replay step is dispatched and how a report labels
# it, which is why both backends share this one implementation: the two must
# never disagree about a traversal result.
#
# It is still only a guess. A repository whose endpoints contain none of these
# words says so in its contract instead -- ``adapters.dispatch`` prefers the
# contract's endpoint -> route mapping over this.
WEBHOOK_MARKERS = ("webhook", "hook", "callback", "received", "receipt", "notification")
WORKER_MARKERS = ("worker", "job", "cron", "sweep", "scheduler", "batch")


def kind_of(endpoint: str) -> str:
    """``webhook`` / ``worker`` / ``request`` for one entry endpoint."""
    lowered = (endpoint or "").lower()
    if any(marker in lowered for marker in WEBHOOK_MARKERS):
        return "webhook"
    if any(marker in lowered for marker in WORKER_MARKERS):
        return "worker"
    return "request"


def entity_id(entity_type: str, primary_key: str) -> str:
    """Stable identity for a stateful entity: ``order:8812``."""
    return f"{str(entity_type).lower()}:{primary_key}"
