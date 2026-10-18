# 🎬 trace-repro — Ultimate Hackathon Demo Script (5:30)

> **Dual Hackathon Submission:**
> 1. **FalkorDB Graph Hacks — Track 1:** *Agents That Act on Connected Data*
> 2. **GitLab Transcend: Life After Code — Track:** *Hands-Off Autonomous DevSecOps*
>
> **Target Video Runtime:** 5 minutes 30 seconds  
> **Recording Medium:** Real Google Chrome Browser + Real Terminal / VS Code

---

## 📋 The Judge's Checklist (Self-Audit)

Before recording, ensure every single one of these points is visibly checked off in the video:

### 🟣 FalkorDB Track 1: "Agents That Act on Connected Data"
- [x] **Clear Problem Statement:** LLMs & vector search cannot correlate independent, concurrent traces that share no IDs.
- [x] **FalkorDB as Core Context:** The `(:StateEntity)` property graph is the *only* link connecting independent traces.
- [x] **Live Cypher Traversals:** Shows multi-hop Cypher queries traversing `Span -> StateEntity <- Span`.
- [x] **Graph Data Model:** Schema explained with nodes (`Trace`, `Span`, `StateEntity`, `StateMutation`, `ASTFunction`) and relationships (`HAPPENED_BEFORE`, `CAUSED_READ`, `WROTE_ENTITY`).
- [x] **Graph Algorithms:** Graph pruning using `shortestPath` over `HAPPENED_BEFORE | CAUSED_READ` (prunes 250 background spans down to 3 causal participants = 98.8% noise reduction).
- [x] **Traceability & Proof:** Before/after lock state read *directly back from the graph* (`[False]` → `[False, True]`).
- [x] **The Acid Test:** "If we remove FalkorDB, the system is blind and cannot function."

### 🟠 GitLab Transcend: "Hands-Off DevSecOps"
- [x] **Zero Human Intervention:** Incident ingestion → triage → failing test compilation → AST source repair → green test verification → MR creation.
- [x] **GitLab Duo Agent Platform:** Custom Agent definition (`causal_healer.yml`), Duo Flow (`incident_self_heal.yml`), and Model Context Protocol (`mcp.json`).
- [x] **Native GitLab CI/CD:** Real `.gitlab-ci.yml` running with `falkordb/falkordb:latest` service container.
- [x] **Autonomous RED → GREEN Loop:** Real `pytest` subprocess runs: Pipeline #1 exits with code `1` (RED), Pipeline #2 exits with code `0` (GREEN).
- [x] **Standard GitLab Artifacts:** Generates `junit.xml` (test report widget) and `gl-code-quality-report.json` (Code Climate MR widget).
- [x] **Automated Merge Request:** Branch `heal/incident_8812` created with reproducible test and full causal breakdown via GitLab REST API.

---

## 💡 Simple Terms: Why FalkorDB & GitLab Solve This

> **Keep these two simple analogies in mind during your delivery:**

### Why FalkorDB? (In Plain English)
> *"Imagine two bank tellers on opposite sides of a city. Teller A deposits $1,000 into Account #8812 at 2:00:00.00 PM. Teller B charges a $50 maintenance fee to Account #8812 at 2:00:00.04 PM. Neither knows the other exists. When the audit fails 10 minutes later, the auditor blames the computer. 
> 
> Traditional tracing tools treat traces as isolated vertical silos: Trace 1, Trace 2, Trace 3. They share no trace ID, no request ID, no correlation ID. An LLM searching logs will hallucinate because there is no link. 
> 
> FalkorDB creates the horizontal bridge. In FalkorDB, the database row (`orders:8812`) is a node, and both traces have edges pointing into it. One Cypher traversal reveals: **these two independent traces collided on the exact same row 40 milliseconds apart.** Without FalkorDB, finding this in millions of log lines is statistically impossible."*

### Why GitLab Duo & Hands-Off DevSecOps? (In Plain English)
> *"Knowing the root cause is only half the battle. Who fixes it at 3 AM? 
> 
> In our hands-off loop, GitLab Duo doesn't just chat. It acts as an autonomous site reliability engineer:
> 1. It compiles a deterministic `pytest` reproducing the exact race timing.
> 2. It runs GitLab CI to prove the failure — **RED (exit code 1)**.
> 3. It parses the Python AST to inject `SELECT FOR UPDATE` and transaction locks into *every* contending writer.
> 4. It re-runs GitLab CI to prove the fix — **GREEN (exit code 0)**.
> 5. It opens an automated GitLab Merge Request with JUnit test reports and Code Quality badges. Zero human intervention from outage to merge request."*

---

## 🎬 Complete Scene-by-Scene Teleprompter Script

### ⏱ ACT 0: The Cold Open Hook (0:00 – 0:35)
**Visual Setup in Chrome:**
- Have Chrome open on the demo web interface (`demo/demo.html` or `http://localhost:8000/demo/demo.html`).
- Start zoomed into the Hero header: *"Three services. Three concurrency bugs. One engine that closes them."*
- Click into the **checkout** tab.

**Spoken Narration (Confident, energetic, clear):**
> "It's Friday night. An e-commerce customer pays for an order. Stripe confirms payment authorization. But five seconds later, your database says the order is **CANCELLED**.
> 
> Your on-call engineer gets paged. They open Datadog or Sentry. The stack trace points an accusing finger at `capture_payment` inside `checkout.py`. 
> 
> But here’s the terrifying truth: **that code is completely innocent.** It’s just the victim.
> 
> The real bug happened 40 milliseconds earlier, in a background cart-expiry cron worker, on a completely different thread, that shares *zero* trace IDs or request IDs with the payment webhook.
> 
> Welcome to `trace-repro`: an autonomous, cross-trace causal self-healing engine powered by **FalkorDB** and **GitLab Duo**."

---

### ⏱ ACT 1: Anatomy of an Invisible Production Collision (0:35 – 1:25)
**Visual Setup in Chrome:**
- Scroll smoothly down to **#incident: The Incident** section.
- Highlight the **contending traces table** (`POST /webhooks/payment` vs `cart-expiry worker`).
- Point mouse cursor to the timestamps and the `WAW (Write-After-Write)` hazard indicator.

**Spoken Narration:**
> "Let’s look at the real production telemetry. We derived this directly from a real-world FastAPI + SQLAlchemy e-commerce architecture.
> 
> Look at this contention timeline on order `orders:8812`:
> 
> - At **T = 0 ms**, a Stripe payment webhook arrives. It reads the order as `PENDING`, calls Stripe's payment gateway—which takes 260 milliseconds—and prepares to write `AUTHORIZED`.
> - But at **T = 190 ms**, while that gateway call is still in-flight, a cart-expiry worker wakes up. It also reads the order as `PENDING`. It rehydrates for 140 milliseconds, and at T = 330 ms, it writes `CANCELLED`.
> - At **T = 560 ms**, `capture_payment` runs, sees `CANCELLED`, and crashes with an HTTP 500 `InvalidStateError`.
> 
> This is a classic distributed Lost Update. The background worker overwrote the payment webhook with stale data because neither transaction acquired a database lock! 
> 
> Grepping logs won't find this. RAG and vector databases won't find this. Why? Because these two requests never talked to each other."

---

### ⏱ ACT 2: Why FalkorDB IS the Context Layer (1:25 – 2:35)
**Visual Setup in Chrome & Terminal:**
- Scroll to **#graph: The graph, as traversed** section in `demo.html`.
- Click on the `falkordb-graph.png` and `falkordb-cli.png` thumbnails to expand them in high resolution using the built-in lightbox.
- Switch to a live terminal or FalkorDB CLI to show the real Cypher query.

**Spoken Narration:**
> "This is where FalkorDB becomes the irreplaceable brain of the system.
> 
> Traces in modern APMs are vertical trees. They don't intersect. But in `trace-repro`, every database read and write is indexed as a state mutation node connected to a shared `(:StateEntity)` property graph.
> 
> Look at our FalkorDB schema:
> - `(:Trace)` contains `(:Span)`.
> - `(:Span)` points via `[:READ_ENTITY]` or `[:WROTE_ENTITY]` to `(:StateEntity)`.
> - And mutations are sequenced chronologically with `[:HAPPENED_BEFORE]` and `[:CAUSED_READ]` edges.
> 
> When `capture_payment` crashes, `trace-repro` queries FalkorDB with Cypher:
> 
> ```cypher
> MATCH (crash:Span)-[:READ_ENTITY]->(entity:StateEntity)
> MATCH (other:Trace)-[:HAS_SPAN]->(mut:Span)-[:WROTE_ENTITY]->(entity)
> WHERE other.id <> crash.trace_id 
>   AND (crash.ts_ns - mut.ts_ns) < $window_ns
> RETURN other, mut, entity
> ```
> 
> In **under two milliseconds**, FalkorDB hops out of the crashing trace, walks through the entity node `orders:8812`, and discovers the background worker in trace `ef1e12b7`.
> 
> Even better: FalkorDB uses a graph shortest-path algorithm across `HAPPENED_BEFORE` edges to prune **250 background spans down to just the 3 causal participants**—eliminating 98.8% of distributed tracing noise.
> 
> If you rip out FalkorDB, you cannot make this connection. The graph isn't just storing data—it *is* the context layer that makes reasoning possible."

---

### ⏱ ACT 3: The Autonomous Hands-Off Loop: RED to GREEN (2:35 – 3:45)
**Visual Setup:**
- Split screen or switch to your Terminal.
- Run the command:
  ```powershell
  python demo/showcase.py
  ```
- Or run the scenario directly:
  ```powershell
  trace-repro demo --repo demo/checkout --graph memory://demo --ci gitlab
  ```
- Watch the output stream live with the ANSI colors and collapsible sections.

**Spoken Narration:**
> "Now let's watch the hands-off self-healing loop in action. No human intervention.
> 
> **Step 1: Deterministic Test Compilation.** 
> The engine takes the minimal replay subgraph from FalkorDB—the exact payloads, function endpoints, and millisecond offsets—and synthesizes a standalone `pytest` reproduction file: `test_incident_8812.py`. Notice: this test imports zero dependencies from trace-repro. It runs directly in the target repository using thread barriers.
> 
> **Step 2: Pipeline #1 — Proving the Failure (RED).**
> The agent triggers the test. Watch the terminal:
> `pytest tests/repro/ -v`
> **Exit code 1.** The production race condition is reproduced deterministically on demand!
> 
> **Step 3: Upstream AST Code Repair.**
> Instead of asking an LLM to guess a fix from a stack trace, `trace-repro` queries FalkorDB's `upstream_mutator` tool. FalkorDB flags that *both* `apply_webhook` and `expire_cart` performed unlocked writes.
> 
> The engine uses Python's Abstract Syntax Tree (AST) to atomically inject:
> 1. `with_for_update()` pessimistic row locking,
> 2. Session transaction isolation, and
> 3. An idempotency guard so webhook retries don't duplicate ledger entries.
> 
> **Step 4: Pipeline #2 — Verifying the Fix (GREEN).**
> The agent re-runs the exact same test against the patched code.
> Look at the exit code: **Exit code 0. Two passed.**
> 
> We have gone from production incident to verified regression test to working patch in seconds."

---

### ⏱ ACT 4: Deep Integration with GitLab Duo Agent Platform (3:45 – 4:40)
**Visual Setup in Chrome:**
- Return to Chrome on `demo.html` → scroll to **#agent: What the agent sees**.
- Highlight the **Live MCP Session transcript**.
- Click the thumbnails for `duo-agent.png`, `duo-flow.png`, and `gitlab-ci-yml.png` to show the real GitLab interface.

**Spoken Narration:**
> "How does this fit into GitLab's vision for 'Life After Code'?
> 
> `trace-repro` is built natively on the **GitLab Duo Agent Platform**:
> 
> 1. **Model Context Protocol (MCP):** Under `.gitlab/duo/mcp.json`, we expose a read-only MCP server giving GitLab Duo 6 specialized FalkorDB tools—including `get_cross_trace_collision_graph` and `upstream_mutator`. The agent cannot hallucinate patches because it is grounded in graph reality.
> 2. **Custom Duo Agent:** We defined `causal-incident-healer` in `.gitlab/duo/agents/causal_healer.yml`. Its system prompt explicitly commands: *'Do not guess the fix from the stack trace alone. Ground all decisions in the FalkorDB state entity graph.'*
> 3. **GitLab Duo Flow:** Configured in `incident_self_heal.yml`. When an alert webhook fires or a pipeline fails, Duo initiates the multi-stage DevSecOps self-healing sequence.
> 4. **Official GitLab CI:** In `.gitlab-ci.yml`, we spin up the official `falkordb/falkordb:latest` service container directly alongside the GitLab CI runner. Every pipeline job gets an isolated, ephemeral FalkorDB graph."

---

### ⏱ ACT 5: Tangible Proof & DevSecOps Governance (4:40 – 5:15)
**Visual Setup in Chrome:**
- Scroll down to **#ci: What it leaves behind**.
- Show the 5 exported artifacts: `junit.xml`, `gl-code-quality-report.json`, `self_heal.patch`, `repro_test.py`, `incident_report.md`.
- Click on `gitlab-mr.png` in the gallery to show the automated Merge Request opened on GitLab.

**Spoken Narration:**
> "To satisfy GitLab's Hands-Off criteria, look at the evidence left in the repository:
> 
> - **GitLab Test Reports:** `junit.xml` reports both the RED reproduction and the GREEN remediation directly in GitLab's pipeline test widget.
> - **GitLab Code Quality:** `gl-code-quality-report.json` flags the exact concurrency hazard and unlocked writer in the Merge Request diff widget.
> - **Automated Merge Request:** Branch `heal/incident_8812` was opened via the GitLab REST API. It includes the reproducible test, the AST patch, and the full causal analysis table.
> - **FalkorDB Graph Governance Proof:** Before merging, the workload is re-run and re-ingested into FalkorDB. The graph reads back:
>   *Writers lock state before: `[False]`.*
>   *Writers lock state after: `[False, True]`.*
> The remediation is mathematically proven by the graph before code reaches production."

---

### ⏱ ACT 6: The Grand Finale: 3 Services, 1 Engine (5:15 – 5:35)
**Visual Setup in Chrome:**
- Scroll back to the top switcher in `demo.html`.
- Click between **checkout** (Lost Update), **inventory** (Oversell), and **payments** (Double Charge).
- Show that all three have real data, real AST diffs, and real green pipelines.
- End on the clean, polished Hero view of `demo.html`.

**Spoken Narration:**
> "And this isn't a hardcoded one-trick pony.
> 
> Watch me switch tabs:
> - **Scenario 1:** A lost update on orders in our checkout service.
> - **Scenario 2:** An inventory oversell on stocks where an expiry sweep raced against supplier receipts.
> - **Scenario 3:** A double charge in payments where acquirer retries created duplicate ledger entries.
> 
> Three completely different services. Three different databases. Three concurrency failure modes.
> 
> One graph context layer in FalkorDB. One hands-off autonomous DevSecOps lifecycle in GitLab Duo.
> 
> This is `trace-repro`. Thank you!"

---

## 🎥 Recording Guide & Director's Tips

1. **Resolution & Scaling:** Set Windows display scaling to 100% or 125% and record at 1920x1080 (1080p 60fps).
2. **Browser Layout:** Use Google Chrome in fullscreen (F11 or clean window without bookmarks bar).
3. **Pacing:** Speak at ~130–140 words per minute. Don't rush; let the visual tabs switch smoothly so judges can read the tables.
4. **Terminal:** Use Windows Terminal with a clean dark theme (PowerShell or WSL). Ensure font size is at least 14pt so text is crisp on YouTube.
5. **Interactive Clicks:** Make sure to click the screenshots in `demo.html`—the lightbox modal pop-up is smooth and proves that high-res real captures exist behind every card!
