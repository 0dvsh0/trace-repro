"""The canonical Cypher query library.

These strings are the artefact an operator reads, an MCP client calls, and the
FalkorDB backend actually executes. Keeping them in one module (rather than
inline in the backend) means the documentation on the landing page cannot drift
away from what runs.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# 1. Cross-trace collision: the killer query.
# --------------------------------------------------------------------------
# The crashing span is the victim. The collision query hops out of its trace
# through the shared (:StateEntity) node and back into every OTHER trace that
# wrote that row inside the causal window. Nothing here can be answered by a
# trace_id lookup -- that is the whole point.

CROSS_TRACE_COLLISION = """
// Given a crashing span, traverse across shared StateEntity nodes to find
// all OTHER independent traces that mutated the same entity before the crash.
MATCH (crash_span:Span {id: $crash_span_id})
MATCH (crash_span)-[r_read:READ_ENTITY|WROTE_ENTITY]->(entity:StateEntity)
MATCH (other_trace:Trace)-[:HAS_SPAN]->(mutator_span:Span)-[r_write:WROTE_ENTITY]->(entity)
WHERE other_trace.id <> $ignore_trace_id
  AND r_write.ts_ns <= r_read.ts_ns
  AND (r_read.ts_ns - r_write.ts_ns) < $window_ns
OPTIONAL MATCH (mutator_span)-[:EXECUTED_FUNCTION]->(upstream_fn:ASTFunction)
OPTIONAL MATCH (crash_span)-[:EXECUTED_FUNCTION]->(crash_fn:ASTFunction)
RETURN
  entity.entity_type      AS entity_type,
  entity.primary_key      AS primary_key,
  other_trace.id          AS other_trace_id,
  mutator_span.id         AS other_span_id,
  other_trace.entry_endpoint AS other_endpoint,
  other_trace.payload_json AS other_payload,
  coalesce(upstream_fn.name, mutator_span.function_name)  AS upstream_mutator,
  coalesce(upstream_fn.file_path, mutator_span.file_path) AS upstream_mutator_file,
  coalesce(crash_fn.name, crash_span.function_name)       AS downstream_crash_symptom,
  coalesce(crash_fn.file_path, crash_span.file_path)       AS downstream_crash_file,
  r_write.ts_ns           AS write_ts_ns,
  r_read.ts_ns            AS read_ts_ns,
  r_write.lock_held       AS was_locked,
  CASE
    WHEN r_write.lock_held = false AND coalesce(crash_fn.name, crash_span.function_name)
         IS NOT NULL AND coalesce(upstream_fn.name, mutator_span.function_name) <> coalesce(crash_fn.name, crash_span.function_name)
    THEN 'WAW'
    ELSE 'RAW'
  END AS hazard
ORDER BY r_write.ts_ns ASC
"""

# --------------------------------------------------------------------------
# 2. Minimal replay subgraph.
# --------------------------------------------------------------------------
# shortestPath over the HAPPENED_BEFORE / CAUSED_READ chain yields the exact
# ordered requests, payloads and millisecond offsets the replay harness needs.

MINIMAL_REPLAY_SUBGRAPH = """
// Exact chain of state transitions: first write on the entity -> corrupted read.
// shortestPath is what makes this *minimal*: it returns the shortest
// HAPPENED_BEFORE / CAUSED_READ walk, not every path that happened to connect.
MATCH (first:StateMutation {seq_no: 0})-[:MUTATED]->(entity:StateEntity {entity_type: $entity_type, primary_key: $primary_key})
MATCH (crash:Span {id: $crash_span_id})
WITH shortestPath((first)-[:HAPPENED_BEFORE|CAUSED_READ*..%d]->(crash)) AS p
RETURN DISTINCT
  [n IN nodes(p) | n.id]      AS path_ids,
  [n IN nodes(p) | labels(n)] AS path_labels,
  length(p)                   AS hops
"""


def minimal_replay_subgraph(max_depth: int = 12) -> str:
    return MINIMAL_REPLAY_SUBGRAPH % int(max_depth)


# Fetch full detail for an ordered list of mutation ids.

REPLAY_DETAIL = """
UNWIND $ids AS mid
MATCH (m:StateMutation {id: mid})
OPTIONAL MATCH (s:Span)-[:EMITTED]->(m)
OPTIONAL MATCH (t:Trace)-[:HAS_SPAN]->(s)
RETURN
  m.id                                       AS mutation_id,
  m.seq_no                                   AS seq_no,
  m.ts_ns                                    AS ts_ns,
  m.field                                    AS field,
  m.old_val                                  AS old_val,
  m.new_val                                  AS new_val,
  m.lock_held                                AS lock_held,
  coalesce(m.function_name, s.function_name) AS function_name,
  coalesce(t.id, m.trace_id)                 AS trace_id,
  coalesce(t.entry_endpoint, m.trace_id)     AS entry_endpoint,
  coalesce(t.payload_json, '{}')             AS payload_json,
  m.span_id                                 AS span_id,
  coalesce(s.start_ns, m.ts_ns)              AS span_start_ns
"""


# --------------------------------------------------------------------------
# 3. Upstream mutator locator.
# --------------------------------------------------------------------------

UPSTREAM_MUTATOR = """
// Which real function is responsible for the lost update?
MATCH (mutator:StateMutation {entity_type: $entity_type, primary_key: $primary_key, lock_held: false})
MATCH (mutator)-[:MUTATED]->(entity:StateEntity)-[:MUTATED_BY_FUNCTION]->(fn:ASTFunction)
RETURN fn.name AS name, fn.file_path AS file_path, fn.line_range AS line_range,
       fn.qualified_name AS qualified_name
ORDER BY fn.name ASC
"""

# --------------------------------------------------------------------------
# 4. Pruning statistics -- how much traffic did we throw away?
# --------------------------------------------------------------------------

PRUNE_STATS = """
MATCH (crash:Span {id: $crash_span_id})-[r:READ_ENTITY|WROTE_ENTITY]->(entity:StateEntity)
MATCH (ot:Trace)-[:HAS_SPAN]->(os:Span)-[:WROTE_ENTITY]->(entity)
WHERE ot.id <> $ignore_trace_id
RETURN
  count(DISTINCT ot.id) AS interfering_traces,
  count(DISTINCT os.id) AS interfering_spans
"""

# --------------------------------------------------------------------------
# 5. Writers of an entity -- the governance proof.
# --------------------------------------------------------------------------
# Deliberately independent of any crash span: after the fix the crash is gone,
# so a diff keyed on the crashing span would have nothing to compare. Asking
# "who writes this row, and do they lock it?" works before and after.

ENTITY_WRITERS = """
MATCH (os:Span)-[rw:WROTE_ENTITY]->(entity:StateEntity {entity_type: $entity_type, primary_key: $primary_key})
OPTIONAL MATCH (ot:Trace)-[:HAS_SPAN]->(os)
OPTIONAL MATCH (os)-[:EXECUTED_FUNCTION]->(fn:ASTFunction)
RETURN
  os.id                                     AS span_id,
  coalesce(ot.id, os.trace_id)              AS trace_id,
  coalesce(ot.entry_endpoint, os.trace_id)  AS entry_endpoint,
  coalesce(fn.name, os.function_name)       AS function_name,
  rw.ts_ns                                  AS ts_ns,
  rw.lock_held                              AS lock_held
ORDER BY rw.ts_ns ASC
"""

# --------------------------------------------------------------------------
# Schema DDL, applied by the FalkorDB backend on connect.
# --------------------------------------------------------------------------

SCHEMA_STATEMENTS = [
    "CREATE INDEX ON :Trace(id)",
    "CREATE INDEX ON :Span(id)",
    "CREATE INDEX ON :Span(trace_id)",
    "CREATE INDEX ON :StateEntity(entity_type, primary_key)",
    "CREATE INDEX ON :StateMutation(id)",
    "CREATE INDEX ON :StateMutation(entity_type, primary_key, seq_no)",
    "CREATE INDEX ON :ASTFunction(name)",
    "CREATE INDEX ON :GitCommit(sha)",
]
