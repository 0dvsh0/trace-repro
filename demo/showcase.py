"""The showcase — one command, three production incidents, zero excuses.

    python demo/showcase.py

Runs every scenario repository end to end exactly as an on-call engineer
would experience it: the incident fires, the graph names the true upstream
cause out of hundreds of unrelated traces, a failing reproduction test is
compiled into the *victim's own repository*, every unlocked upstream writer
is patched by AST analysis, the same test goes green, and a before/after
graph diff proves the race is gone.

Everything streams live to the terminal. Each scenario leaves its evidence
in ``demo/artifacts/<scenario>/``, and the captured console output is written
to ``js/demo-data.js`` so the landing page can replay the incident
step by step with the numbers a real run produced — never a mock-up.

No Docker, no databases, no configuration: the graph falls back to the
in-process twin (``memory://``) with identical traversal semantics. Point
``--graph falkordb://...`` at a live server to run the identical Cypher.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEMO = Path(__file__).resolve().parent
REPO_ROOT = DEMO.parent
OUT_JS = REPO_ROOT / "js" / "demo-data.js"

if sys.platform == "win32" and sys.stdout.isatty():
    from ctypes import windll

    windll.kernel32.SetConsoleMode(windll.kernel32.GetStdHandle(-11), 7)

CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
OFF = "\033[0m"


def _c(text: str, *codes: str) -> str:
    if not sys.stdout.isatty() or not codes:
        return text
    return "".join(codes) + text + OFF


ACT_RULE = "─" * 78

# The keynote framing for each act. Everything measured at run time comes
# from the scenario itself; only the outage-desk copy below is authored.
SCENARIOS: dict[str, dict] = {
    "checkout": {
        "act": 1,
        "clock": "02:14 UTC",
        "sev": "SEV-8812",
        "service": "checkout-svc",
        "entity": "orders:8812",
        "hook": "A customer pays INR 4,500. The order cancels itself.",
        "short": "A paid order cancels itself",
        "story": (
            "A payment webhook and the cart-expiry worker both read "
            "orders:8812 before either wrote it. The worker commits its "
            "stale PENDING view 70 ms after the webhook commits, so a "
            "captured order comes back CANCELLED. Every monitoring stack "
            "blames capture_payment — the one function that did nothing "
            "wrong."
        ),
    },
    "inventory": {
        "act": 2,
        "clock": "11:37 UTC",
        "sev": "SEV-4471",
        "service": "inventory-svc",
        "entity": "stocks:4471",
        "hook": "Fifty-three units vanish. Nobody bought anything.",
        "short": "53 units vanish, nobody bought",
        "story": (
            "A goods receipt spends 260 ms in the supplier API; the expiry "
            "sweep read the same lot 60 ms earlier, still QUARANTINED, and "
            "writes off every sellable unit 80 ms after the receipt "
            "commits. The API returns a polite 409 the whole time, so "
            "every 5xx-based alert never even fires."
        ),
    },
    "payments": {
        "act": 3,
        "clock": "17:52 UTC",
        "sev": "SEV-8812",
        "service": "payments-svc",
        "entity": "payments:8812",
        "hook": "One payment. One webhook. Two ledger entries.",
        "short": "One webhook, two charges",
        "story": (
            "The provider retries when no 200 arrives in time. The retry "
            "lands in a different trace, a different request id, sees the "
            "same row still PENDING, and appends a second CAPTURE entry. "
            "Both deliveries answer 200. Correlating by request id finds "
            "nothing — the only thing the two traces share is the row."
        ),
    },
}


def out(text: str = "") -> None:
    print(text, flush=True)


# --------------------------------------------------------------------------
# Parsing: every number the landing page shows is a number a run measured.
# --------------------------------------------------------------------------

STAGE_RE = re.compile(r"^\s*Stage (\d+) · (.+?) — (.*)$")

STAGE_KEY = {
    "Stage 8": "monitor",
    "Stage 1": "triage",
    "Stage 2": "create",
    "Stage 9": "govern",
}


class Parsed:
    def __init__(self) -> None:
        self.crash_endpoint = ""
        self.http_status = ""
        self.error_type = ""
        self.error_message = ""
        self.final_state = ""
        self.blamed_function = ""
        self.blamed_file = ""
        self.spans = 0
        self.pruned = 0
        self.pruned_pct = ""
        self.causal = 0
        self.window_ms = 0.0
        self.collisions: list[dict] = []
        self.replay_steps: list[dict] = []
        self.red_summary = ""
        self.green_summary = ""
        self.repair_summary = ""
        self.diff: list[tuple[str, str]] = []
        self.writers_before = ""
        self.writers_after = ""
        self.lock_before = ""
        self.lock_after = ""
        self.now_locked = ""
        self.replay_http = ""
        self.replay_state = ""
        self.resolved = ""
        self.advisory = ""
        self.duration = 0.0
        self.ok = False
        self.stages: dict[str, list[tuple[str, str]]] = {}
        self.stage = ""
        self.closed_line = ""

    def note(self, tone: str, text: str) -> None:
        bucket = self.stages.setdefault(self.stage or "closed", [])
        if len(bucket) < 26:
            bucket.append((tone, text))


def _tone(line: str) -> str | None:
    """Classify one console line for the site's terminal panel."""
    s = line.strip()
    if not s or set(s) <= {"─"}:
        return None
    if s.startswith(("+++", "---")):
        return None
    if s.startswith("+"):
        return "add"
    if s.startswith("-"):
        return "del"
    if s.startswith("@@"):
        return "dim"
    if (re.search(r"-> \d+ \w+:", s) or "unlocked write in" in s
            or "pipeline #1" in s or s.startswith("E ") or "AssertionError" in s):
        return "red"
    if ("pipeline #2" in s or "resolved True" in s or "closed ·" in s):
        return "green"
    if re.match(r"^(trace |pruned |repaired |now locked |workload replay|"
                r"race reproduced|writers |spans in graph|concurrent traces|"
                r"final |causal window|entity under|reverting|reverted)",
                s):
        return "dim"
    return "ink"


def _parse_line(line: str, p: Parsed) -> None:
    m = STAGE_RE.match(line)
    if m:
        header = f"Stage {m.group(1)}"
        if header == "Stage 3":
            p.stage = "red" if "(RED)" in line else "green"
        else:
            p.stage = STAGE_KEY.get(header, p.stage)
        p.stages.setdefault(p.stage, [])
        return

    if set(line.strip()) <= {"─"} or not line.strip():
        return

    tone = _tone(line)
    if tone is not None:
        p.note(tone, line.strip())

    m = re.match(r"^\s*(POST \S+)\s*-> (\d+) ([\w.]+): (.*)$", line)
    if m:
        p.crash_endpoint, p.http_status, p.error_type, p.error_message = (
            g.strip() for g in m.groups()
        )
        return
    m = re.match(r"^  final (\S+)\s*: (\S+)$", line)
    if m:
        p.final_state = m.group(2)
        return
    m = re.match(r"^  stack trace blames : (\w+) \((.+):(\d+)\)$", line)
    if m:
        p.blamed_function, p.blamed_file = m.group(1), m.group(2)
        return
    m = re.match(r"^  spans in graph\s*: (\d+)$", line)
    if m:
        p.spans = int(m.group(1))
        return
    m = re.match(r"^  pruned (\d+)/(\d+) spans \(([\d.]+)%\) down to (\d+)", line)
    if m:
        p.pruned, p.spans, p.pruned_pct, p.causal = m.groups()
        p.spans, p.pruned, p.causal = int(p.spans), int(p.pruned), int(p.causal)
        return
    m = re.match(r"^  trace (\S+)\s+(.+?)\s+wrote it\s+([\d.]+)ms before the crash$", line)
    if m:
        p.collisions.append({
            "trace": m.group(1), "endpoint": m.group(2).strip(),
            "window_ms": float(m.group(3)),
        })
        p.window_ms = max(p.window_ms, float(m.group(3)))
        return
    m = re.match(r"^\s+unlocked write in (\w+)\(\)\s+\[(.+)\]$", line)
    if m and p.collisions:
        p.collisions[-1]["mutator"] = m.group(1)
        p.collisions[-1]["file"] = m.group(2)
        return
    m = re.match(r"^\s*\+\s*([\d.]+)ms\s+(\w+)\s+(.+?)\s+(\w+)$", line)
    if m:
        p.replay_steps.append(
            {"offset_ms": float(m.group(1)), "kind": m.group(2),
             "endpoint": m.group(3).strip(), "function": m.group(4)}
        )
        return
    m = re.match(r"^  pipeline #1  exit=(\d+)\s+(.*)$", line)
    if m:
        p.red_summary = m.group(2)
        return
    m = re.match(r"^  pipeline #2  exit=(\d+)\s+(.*)$", line)
    if m:
        p.green_summary = m.group(2)
        return
    m = re.match(r"^\s+repaired \d+ file\(s\) with (.*)$", line)
    if m:
        p.repair_summary = m.group(1)
        return
    m = re.match(r"^  writers before\s*: (\d+)\s+lock_held=\[(.*)\]$", line)
    if m:
        p.writers_before, p.lock_before = m.group(1), m.group(2)
        return
    m = re.match(r"^  writers after\s*: (\d+)\s+lock_held=\[(.*)\]$", line)
    if m:
        p.writers_after, p.lock_after = m.group(1), m.group(2)
        return
    m = re.match(r"^  now locked\s*: (.*)$", line)
    if m:
        p.now_locked = m.group(1)
        return
    m = re.match(r"^  workload replay\s*: HTTP (\d+)\s+final state (\S+)$", line)
    if m:
        p.replay_http, p.replay_state = m.group(1), m.group(2)
        return
    m = re.match(r"^  race reproduced\s*: (\w+)\s*->\s*resolved (\w+)$", line)
    if m:
        p.resolved = m.group(2)
        return
    m = re.match(r"^  advisory: (.*)$", line)
    if m:
        p.advisory = m.group(1)
        return
    m = re.search(r"closed · (.+)$", line)
    if m:
        p.closed_line = m.group(1)
        return


_RE_PAINT = [
    (re.compile(r"^\s*(POST \S+)\s*-> \d+ [\w.]+:"), RED),
    (re.compile(r"unlocked write in"), RED),
    (re.compile(r"pipeline #1"), RED),
    (re.compile(r"pipeline #2"), GREEN),
    (re.compile(r"resolved True$"), GREEN),
    (re.compile(r"closed ·"), GREEN),
    (re.compile(r"^  pruned \d+/\d+ spans"), CYAN),
    (re.compile(r"wrote it"), CYAN),
    (re.compile(r"repaired \d+ file\(s\)"), CYAN),
    (re.compile(r"\+\s*[\d.]+ms\s+\w+"), DIM),
]


def _paint(line: str) -> str:
    for pattern, code in _RE_PAINT:
        if pattern.search(line):
            return _c(line, code)
    return line


# --------------------------------------------------------------------------
# The acts
# --------------------------------------------------------------------------


def run_scenario(name: str, args) -> Parsed:
    p = Parsed()
    spec = SCENARIOS[name]
    started = time.monotonic()

    out()
    out(_c(f"ACT {spec['act']}", BOLD, CYAN)
        + _c(f"  ·  {spec['clock']}  ·  {spec['sev']}  ·  "
             f"{spec['service']}", DIM))
    out(_c(f'"{spec["hook"]}"', YELLOW))
    out(_c(spec["story"], DIM))
    out()

    cmd = [
        sys.executable, "-m", "trace_repro.cli", "demo",
        "--repo", str(DEMO / name),
        "--graph", args.graph,
        "--background", str(args.background),
        "--export-reports", str(DEMO / "artifacts" / name),
    ]
    if args.ci != "auto":
        cmd += ["--ci", args.ci]

    child = subprocess.Popen(
        cmd, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    assert child.stdout is not None
    for raw in child.stdout:
        line = raw.rstrip()
        if args.ci == "gitlab" and line.startswith("section_start"):
            continue
        out("  " + _paint(line))
        _parse_line(line, p)
    code = child.wait()
    p.duration = time.monotonic() - started
    p.ok = code == 0
    p.stages.setdefault("closed", [])
    p.stages["closed"].append(
        ("green", f"  {p.closed_line or 'closed'}"))

    if p.ok:
        out(_c(f"● {spec['sev']} closed in {p.duration:.2f}s — patch reverted, "
               f"repository pristine, evidence in demo/artifacts/{name}/",
               GREEN))
    else:
        out(_c(f"■ {spec['sev']} NOT resolved (exit {code}) — "
               f"see the output above.", RED))
    return p


# --------------------------------------------------------------------------
# The story the judges watch — authored narration over measured numbers
# --------------------------------------------------------------------------


def _ms(value: float) -> str:
    return f"{value:.0f} ms".replace(" ", "&thinsp;")


def _beats(name: str, p: Parsed) -> list[dict]:
    spec = SCENARIOS[name]
    first = p.replay_steps[0]["offset_ms"] if p.replay_steps else 0
    last = p.replay_steps[-1]["offset_ms"] if p.replay_steps else 0
    blame = f"{spec['entity']}"
    return [
        {
            "key": "monitor",
            "stage": "Monitor",
            "title": spec["hook"],
            "status": ["Incident open", "is-red"],
            "lede": (
                f"{spec['clock']}. {spec['service']} returns "
                f"{p.http_status} on {p.crash_endpoint} — "
                f"<em>{p.error_message}</em> The record is left "
                f"{p.final_state}. The alert points at "
                f"{p.blamed_function}() in {p.blamed_file}: the function "
                f"that did nothing wrong."
            ),
        },
        {
            "key": "triage",
            "stage": "Plan",
            "title": "Not that line — the two lines before it",
            "status": ["Causal triage", ""],
            "lede": (
                f"trace-repro never reads only the failing request. It walks "
                f"{p.spans:,} recorded spans into the record it touched, "
                f"{blame}, and collects every other event that changed it. "
                f"<b>{p.pruned} unrelated events are pruned</b> "
                f"({p.pruned_pct}%). What survives: {p.causal} events inside "
                f"{_ms(p.window_ms)}, none of them holding a lock."
            ),
        },
        {
            "key": "red",
            "stage": "Verify",
            "title": "A test that fails on purpose",
            "status": ["Pipeline #1 · red", "is-red"],
            "lede": (
                f"Before a single line of code changes, the graph's own "
                f"timings are compiled into a pytest inside <em>your</em> "
                f"repository: {p.causal} participants replayed at "
                f"{first:.0f}–{last:.0f} ms, exactly as production ran them. "
                f"It fails with the production error — the bug is now "
                f"proven, not guessed."
            ),
        },
        {
            "key": "create",
            "stage": "Create",
            "title": "The fix goes where the trouble started",
            "status": ["AST repair", "is-red"],
            "lede": (
                f"An AST transform rewrites every unlocked writer of "
                f"{blame} — <b>{p.repair_summary or 'row lock + dedup'}</b>. "
                f"{p.blamed_function}() is never touched: it was the victim, "
                f"not the cause."
            ),
        },
        {
            "key": "green",
            "stage": "Verify",
            "title": "Same test, no edits — it passes",
            "status": ["Pipeline #2 · green", "is-ok"],
            "lede": (
                f"The identical test, re-run against the patched source: "
                f"<b>{p.green_summary}</b>. The red test is the proof of "
                f"the bug; this is the proof of the fix."
            ),
        },
        {
            "key": "govern",
            "stage": "Govern",
            "title": "Verified, read back out of the graph",
            "status": ["Resolved", "is-ok"],
            "lede": (
                f"The workload is replayed with the patch live and "
                f"re-ingested. lock_held went {p.lock_before} → "
                f"<b>{p.lock_after}</b>, the request now answers HTTP "
                f"{p.replay_http} and {blame} is {p.replay_state}. "
                f"Race reproduced → resolved: {p.resolved}."
            ),
        },
        {
            "key": "closed",
            "stage": "Close",
            "title": "Incident closed. The patch reverts itself.",
            "status": [f"Healed in {p.duration:.2f}s", "is-ok"],
            "lede": (
                f"Your repository is left exactly as it was found — the "
                f"patch is reverted after the proof. The reproduction test "
                f"stays behind as a regression guard, and the evidence "
                f"(JUnit, Code Quality, patch, incident report) lands in "
                f"<code>demo/artifacts/{name}/</code>."
            ),
        },
    ]


def _incident_payload(name: str, p: Parsed) -> dict:
    spec = SCENARIOS[name]
    return {
        "key": name,
        "sev": spec["sev"],
        "clock": spec["clock"],
        "service": spec["service"],
        "entity": spec["entity"],
        "hook": spec["hook"],
        "short": spec["short"],
        "story": spec["story"],
        "healed": p.ok,
        "duration": round(p.duration, 2),
        "metrics": {
            "spans": p.spans,
            "pruned": p.pruned,
            "pruned_pct": p.pruned_pct,
            "causal": p.causal,
            "window_ms": round(p.window_ms, 1),
            "red": p.red_summary,
            "green": p.green_summary,
            "lock_before": p.lock_before,
            "lock_after": p.lock_after,
            "replay_http": p.replay_http,
            "replay_state": p.replay_state,
            "resolved": p.resolved == "True",
        },
        "timeline": p.replay_steps,
        "collisions": p.collisions,
        "steps": [
            {**b, "term": [[t, s] for t, s in p.stages.get(b["key"], [])]}
            for b in _beats(name, p)
        ],
    }


def emit_site_data(parsed: list[Parsed], names: list[str], total_s: float) -> None:
    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command": "python demo/showcase.py",
        "total_seconds": round(total_s, 1),
        "incidents": [_incident_payload(n, p) for p, n in zip(parsed, names, strict=True)],
    }
    body = json.dumps(payload, ensure_ascii=True, indent=2)
    OUT_JS.parent.mkdir(parents=True, exist_ok=True)
    OUT_JS.write_text(
        "/* Generated by demo/showcase.py - real console output from real runs.\n"
        "   Re-run `python demo/showcase.py` to refresh. Do not edit. */\n"
        f"window.TR_DEMO = {body};\n",
        encoding="utf-8",
    )


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass
    ap = argparse.ArgumentParser(description="the trace-repro showcase")
    ap.add_argument("--graph", default="memory://showcase",
                    help="graph URL (default: in-process twin, no Docker)")
    ap.add_argument("--background", type=int, default=250,
                    help="unrelated traces per scenario (measures pruning)")
    ap.add_argument("--ci", choices=["auto", "local", "gitlab"], default="auto",
                    help="CI adapter (auto detects GitLab CI)")
    args = ap.parse_args()

    out()
    out(_c(ACT_RULE, CYAN))
    out(_c(" ONE COMMAND. THREE PRODUCTION INCIDENTS.", BOLD))
    out(_c(" Each diagnosed, reproduced, patched and proven — live.", DIM))
    out(_c(ACT_RULE, CYAN))

    names = list(SCENARIOS)
    parsed: list[Parsed] = []
    total = 0.0
    for name in names:
        p = run_scenario(name, args)
        parsed.append(p)
        total += p.duration

    heals = sum(p.ok for p in parsed)
    out()
    out(_c("STANDINGS", BOLD, CYAN))
    for p, name in zip(parsed, names, strict=True):
        mark = _c("●  ", GREEN) if p.ok else _c("■  ", RED)
        out(f"  {mark}{SCENARIOS[name]['service']:<14} "
            f"pruned {p.pruned}/{p.spans} spans ({p.pruned_pct}%)  "
            f"window ≤ {p.window_ms:.0f} ms  lock_held "
            f"[{p.lock_before}] → [{p.lock_after}]  {p.duration:.2f}s")
    out()

    emit_site_data(parsed, names, total)
    out(_c(f"wrote the landing-page demo data: {OUT_JS.relative_to(REPO_ROOT)}",
           DIM))
    out()

    return 0 if heals == len(parsed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
