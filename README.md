# trace-repro

[![pipeline status](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43388957/showcase/badges/main/pipeline.svg)](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43388957/showcase/-/pipelines)

**Autonomous cross-trace bug reproducer & causal self-healing engine.**

> GitLab Transcend Hackathon submission · author [@0dvsh0](https://gitlab.com/0dvsh0)
> ([DevPost: devanshindepth](https://devpost.com/devanshindepth)) ·
> [onboarding issue](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43388957/showcase/-/work_items/1)
>
> **Tracks** — (1) *Self-Healing DevSecOps*: the red → green loop runs in official
> GitLab CI with JUnit / Code Quality artifacts and auto-created Merge Requests;
> the agent is exposed to GitLab Duo via a read-only MCP server. (2) *FalkorDB
> Graph Hacks — Track 01: Agents That Act on Connected Data*: the
> `(:StateEntity)` property graph is the context layer that makes cross-trace
> races visible at all.

Your APM shows you the victim. Stateful production bugs are born where *different*
traces collide on the same row — 40 ms apart, in code that never appears in the
stack trace you are staring at.

`trace-repro` links those traces through a property graph, compiles the causal
subgraph into a deterministic failing test, repairs the upstream mutator, and
proves the repair by re-running the same test.

```
$ trace-repro demo --repo demo/checkout --graph memory://demo

  Lost update: a payment webhook and the cart-expiry worker clobber one order
  contract   : checkout_service · orders:8812 · 3 participants

  stack trace blames : capture_payment (checkout.py:19)

  trace 134e0692c742  POST /webhooks/payment   wrote it   64.2ms before the crash
      unlocked write in apply_webhook()  [webhooks.py]      hazard=WAW  lock_held=False
  trace 47b59fe26fda  cart-expiry worker       wrote it   31.4ms before the crash
      unlocked write in expire_cart()  [expiry.py]         hazard=WAW  lock_held=False

  pruned 250/253 spans (98.81%) down to 3 causal participants

  pipeline #1  exit=1   1 failed, 1 passed                ← RED
  pipeline #2  exit=0   2 passed                           ← GREEN

  incident_8812 (lost_update) closed · lock_held [False] -> [False, True] · healed
```

---

## Quick start

```bash
pip install -e .                    # installs the `trace-repro` CLI
docker run -d -p 6379:6379 --name falkordb falkordb/falkordb:latest

trace-repro demo --repo demo/checkout   --graph falkordb://demo    # a lost update
trace-repro demo --repo demo/inventory  --graph falkordb://demo    # an oversell
trace-repro demo --repo demo/payments   --graph falkordb://demo    # a double charge
pytest                                  # 265 tests
```

No Docker? Every command takes `--graph memory://local` and the in-process graph
implements the same traversals. The `falkordb` tests skip themselves.

---

## What is actually real here

This is not a simulation. Concretely:

| Claim | How it is honoured |
| --- | --- |
| The race is real | Two genuine concurrent read-modify-write sequences against a real database. No `time.sleep` fake-out, no scripted interleaving. |
| The graph does the work | Traversal happens in Cypher on FalkorDB, and in BFS/shortest-path on the in-process twin. Every traversal test runs against **both**. |
| Function attribution is real | `(:Span)-[:EXECUTED_FUNCTION]->(:ASTFunction)` is resolved from the live Python frame (`inspect`), never a hardcoded allow-list. |
| RED and GREEN are real | Two `python -m pytest` subprocesses. The exit codes are the result; nothing asserts "it should have been red". |
| The repair is real | An `ast`-driven source transform. It is idempotent, re-parses to valid Python, and reverts cleanly. |
| The proof is real | The workload is re-run with the patch applied, re-ingested, and the before/after lock state is **read back out of the graph**. |

The one thing that is modelled rather than simulated is the race *amplification*:
the webhook does a ~260 ms payment-gateway call and the expiry worker a ~140 ms
rehydration, both between read and write. That is the real window, and it is
what makes a 190 ms interleaving deterministic in CI instead of probabilistic.

Those numbers are not arbitrary. With the webhook at offset 0 taking `G` ms
end to end, and the worker starting at `x` for `R` ms, the worker's stale write
only survives if:

```
x      < G      worker reads before the webhook commits
x + R  > G      worker commits after the webhook commits
```

Slack on each side is `G - x` and `x + R - G`, and the best case `x = G - R/2`
caps the slack at `R/2`. A robust interleaving therefore needs a *slow*
rehydrate. `G=260, x=190, R=140` gives 70 ms on each side — verified to hold
under eight threads of CPU saturation. An earlier version had 3 ms of slack and
was flaky on a loaded runner; `test_interleaving_has_slack_on_both_sides` now
fails if anyone retunes those constants into fragility.

---

## How it works

### 1 · Capture — index by state, not by trace

Spans alone are useless here: three independent requests produce three disjoint
trace trees with no edge between them. So the ingest layer also records every
**state mutation** — field-level, with the value before, the value after, and
crucially `lock_held`.

```
(:Trace)-[:HAS_SPAN]->(:Span)-[:READ_ENTITY|WROTE_ENTITY]->(:StateEntity)
                                    |
                                    +-> (:Span)-[:EXECUTED_FUNCTION]->(:ASTFunction)

(:StateMutation)-[:MUTATED]->(:StateEntity)
        +--[:HAPPENED_BEFORE {delta_ms, concurrent_overlap}]--> (:StateMutation)
        +--[:CAUSED_READ]--> (:Span)
```

### 2 · Traverse — the collision query

The crashing span is the victim. The query hops *out* of its trace through the
shared `(:StateEntity)` and back *into* every other trace that wrote that row in
the causal window:

```cypher
MATCH (crash_span:Span {id: $crash_span_id})
MATCH (crash_span)-[r_read:READ_ENTITY|WROTE_ENTITY]->(entity:StateEntity)
MATCH (other_trace:Trace)-[:HAS_SPAN]->(mutator_span:Span)-[r_write:WROTE_ENTITY]->(entity)
WHERE other_trace.id <> $ignore_trace_id
  AND r_write.ts_ns <= r_read.ts_ns
  AND (r_read.ts_ns - r_write.ts_ns) < $window_ns
...
```

The middle node is the whole product. Without it there is no edge between the
two spans, and no amount of log grepping will find it.

### 3 · Extract — the minimal replay subgraph

`shortestPath` over `HAPPENED_BEFORE | CAUSED_READ` yields the ordered
participants, payloads and offsets.

Two details that are easy to get wrong and that the tests pin down:

- **Offsets are request *start* times, not commit times.** A webhook that
  reads, does 40 ms of I/O, then writes commits at +40 ms but started at 0 ms.
  Replaying from commit times destroys the read-modify-write window and tests an
  interleaving that never happened.
- **`CAUSED_READ` is linked only from the latest write.** Linking it from every
  mutation lets `shortestPath` jump straight from the first write to the crash
  and silently drop the writer in between.

### 4 · Compile — a test that survives being committed

The generated file is plain `pytest`, importing nothing from trace-repro:

```python
CAUSAL_STEPS = [
    Step(name="POST /webhooks/payment", kind="webhook",
         endpoint="POST /webhooks/payment",
         payload={"event_id": "evt_8812", "order_id": 8812, "type": "payment.authorized"},
         offset_ms=0.0, function="apply_webhook",
         field_name="status", old_val="PENDING", new_val="AUTHORIZED", lock_held=False),
    Step(name="cart-expiry worker", kind="worker", ..., offset_ms=24.8, ...),
    Step(name="POST /checkout/capture", kind="request", ..., offset_ms=100.0, ...),
]

def test_incident_8812_cross_trace_race(db):
    seed(db)
    barrier = Barrier(parties=3)
    result = replay(CAUSAL_STEPS, lambda step: dispatch(step, db), barrier=barrier)
    assert not [o for o in result if o.failed], "production failure reproduced: ..."
    final = field(db, 'orders', PRIMARY_KEY, 'status')
    assert final != CORRUPT_VALUE, 'lost update still wins: ...'
```

`trace-repro` installs two small files into the target repo (`harness.py`,
`adapters.py`) so the test has a replay mechanism and a way to call your service.
Neither file imports your service: the harness is pure concurrency, and the
adapters read your `incident.json`. Everything else — the barrier, the offsets,
the assertions — is generated.

The split matters. The **graph** supplies facts: who ran, with which payload, at
which measured offset, whether a lock was held, which write lost the race. The
**repository** supplies meaning: what a correct outcome looks like for its own
domain. Neither side has to know the other's vocabulary.

### 5 · Repair — every unlocked writer, not the one that got blamed

```diff
-    with db.session() as session:
-        order = session.query("orders").filter_by(id=event.order_id).one()
+    with db.session(for_update=True) as session:
+        order = session.query("orders").filter_by(id=event.order_id).with_for_update().one()
+        if order.last_event_id == event.id:
+            return order.as_dict()
```

Three strategies, each with its own justification, applied by `ast` rather than
by a language model:

| Strategy | Fixes |
| --- | --- |
| `session_isolation` | Escalate the unit of work to a locking transaction. **This is the lost-update fix** — and it only counts once *every* writer takes the lock. |
| `row_lock` | Load the entity with `SELECT ... FOR UPDATE`. |
| `idempotency_guard` | At-least-once delivery re-applying an event. A retried webhook corrupts data even after the lock lands. |

Repairing only the function the crash blamed would leave `expire_cart` free to
clobber the webhook's write, so the pipeline plans for all contending writers.

### 6 · Prove — a measured before/after

After the patch, the workload is re-run and re-ingested into a second graph, and
the lock state is read back out:

```
writers before       : 2  lock_held=[False]
writers after        : 2  lock_held=[False, True]
contended writers    : apply_webhook, expire_cart
now locked           : apply_webhook, expire_cart
workload replay      : HTTP 200  final state CAPTURED
race reproduced      : True  ->  resolved True
```

`expire_cart` stops appearing as a writer at all: with the lock in place it
re-reads fresh state and correctly declines to cancel an authorised order.

---

## The demo targets

`demo/` holds **three separate repositories**, each a real service with a real
bug and its own `incident.json` contract. `trace-repro` is pointed at one with
`--repo`, which is the whole point: the engine is not hardcoded to one codebase.

| | Scenario | Contended row | What actually happens | Stack trace blames | Repair |
| --- | --- | --- | --- | --- | --- |
| 1 | **Lost update**<br>`demo/checkout` | `orders:8812` | A payment webhook and the cart-expiry worker both read the order before either wrote it. The worker commits its stale `PENDING` view 70 ms after the webhook commits, so a captured order comes back `CANCELLED`. | `capture_payment` — correct code refusing to capture a cancelled order | lock both writers + dedup the event |
| 2 | **Oversell**<br>`demo/inventory` | `stocks:4471` | A goods receipt spends 260 ms in the supplier API; the expiry sweep read the same lot 60 ms earlier, still `QUARANTINED`, and writes off every sellable unit 80 ms after the receipt commits. | `reserve_stock` — correct code refusing to sell units the row says do not exist | lock both writers |
| 3 | **Double charge**<br>`demo/payments` | `payments:8812` | The acquirer takes 240 ms. The provider's retry lands at +40 ms in a **different trace**, sees the pre-capture state, and appends a second ledger entry. Both deliveries answer 200. | `reconcile_settlement` — correct code reporting that the ledger does not balance | dedup the event + lock |

Scenario 3 is the one worth staring at: the two colliding writers are **the same
function, in two different traces, with two different request ids and one event
id**. Correlating the failing request through logs finds nothing — the two
deliveries share no request id, no span id and no trace id. They are adjacent
only through the row they both wrote, which is exactly the node the graph makes
explicit.

Scenario 2 does not even produce a 5xx: the customer gets a `409` from a stale
stock row. An APM that alerts on 5xx never hears about it; the graph still
explains it.

```python
# demo/checkout/checkout_service/webhooks.py
@traced()
def apply_webhook(db: Database, event: PaymentEvent) -> dict:
    with db.session() as session:                          # no lock
        order = session.query("orders").filter_by(id=event.order_id).one()
        if order.status == CANCELLED:
            return order.as_dict()

        authorization = payment_gateway(order.amount_paise, event.id)   # ~260ms
        order.status = apply_event(order.status, event.type, authorization)
        order.last_event_id = event.id
        order.webhook_applications += 1      # audit column; doubles on a retry
        session.commit()
```

Three independent callers contend for `orders:8812`:

| Participant | Kind | Starts at | Does |
| --- | --- | --- | --- |
| `apply_webhook` | webhook | +0 ms | read → 260 ms gateway → write `AUTHORIZED` at 260 ms |
| `expire_cart` | worker | +190 ms | read → 140 ms rehydrate → write `CANCELLED` at 330 ms |
| `capture_payment` | request | +560 ms | read → sees `CANCELLED` → **HTTP 500** |

Both writers read before either writes, so the last write wins with a stale
value. `capture_payment` is correct code refusing to capture a cancelled order;
the stack trace points at it and the bug is not there.

### The contract

A scenario repository publishes what `trace-repro` needs in one JSON file at its
root — the contended row, `kind -> module:callable` routes, each participant's
payload and measured offset, the seed state, and the assertions a correct run
must satisfy.

It is JSON, not Python, because the generated reproduction test has to run in a
repository where trace-repro is **not installed**. And the split is deliberate:
**the graph supplies facts, the repository supplies meaning.** See
[`demo/README.md`](demo/README.md) for the full field reference and the slack
derivation that makes each interleaving reproducible rather than lucky.

---

## CLI

```
trace-repro graph     --graph falkordb://incident_8812 [--reset] [--json]
trace-repro triage    --graph ... [--crash-span ID] [--error-type Class]
trace-repro compile   --graph ... --repo . [--print-source]
trace-repro repair    --graph ... --repo . [--apply]        # dry-run by default
trace-repro heal      --graph ... --repo . [--keep] [--skip-repair] [--ci gitlab] [--export-reports] [--create-mr]
trace-repro demo      --repo demo/checkout [--order-id N] [--background 250] [--ci gitlab] [--export-reports] [--create-mr] [--json]
trace-repro mcp       --graph ...                          # MCP over stdio
```

`--graph` accepts `falkordb://<graph-name>`, `falkordb://host:port/<graph>`, `falkordbs://user:pass@host:port/<graph>` (with SSL/TLS for FalkorDB Cloud), or `memory://name`.

Two practical details on driving the engine yourself:

- **`demo` is self-contained; `heal` is not.** A new process starts with an
  empty graph, so `triage`-family commands (`heal`, `triage`, `compile`,
  `repair`) require a graph that already holds an ingested incident. In CI this
  matters twice: every GitLab job gets a fresh FalkorDB service container, so
  the pipeline drives the lifecycle per job via `trace-repro demo`, which
  replays the workload, ingests it, and runs the whole loop in one process.
  The exported `artifacts/junit.xml` still reports Pipeline #1 (RED) and
  Pipeline #2 (GREEN) as separate testcases.
- **`memory://` is in-process and per-invocation.** It exists so you can run
  everything without Docker. To inspect a graph after the fact, or to share one
  command's graph with the next, use a `falkordb://` graph.

`--repo` is any repository that publishes an `incident.json`; it is not limited
to the demos.

---

## MCP surface & GitLab Duo Agent Platform

The agent gets the graph and nothing else — no file access, no shell, no way to
return an invented patch.

| Tool | Purpose |
| --- | --- |
| `get_cross_trace_collision_graph` | Who else wrote this row, and did they lock? |
| `minimal_replay_subgraph` | Ordered participants, payloads and offsets |
| `locate_crash` | ERROR spans, optionally filtered by error class |
| `upstream_mutator` | Every unlocked writer that must be repaired |
| `graph_stats` / `index_code` | Housekeeping |

The server's own instructions tell the model to refuse to propose a fix from a
stack trace alone. The patch comes from the AST planner; the model's job is to
call the tools in the right order.

### GitLab Duo Agent Platform Integration

`trace-repro` is pre-configured for the **GitLab Duo Agent Platform**:
- `.gitlab/duo/mcp.json` / `.gitlab/mcp.json`: Registers `trace-repro mcp` with all 6 FalkorDB tools.
- `.gitlab/duo/agents/causal_healer.yml`: Custom GitLab Duo Agent definition (`causal-incident-healer`) for hands-off autonomous incident remediation.
- `.gitlab/duo/flows/incident_self_heal.yml`: Duo flow orchestrating incident ingestion, collision graph traversal, repro compilation, AST repair, and MR creation.

---

## GitLab CI/CD & Self-Healing DevSecOps (GitLab Transcend)

`trace-repro` bridges production telemetry directly to GitLab CI/CD:

1. **Native GitLab CI Runner Collapsible Sections**:
   When executed inside GitLab CI (or with `--ci gitlab`), log output is formatted using ANSI collapsible section sequences (`\e[0Ksection_start:...`), rendering each DevSecOps stage as a collapsible panel in GitLab runner consoles.
2. **Standard GitLab CI Artifacts**:
   - `artifacts/junit.xml`: Formatted according to the JUnit XML schema, reporting both Pipeline #1 (RED deterministic reproduction) and Pipeline #2 (GREEN self-healing verification).
   - `artifacts/gl-code-quality-report.json`: Formatted according to the GitLab Code Quality (Code Climate) specification, highlighting the exact cross-trace concurrency hazard and unlocked writer in the MR Code Quality widget.
   - `artifacts/self_heal.patch` and `artifacts/repro_test.py`: Source diff and compiled test artifacts.
3. **Automated GitLab Merge Request Creation**:
   Passing `--create-mr` (or `CI_CREATE_MR=true`) uses the GitLab REST API (`GitLabClient`) to automatically create an incident branch `heal/incident_<id>`, commit the reproduction test, and open a Merge Request containing the full causal analysis, collision table, graph governance proof, and AST diff.
4. **Official `.gitlab-ci.yml`**:
   The root `.gitlab-ci.yml` defines the complete pipeline with the official `falkordb/falkordb:latest` service container. FalkorDB service containers are per-job in GitLab CI, so each healing job is self-contained: `trace-repro demo` captures the incident workload into its own graph and drives red → green → governance in a single process. A manual `merge_request:demo` job shows the MR autoplay when a `GITLAB_TOKEN` variable with `api` scope is configured.

---

## FalkorDB Context Layer & Agent Reasoning (Graph Hacks)

Built for the **WeMakeDevs FalkorDB Hackathon ("Graph Hacks: Context for AI Agents")** under **Track 01: Agents That Act on Connected Data**:

- **The Graph IS the Context Layer**: Agents cannot diagnose stateful race conditions from isolated logs or linear traces. FalkorDB provides the shared `(:StateEntity)` property graph bridging independent traces.
- **Multi-Hop Cypher Traversals**: Traverses `(:Span)-[:READ_ENTITY|WROTE_ENTITY]->(:StateEntity)<-[:WROTE_ENTITY]-(:Span)` across different distributed traces.
- **Graph Algorithms for Actionable Context**: Runs `shortestPath` over `HAPPENED_BEFORE | CAUSED_READ` to distill thousands of background spans down to the exact minimal replay subgraph.
- **FalkorDB Cloud Ready**: Supports local Docker containers and FalkorDB Cloud instances with TLS/SSL encryption (`falkordbs://`).
- **Explainable Decisions**: Decisions are never hallucinations; the agent traces every repair directly to an unlocked write relationship recorded in FalkorDB.

---

## Layout

```
src/trace_repro/
  schema.py          labels, relationship types, causal constants
  contract.py        the incident.json a target repository publishes
  models.py          the dataclasses every layer speaks
  graph/             store contract, FalkorDB backend, in-process twin, query library
  ingest/            span + state-mutation capture, live frame attribution
  astmap/            Python source -> (:ASTFunction)
  analysis/          cross-trace triage, replay planning, pruning
  codegen/           subgraph -> pytest, plus the harness it installs
  repair/            locate, plan and apply the upstream fix
  pipeline/          the workload, red -> green loop, and GitLab CI adapter
  mcp/               stdio JSON-RPC server
  cli.py, demo.py

demo/
  checkout/          scenario 1 · lost update on orders:8812
  inventory/         scenario 2 · oversell on stocks:4471
  payments/          scenario 3 · double charge on payments:8812
tests/               265 tests
.gitlab-ci.yml       GitLab CI/CD pipeline definition
.gitlab/             GitLab Duo MCP and custom agent definitions
```

---

## Testing

```bash
pytest                          # 265 tests
pytest -m "not integration"     # skip the FalkorDB-backed half
```

In GitLab CI the FalkorDB service container is reachable through the standard
`FALKORDB_HOST` / `FALKORDB_PORT` variables, and the 46 integration tests skip
themselves locally when nothing is listening on ``localhost:6379``.

| Module | What it pins down |
| --- | --- |
| `test_collision.py` | Collision detection, replay planning, pruning |
| `test_graph_store.py` | Both backends answer identically |
| `test_falkor_cloud.py` | FalkorDB Cloud URI parsing, SSL/TLS, and credentials |
| `test_gitlab.py` | GitLab CI adapter, JUnit XML, Code Quality JSON, MR client |
| `test_repair.py` | Idempotent, valid Python, repairs every writer, per scenario |
| `test_codegen.py` | The artefact parses, collects, and imports nothing from us |
| `test_mcp.py` | Protocol, and that no tool can edit source |
| `test_e2e.py` | All three real races, real pytest runs, real patches |
| `test_cli.py` / `test_telemetry.py` / `test_astmap.py` / `test_schema.py` | Surfaces, the `incident.json` contract, and invariants |

---

## Honest limitations

- **SQLite has no `SELECT ... FOR UPDATE`.** The demos' `for_update=True` issues
  `BEGIN IMMEDIATE`, the closest real equivalent. Against Postgres this is the
  genuine article.
- **`BEGIN IMMEDIATE` is a database-wide write lock.** The repair holds it
  across the acquirer call, which is coarse. In production you would move the
  I/O before the transaction and keep the lock scoped to the critical section;
  the demos keep it simple so the interleaving stays readable.
- **The planner handles three strategies.** It is not a general refactoring
  engine; it declines (with a reason) rather than guessing.
- **A victim that writes the row is reported as an advisory, not a failure.**
  `capture_payment`, `reserve_stock` and `reconcile_settlement` all still write
  the contended row unlocked outside the race window. Fixing them is not what
  closed the incident, so the tool says so rather than claiming them.
- **Entry-point classification is a substring guess.** The graph only knows an
  endpoint as a string. `adapters.dispatch` resolves the route from the
  repository's contract first and only falls back to the guess, so a repository
  with unusual endpoint naming still replays correctly.
- **Dirty tracking suppresses no-op writes.** A writer that assigns back the
  value it read is not recorded as a mutation. That is right for a domain object
  and wrong for a stale clobber, so a scenario's losing write has to differ from
  the writer's own snapshot — which is why the inventory lot is seeded with 3
  counted units rather than 0.
- **Pruning numbers are small.** 250 background traces gives 98.8%. The ratio is
  real; it is not 99.99%.

---

## Naming

Named for what it does.
