# `demo/` — three real incidents, three real services

Every directory here is a **separate repository** with its own service package,
its own tests and its own `incident.json` contract. `trace-repro` is pointed at
one of them with `--repo` and drives the whole loop: reproduce → triage →
compile a failing test → patch the source → re-run → prove it from the graph.

Nothing here is mocked. Each service runs genuine concurrent read-modify-write
sequences against a real SQLite file, the crash comes out of the service's own
error type, and RED/GREEN are two real `python -m pytest` subprocesses whose exit
codes are the result.

## Run them

```bash
trace-repro demo --repo demo/checkout    --graph memory://demo    # or falkordb://…
trace-repro demo --repo demo/inventory   --graph memory://demo
trace-repro demo --repo demo/payments    --graph memory://demo
```

Add `--json` for a machine-readable summary, or `--graph falkordb://incident_x`
to run the identical traversals in Cypher against a live server.

---

## The three conditions

| | Scenario | Contended row | What actually happens | Stack trace blames | Repair |
| --- | --- | --- | --- | --- | --- |
| 1 | **Lost update** (`demo/checkout`) | `orders:8812` | A payment webhook and the cart-expiry worker both read the order before either wrote it. The worker commits its stale `PENDING` view 70 ms after the webhook commits, so a captured order comes back `CANCELLED`. | `capture_payment` — correct code refusing to capture a cancelled order | lock both writers + dedup the event |
| 2 | **Oversell** (`demo/inventory`) | `stocks:4471` | A goods receipt spends 260 ms in the supplier API; the expiry sweep read the same lot 60 ms earlier, still `QUARANTINED`, and writes off every sellable unit 80 ms after the receipt commits. | `reserve_stock` — correct code refusing to sell units the row says do not exist | lock both writers |
| 3 | **Double charge** (`demo/payments`) | `payments:8812` | The acquirer takes 240 ms. The provider's retry lands at +40 ms in a **different trace**, sees the pre-capture state, and appends a second ledger entry. Both deliveries answer 200. | `reconcile_settlement` — correct code reporting that the ledger does not balance | dedup the event + lock |

These are the bug classes that show up in real postmortems: the TOCTOU oversell
that a read-then-decrement always has, the at-least-once webhook retry that a
handler without a dedup key always has, and the plain lost update underneath
both.

### Why scenario 3 is the interesting one

The two colliding writers in scenarios 1 and 2 are *different functions in
different processes*. In scenario 3 they are **the same function, in two
different traces, with two different request ids and one event id**. Correlating
the failing request through logs finds nothing at all — the two deliveries share
no request id, no span id and no trace id. They are adjacent only through the
row they both wrote, which is the node the graph makes explicit.

---

## Why each interleaving is reproducible rather than lucky

A race that only reproduces 1 run in 5 is not a demo. The constants in each
service are chosen so the losing writer loses *by construction*, with margin.

With the first writer starting at offset 0 and taking `G` ms end to end, and
the second starting at `x` for `R` ms, the second writer's stale view is the one
that survives only when:

```
x      < G       the second writer reads before the first commits
x + R  > G       the second writer commits after the first commits
```

The slack is `G - x` on the left and `x + R - G` on the right, and the best
case `x = G - R/2` caps it at `R/2`. So a robust interleaving needs a **slow**
second phase, not a fast one.

| Scenario | `G` | `x` | `R` | read slack | commit slack | reader slack |
| --- | --- | --- | --- | --- | --- | --- |
| checkout | 260 ms | 190 ms | 140 ms | 70 ms | 70 ms | 230 ms |
| inventory | 260 ms | 200 ms | 140 ms | 60 ms | 80 ms | 260 ms |
| payments | 240 ms | 40 ms | 260 ms | 200 ms | 60 ms | 300 ms |

`tests/test_e2e.py::test_interleaving_has_slack_on_both_sides` fails if anyone
retunes those constants back into fragility. An earlier version of the checkout
scenario had 3 ms of slack and was flaky on a loaded runner.

---

## What a scenario repository contains

```
demo/checkout/
  incident.json          the contract (see below)
  conftest.py            puts the repository on sys.path for its own tests
  checkout_service/
    db.py                SQLite layer; emits spans and state mutations
    observ.py            the instrumentation seam (thread-local span context)
    state_machine.py     the domain rules
    webhooks.py          upstream mutator #1  <- the bug is here
    expiry.py            upstream mutator #2  <- and here
    checkout.py          the victim: where the 500 is raised
    runtime.py           HTTP-shaped entry points, fn(db, payload)
  tests/repro/
    harness.py           concurrency replay (barrier + recorded offsets)
    adapters.py          dispatch + fixtures, driven by incident.json
    test_incident_8812.py   generated; the evidence of a run
```

### `incident.json` — the contract

`trace-repro` does not know what any of these services look like. The repository
publishes what it needs in one JSON file:

| Field | What it tells trace-repro |
| --- | --- |
| `package` | which importable package holds the service |
| `entity_type` / `primary_key` | the contended row |
| `routes` | `kind -> "module:callable"` — every entry point, called as `fn(db, payload)` |
| `participants` | who ran, with which payload, at which millisecond offset |
| `crash_endpoint` | which participant is expected to fail |
| `seed` | the state the replay starts from |
| `assertions` | what "correct" means — only the repository knows this |

It is JSON, not Python, because the generated reproduction test has to run in a
repository where trace-repro is **not installed**; it reads the contract with
nothing but the standard library.

The split is deliberate: **the graph supplies facts** (who ran, with what
payload, at what offset, whether a lock was held, which write lost), and **the
repository supplies meaning** (what a correct outcome looks like). Neither side
has to know the other one's domain.

---

## The generated reproduction test

`trace-repro compile` writes `tests/repro/test_incident_<pk>.py` into the target
repository. It imports nothing from trace-repro, carries only facts that came out
of the graph, and asserts the repository's own invariants:

```python
CAUSAL_STEPS = [
    Step(name='POST /webhooks/payment', kind='webhook', ..., offset_ms=0.0,
         function='apply_webhook', lock_held=False),
    Step(name='cart-expiry worker',   kind='worker',  ..., offset_ms=189.9,
         function='expire_cart',   lock_held=False),
    Step(name='POST /checkout/capture', kind='request', ..., offset_ms=560.0,
         function='capture_payment', lock_held=False),
]

def test_incident_8812_cross_trace_race(db):
    seed(db)
    barrier = Barrier(parties=3)
    result = replay(CAUSAL_STEPS, lambda step: dispatch(step, db), barrier=barrier)
    assert not failures, "production failure reproduced: ..."
    assert final != CORRUPT_VALUE, 'lost update still wins: ...'
```

A barrier releases every participant together and each one then waits for its own
**recorded** offset, so the interleaving under test is the one that broke in
production rather than whatever the thread scheduler happened to pick.

Every scenario also gets a second test for the at-least-once case, because a
retried delivery corrupts data *even after the lock lands* — the lock fixes the
race, the dedup key fixes the retry, and only one of them is a race.

---

## Honest limitations

- **SQLite has no `SELECT ... FOR UPDATE`.** `for_update=True` issues
  `BEGIN IMMEDIATE`, the closest real equivalent. Against Postgres this is the
  genuine article.
- **`BEGIN IMMEDIATE` is a database-wide write lock**, held across the network
  call. That is coarser than production practice, where the I/O moves out of the
  transaction and the lock is scoped to the critical section. The demo keeps it
  simple so the interleaving stays readable.
- **The planner handles three strategies** (`session_isolation`, `row_lock`,
  `idempotency_guard`). It is not a general refactoring engine; it declines with
  a reason rather than guessing.
- **Dirty tracking suppresses no-op writes.** A writer that assigns back the
  value it read is not recorded as a mutation — correct for a domain object, but
  it means a scenario's stale write has to actually differ from the writer's own
  snapshot. That is why the inventory lot is seeded with 3 counted units.
