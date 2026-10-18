# 🧠 trace-repro — Hackathon Context & Live Video Production Rules

> **Single Source of Truth** for the hackathon demo submission:
> 1. **FalkorDB Graph Hacks — Track 1:** *Agents That Act on Connected Data* (WeMakeDevs)
> 2. **GitLab Transcend: Life After Code — Track:** *Hands-Off Autonomous DevSecOps* (Devpost)
> 
> Reference Video Inspiration: [GitLab Transcend Hackathon Process Walkthrough](https://youtu.be/VNU-fs60FE0)

---

## 🎯 Mandatory Track Requirements & Scoring Rubric

### 🟣 FalkorDB Track 1: Agents That Act on Connected Data
* **Core Philosophy:** FalkorDB must be the **essential context layer**, not a decorative database. If FalkorDB is removed, the agent cannot identify the bug.
* **The Connected Data Problem:** Modern APMs record traces as vertical, isolated silos. Two independent requests (e.g., a payment webhook and a cart-expiry cron worker) share **zero** trace IDs, span IDs, or request IDs. Standard RAG and vector databases cannot correlate them.
* **The Graph Model:**
  - `(:Trace)-[:HAS_SPAN]->(:Span)-[:READ_ENTITY|WROTE_ENTITY]->(:StateEntity)`
  - `(:StateMutation)-[:MUTATED]->(:StateEntity)`
  - `(:StateMutation)-[:HAPPENED_BEFORE {delta_ms}]->(:StateMutation)`
  - `(:StateMutation)-[:CAUSED_READ]->(:Span)`
  - `(:Span)-[:EXECUTED_FUNCTION]->(:ASTFunction)`
* **Graph Algorithms & Multi-Hop Cypher:**
  - Cypher traversal hops out of the crashing trace, through the shared `(:StateEntity)` node (`orders:8812`), and discovers the colliding background worker.
  - `shortestPath` over `HAPPENED_BEFORE | CAUSED_READ` prunes 250 background spans down to just the 3 causal participants (**98.81% noise reduction**).
* **Proof of Tech:** Before/after lock state read *directly back out of the graph* (`[False]` → `[False, True]`).

### 🟠 GitLab Transcend: Hands-Off Track (Life After Code)
* **Core Philosophy:** **Zero human intervention** between incident detection and production-ready Merge Request.
* **GitLab Duo Agent Platform:**
  - **MCP Server:** `.gitlab/duo/mcp.json` exposes 6 read-only FalkorDB tools to GitLab Duo (`get_cross_trace_collision_graph`, `minimal_replay_subgraph`, `upstream_mutator`, etc.).
  - **Custom Agent:** `.gitlab/duo/agents/causal_healer.yml` (`causal-incident-healer`) instructed to reject stack-trace guessing and ground all fixes in graph facts.
  - **Duo Flow:** `.gitlab/duo/flows/incident_self_heal.yml` orchestrates triage → test compilation → AST repair → green verification → MR creation.
* **GitLab CI/CD Integration:**
  - Official `.gitlab-ci.yml` running the official `falkordb/falkordb:latest` service container.
  - Collapsible terminal sections (`section_start:...`).
  - Standard GitLab artifacts: `junit.xml` (test report widget) and `gl-code-quality-report.json` (Code Climate MR widget).
  - Automated MR opened on `heal/incident_8812` via GitLab REST API.

---

## 🚫 Video Production Rules (Zero Fluff, Zero Static Frames)

1. **NO Static Image Slideshows:**
   - Every single second of the video must be **active, live screen recording**.
   - No PowerPoint slides, no artificial transitions, no edited static screenshots pretending to be video.
2. **Raw Screen Footage from Real Chrome & Terminal:**
   - Real Chrome browser session showing real tabs:
     1. The E-commerce Checkout / Store session where the order crashes.
     2. The live FalkorDB Browser (`http://localhost:3000`) querying graphs and rendering nodes.
     3. The live GitLab session (`gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43388957/showcase`) showing the unsolved pipeline/issue.
     4. The live terminal running `python demo/showcase.py` with real ANSI colors and real `pytest` exit codes.
     5. The resolved GitLab Merge Request !1 with green CI pipeline and code quality reports.
3. **Bullet-Point Narration Style (Inspired by `https://youtu.be/VNU-fs60FE0`):**
   - Direct, technical, engineer-to-engineer speech.
   - Bulleted structure: state the problem clearly, show the tool in action, explain the graph hop, show the test fail (RED), show the fix pass (GREEN), show the MR.
   - Pacing: ~3 to 5 minutes of focused walkthrough.

---

## 🎬 The Bullet-Point Walkthrough Script (Shot-by-Shot)

### 📌 STEP 1: The Incident in Production (Chrome Tab 1)
* **What to Show:** Chrome showing the Storefront / Checkout page (`demo/demo.html` or E-commerce UI).
* **Live Action:** Navigate to order `8812`. Show status = `CANCELLED` even though payment was authorized. Show the HTTP 500 `InvalidStateError`.
* **Talking Points (Bullet Points):**
  * "Here is our e-commerce checkout service. A customer authorized a payment for order #8812."
  * "Five seconds later, the order status inexplicably reads `CANCELLED`."
  * "The APM stack trace blames `capture_payment` inside `checkout.py`."
  * "But that function is completely innocent—it's just refusing to capture an already-cancelled order."
  * "The true collision happened 40ms earlier in a background cart-expiry worker on another trace."

### 📌 STEP 2: The Unsolved Incident in GitLab (Chrome Tab 2)
* **What to Show:** GitLab Project UI (`/pipelines` or `/jobs` or Issue #1).
* **Live Action:** Click into the failed `self_heal:scenario: [checkout]` job log. Scroll to the failure.
* **Talking Points (Bullet Points):**
  * "Over in GitLab, our CI pipeline failed on the production workload."
  * "The job log shows 253 concurrent spans captured."
  * "Standard logs give us no way to connect the payment webhook to the cart-expiry worker."
  * "In traditional APMs, they are isolated vertical silos with different request IDs."

### 📌 STEP 3: How FalkorDB Sees the Hidden Collision (Chrome Tab 3)
* **What to Show:** Live FalkorDB Browser UI at `http://localhost:3000`.
* **Live Action:** Select graph `demo`. Run the live Cypher query:
  ```cypher
  MATCH (s:Span)-[r:WROTE_ENTITY]->(e:StateEntity {key: 'orders:8812'})
  RETURN s.id, s.endpoint, e.key, r.ts_ns
  ```
  Expand the node visualization. Click between the two colliding spans.
* **Talking Points (Bullet Points):**
  * "Here is the FalkorDB Browser running on localhost:3000."
  * "Notice how FalkorDB models the system: every read and write points to a shared `(:StateEntity)` node."
  * "When we run this Cypher query, FalkorDB instantly traverses across traces."
  * "In under 2 milliseconds, it reveals: span `s1f004a74c964` (the webhook) and span `scb38927fbb7b` (the worker) both wrote to `orders:8812` without taking a lock."
  * "FalkorDB's `shortestPath` algorithm prunes 250 background spans down to just the 3 causal participants."
  * "Without FalkorDB, this cross-trace collision is literally invisible."

### 📌 STEP 4: Autonomous Hands-Off Healing in Terminal (Split Screen / Terminal)
* **What to Show:** Live PowerShell / Terminal window.
* **Live Action:** Run `python demo/showcase.py` live. Watch the stages scroll:
  - Stage 8: Monitor (Crash reproduced)
  - Stage 1: Plan (FalkorDB triage)
  - Stage 3: Verify (Pipeline #1 RED - exit=1)
  - Stage 2: Create (AST repair with `with_for_update()`)
  - Stage 3: Verify (Pipeline #2 GREEN - exit=0)
  - Stage 9: Govern (FalkorDB lock proof `[False]` -> `[False, True]`)
* **Talking Points (Bullet Points):**
  * "Now watch the autonomous GitLab Duo self-healing loop run end to end."
  * "Stage 1: FalkorDB extracts the exact replay subgraph."
  * "Stage 3: Compiles `test_incident_8812.py` in pytest. Exit code 1 — the race is deterministically reproduced."
  * "Stage 2: AST surgery injects `SELECT FOR UPDATE` and an idempotency guard into *both* unlocked writers."
  * "Stage 3 re-runs the test: Exit code 0 — verified green in under 1 second."
  * "Stage 9: Telemetry is re-ingested into FalkorDB to mathematically prove the lock state changed from False to True."

### 📌 STEP 5: The Solved Merge Request in GitLab (Chrome Tab 3)
* **What to Show:** GitLab Merge Request !1 (`heal/incident_8812`).
* **Live Action:** Scroll through the MR overview, the pipeline status (Green), the Code Quality widget, and the diff.
* **Talking Points (Bullet Points):**
  * "Back in GitLab, the agent has autonomously opened Merge Request !1."
  * "The MR includes the full causal collision table from FalkorDB."
  * "GitLab Code Quality widget highlights the exact resolved concurrency hazard."
  * "GitLab JUnit report confirms 2 tests passing."
  * "Zero humans were needed from production crash to verified Merge Request."

### 📌 STEP 6: Generalization: 3 Services, 3 Bugs, 1 Engine (Chrome Tab 2)
* **What to Show:** `demo/demo.html` switcher tabs.
* **Live Action:** Click between **checkout** (lost update), **inventory** (oversell), and **payments** (double charge).
* **Talking Points (Bullet Points):**
  * "This isn't hardcoded to checkout."
  * "Scenario 2: An inventory oversell on `stocks:4471` where goods receipts race with expiry sweeps."
  * "Scenario 3: A double charge on `payments:8812` where webhook retries duplicate ledger entries."
  * "All 3 closed by the same engine using FalkorDB as the context layer and GitLab Duo as the autonomous DevSecOps agent."

### 📌 STEP 7: Technical Checklist & Hackathon Rubric Compliance
* **What to Show:** Full architecture and standing metrics card (3/3 incidents healed in 14.2s, 98.81% noise pruned).
* **Talking Points (Bullet Points):**
  * "For FalkorDB Track 1: FalkorDB is the essential context layer connecting disjoint traces through shared StateEntity nodes."
  * "For GitLab Transcend Hands-Off Track: The GitLab Duo MCP server, custom causal healer agent, and CI/CD pipelines automate the entire path from incident to green Merge Request."

---

## 📊 Broadcast Production Assets & Timings

* **Master Video Output:** `demo/trace_repro_final_demo.mp4`
* **Resolution:** 1936x1056 High Definition @ 20 FPS (H.264 / AAC)
* **Total Duration:** 314.95 seconds (5.25 minutes)
* **Zero Static Slides:** 100% full-motion, live-interactive screen recording captured directly from user's Chrome desktop session.

| Scene | Title | Source / Window | Duration |
|---|---|---|---|
| 01 | The Production Incident | Chrome Tab 1 (`Vaayu Electronics` Oscar Storefront) | 53.49s |
| 02 | The Unsolved Incident in GitLab CI | Chrome Tab 4 (Job Log #17230374712) & Tab 3 (MR !1) | 41.30s |
| 03 | FalkorDB — Essential Context Layer | Chrome Tab 5 (FalkorDB Browser `orders:8812` Cypher) | 59.01s |
| 04 | Autonomous Hands-Off Healing | Chrome Tab 2 (`demo.html` Sequential Terminal Stages 1-6) | 58.57s |
| 05 | Solved Merge Request in GitLab | Chrome Tab 3 (GitLab MR !1) | 23.76s |
| 06 | Generalization Across 3 Services | Chrome Tab 2 (`demo.html` Switcher) | 37.80s |
| 07 | Live Resolution: Healed Checkout | Chrome Tab 1 (Storefront Checkout to Confirmation) | 31.42s |
| **Total** | **Full Broadcast Walkthrough** | **Chrome Live Session (1936x1056 @ 20fps)** | **305.35s (5.08m)** |

