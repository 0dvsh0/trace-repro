"""Capture real screenshots of trace-repro — no mock-ups, no redrawing.

    python demo/screenshot.py                    # what this machine can do
    python demo/screenshot.py --falkordb         # + the real FalkorDB Browser

Every image this writes is a genuine capture of a real window:

* **console shots** — a real PowerShell window runs the real command, and the
  window is captured through the Windows GDI (``PrintWindow``), ANSI colours,
  scrollback and all;
* **browser shots** — a real Chromium (headless) renders a real page;
* **FalkorDB shots** — a real Chromium renders FalkorDB's own Browser UI
  against a live server, so the graph picture is FalkorDB's, not a drawing of
  it. Needs Docker (``docker run -d -p 6379:6379 falkordb/falkordb:latest``)
  and ``--falkordb``;
* **GitLab / Duo shots** — those need an authenticated GitLab session, so they
  cannot be faked here. Drop your own PNGs into ``demo/assets/manual/`` with the
  names listed by ``--list-manual`` and ``demo.html`` picks them up
  automatically. The shots committed here were taken out of a signed-in browser
  against the real project: the Merge Request, the failing ``self_heal`` job log,
  the pipeline, ``.gitlab-ci.yml``, the Duo agent and flow, plus the FalkorDB CLI.

Nothing in ``demo/assets`` is drawn by this project. If a slot has no real
screenshot, the page shows the exact command that produces one, and the section
headline turns amber instead of claiming a count it has not verified.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

DEMO = Path(__file__).resolve().parent
REPO_ROOT = DEMO.parent
ASSETS = DEMO / "assets"
MANUAL = ASSETS / "manual"

IS_WIN = sys.platform == "win32"
if IS_WIN:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# The slots demo.html expects. "cmd" is shown to the user when the real
# screenshot is missing, so the page never pretends.
CONSOLE_SLOTS: list[tuple[str, str, str]] = [
    ("console-incident", "The incident, as an engineer sees it",
     "python -m trace_repro.cli demo --repo demo/{sc} --graph memory://shot"),
    ("console-telemetry", "What the instrumentation collected",
     "python -m trace_repro.cli graph --graph memory://shot --json"),
    ("console-red", "RED — the compiled reproduction test",
     "python -m trace_repro.cli demo --repo demo/{sc} --graph memory://shot"),
    ("console-green", "GREEN — the same test, unedited",
     "python -m trace_repro.cli demo --repo demo/{sc} --graph memory://shot"),
    ("console-mcp", "GitLab Duo Agent querying the graph over MCP",
     "python -m trace_repro.cli mcp --graph memory://shot"),
]

BROWSER_SLOTS: list[tuple[str, str, str]] = [
    ("page-landing", "The product page this demo belongs to", "index.html"),
    ("page-demo", "This demo page", "demo/demo.html"),
]

FALKORDB_SLOTS: list[tuple[str, str, str]] = [
    ("falkordb-graph", "FalkorDB Browser — the live graph",
     "http://localhost:3000/graph?key={graph}"),
    ("falkordb-stats", "FalkorDB Browser — node counts",
     "http://localhost:3000/graph?key={graph}&query=match%20(n)%20return%20labels(n)%20count(*)"),
]

MANUAL_SLOTS: list[tuple[str, str, str]] = [
    ("gitlab-mr", "GitLab Merge Request !1 — the submission",
     "the MR overview: scope, description and pipeline widget"),
    ("gitlab-joblog", "GitLab CI — the job log that reproduced the incident",
     "the self_heal:scenario [checkout] log, scrolled to the 500"),
    ("gitlab-pipeline", "GitLab CI — the pipeline",
     "the pipeline graph: lint/test green, self_heal red"),
    ("gitlab-ci-yml", "GitLab CI — the job definition",
     ".gitlab-ci.yml, scrolled to the self_heal:scenario matrix job"),
    ("duo-agent", "GitLab Duo Agent — causal-incident-healer",
     "the agent definition and the MCP tools it calls"),
    ("duo-flow", "GitLab Duo Flow — incident-causal-self-heal",
     "the flow triggered by pipeline_failure"),
    ("falkordb-cli", "FalkorDB from the command line",
     "redis-cli / falkor-cli graph query output"),
]


# --------------------------------------------------------------------------
# Windows GDI — capture a real console window
# --------------------------------------------------------------------------

if IS_WIN:
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001 - older Windows
        pass

    # PowerShell 5.1's console only interprets ANSI escapes once virtual
    # terminal processing is on (on the *real* stdout handle), and paging a run
    # taller than the window needs an oversized buffer plus the cursor's
    # absolute buffer position, which $Host.UI.RawUI does not report.
    VT_SETUP = r"""
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class Win32 {
  [StructLayout(LayoutKind.Sequential)]
  public struct COORD { public short X; public short Y; }
  [StructLayout(LayoutKind.Sequential)]
  public struct SMALL_RECT { public short Left, Top, Right, Bottom; }
  [StructLayout(LayoutKind.Sequential)]
  public struct CONSOLE_SCREEN_BUFFER_INFO {
    public COORD Size;
    public COORD CursorPosition;
    public short Attributes;
    public SMALL_RECT Window;
    public COORD MaximumWindowSize;
  }
  [DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int nStdHandle);
  [DllImport("kernel32.dll")] public static extern bool GetConsoleScreenBufferInfo(IntPtr h, out CONSOLE_SCREEN_BUFFER_INFO info);
  [DllImport("kernel32.dll")] public static extern bool SetConsoleMode(IntPtr h, uint mode);
}
'@
$script:CON = [Win32]::GetStdHandle(-11)
[void][Win32]::SetConsoleMode($script:CON, 7)
chcp 65001 > $null
$raw = $Host.UI.RawUI
try { $raw.BufferSize  = New-Object System.Management.Automation.Host.Size(200, 4000) } catch { }
try { $raw.WindowSize = New-Object System.Management.Automation.Host.Size(176, 52)  } catch { }
function Get-OutputBottom {
  $info = New-Object Win32+CONSOLE_SCREEN_BUFFER_INFO
  [void][Win32]::GetConsoleScreenBufferInfo($script:CON, [ref]$info)
  return $info.CursorPosition.Y
}
"""

    PW_RENDERFULLCONTENT = 0x00000002

    def _enum_window_for_pid(pid: int) -> int:
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, _lparam):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True

        user32.EnumWindows(cb, 0)
        return found[0] if found else 0

    def capture_window(hwnd: int, out_path: Path) -> bool:
        """PrintWindow into a PNG — the window as the user sees it."""
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return False
        hdc = user32.GetWindowDC(hwnd)
        mem = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        gdi32.SelectObject(mem, bmp)
        ok = user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]

        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth, bi.biHeight = w, -h
        bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, 0
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)

        from PIL import Image

        img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, "PNG", optimize=True)

        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)
        return bool(ok)

    def kill_tree(pid: int) -> None:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, check=False)


def _launch_via_powershell(ps1: Path) -> int:
    """Start the console window through PowerShell.

    A window started straight from this process never reaches the interactive
    desktop (it lands on a hidden window station and cannot be photographed).
    PowerShell's ``Start-Process`` does reach it, so that is the launcher.
    """
    launcher = (
        "$p = Start-Process powershell.exe -ArgumentList "
        "'-NoLogo','-NoExit','-ExecutionPolicy','Bypass','-File','" + str(ps1) + "' -PassThru; "
        "Write-Output $p.Id"
    )
    res = subprocess.run(["powershell.exe", "-NoProfile", "-Command", launcher],
                         capture_output=True, text=True, timeout=60)
    return int(res.stdout.strip().splitlines()[-1])


def capture_console(
    script_lines: list[str],
    out_path: Path,
    *,
    title: str,
    cols: int = 176,
    rows: int = 52,
    pages: int = 3,
    timeout: float = 300.0,
) -> list[Path]:
    """Run commands in a real PowerShell window and photograph that window.

    The run is usually taller than one screen, so the window is scrolled
    through its own buffer and each screen is captured. Every returned file is
    a real GDI grab of the real console — never a redrawing of its text.
    """
    if not IS_WIN:
        return []
    work = Path(tempfile.mkdtemp(prefix="tr-shot-"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    taken: list[Path] = []

    # The child scrolls to a page, raises a flag, and waits for us to say "go",
    # so the window is captured while it is genuinely showing that page.
    handshake = [
        # how many rows the run actually occupies (the buffer itself is huge)
        "$bottom = Get-OutputBottom",
        "try { [Console]::WindowTop = 0 } catch {}",
        "[Console]::SetCursorPosition(0, 0)",
        "Start-Sleep -Milliseconds 700",
        f"Set-Content -Path '{work / 'p1.ready'}' -Value $bottom",
        f"while (-not (Test-Path '{work / 'p1.go'}')) {{ Start-Sleep -Milliseconds 250 }}",
        # then the caller decides how many further pages are worth taking and
        # writes the row offsets; the console walks them one screen at a time
        "$offsets = @(Get-Content '%s')" % (work / "plan.txt"),
        "$i = 1",
        "foreach ($o in $offsets) {",
        "  if ($o -eq $null -or $o -eq '') { continue }",
        "  $i = $i + 1",
        "  try { [Console]::WindowTop = [int]$o } catch {}",
        "  try { [Console]::SetCursorPosition(0, [int]$o) } catch {}",
        "  Start-Sleep -Milliseconds 700",
        f"  Set-Content -Path (Join-Path '{work}' ('p' + $i + '.ready')) -Value $bottom",
        f"  while (-not (Test-Path (Join-Path '{work}' ('p' + $i + '.go')))) {{ Start-Sleep -Milliseconds 250 }}",
        "}",
        f"Set-Content -Path '{work / 'done'}' -Value 'done'",
        "Start-Sleep -Seconds 120",
    ]

    ps1 = work / "run.ps1"
    # PowerShell 5.1 intercepts a native command's stdout and re-emits it
    # through its own host, which turns the engine's ANSI colours into literal
    # "[0m" text. Running the command via Start-Process -NoNewWindow hands it
    # the console handles untouched, so VT sequences land in the real buffer.
    runs = "\n".join(
        f"Start-Process cmd.exe -ArgumentList '/c','{line}' -NoNewWindow -Wait\n"
        for line in script_lines
    )
    ps1.write_text(
        "$Host.UI.RawUI.WindowTitle = '" + title + "'\n"
        "Clear-Host\n"
        + "\n".join([VT_SETUP] + [runs] + handshake) + "\n",
        encoding="utf-8",
    )

    pid = _launch_via_powershell(ps1)
    try:
        hwnd = 0
        deadline = time.time() + 25
        while time.time() < deadline and not hwnd:
            hwnd = _enum_window_for_pid(pid)
            time.sleep(0.25)
        if not hwnd:
            print(f"    ! no window appeared for {title}", flush=True)
            return []

        # a judge-sized window, so a full screenful stays legible
        user32.MoveWindow(hwnd, 40, 40, 1860, 1180, True)
        user32.SetForegroundWindow(hwnd)

        # Page 1 is already positioned at the top; once we know how many rows
        # the run occupies, decide how many further screens are worth taking.
        ready = work / "p1.ready"
        deadline = time.time() + timeout
        while time.time() < deadline and not ready.exists():
            time.sleep(0.35)
        if not ready.exists():
            print(f"    ! timed out waiting for {title}", flush=True)
            return taken

        bottom = 0
        try:
            bottom = int(ready.read_text(encoding="utf-8", errors="replace").strip() or 0)
        except (OSError, ValueError):
            pass

        stem, suffix = out_path.stem, out_path.suffix

        def page_path(n: int) -> Path:
            return out_path.with_name(f"{stem}-{n}{suffix}")

        time.sleep(0.6)
        if capture_window(hwnd, page_path(1)):
            taken.append(page_path(1))

        # the plan must exist before the console is released, or it reads an
        # empty file and never pages again
        step = 22
        extra = [str(k * step) for k in range(1, max(pages, 1))
                 if k * step < bottom]
        (work / "plan.txt").write_text("\n".join(extra), encoding="utf-8")
        (work / "p1.go").write_text("go", encoding="utf-8")

        for index in range(len(extra)):
            ready_n = work / f"p{index + 2}.ready"
            deadline = time.time() + timeout
            while time.time() < deadline and not ready_n.exists():
                time.sleep(0.35)
            if not ready_n.exists():
                print(f"    ! timed out on page {index + 2} of {title}", flush=True)
                break
            time.sleep(0.6)
            if capture_window(hwnd, page_path(index + 2)):
                taken.append(page_path(index + 2))
            (work / f"p{index + 2}.go").write_text("go", encoding="utf-8")

        if taken and not out_path.exists():
            # keep the plain name too
            shutil.copyfile(taken[0], out_path)
        return taken
    finally:
        kill_tree(pid)
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------
# Real browser screenshots via Chromium
# --------------------------------------------------------------------------


def find_browser() -> str | None:
    for candidate in BROWSERS:
        if Path(candidate).exists():
            return candidate
    which = shutil.which("chrome") or shutil.which("msedge")
    return which


def capture_browser(url: str, out_path: Path, width: int = 1600, height: int = 1000,
                    full_page: bool = False) -> bool:
    browser = find_browser()
    if not browser:
        print("    ! no Chromium/Edge found for browser screenshots", flush=True)
        return False
    with tempfile.TemporaryDirectory(prefix="tr-chrome-") as profile:
        cmd = [
            browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={profile}",
            "--force-device-scale-factor=2",
            "--virtual-time-budget=6000",
            f"--window-size={width},{height}",
        ]
        if full_page:
            cmd.append("--screenshot-full-page")
        cmd += [f"--screenshot={out_path}", url]
        subprocess.run(cmd, capture_output=True, check=False, timeout=120)
    ok = out_path.exists() and out_path.stat().st_size > 2000
    return ok


def falkordb_alive(host: str = "localhost", port: int = 6379) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def sync_shot_lists() -> None:
    """Keep ``data/demo-data.js`` pointing at the screenshots that exist.

    The page must never reference an image that was never captured, and the
    capture order should not matter — so the screenshot run rewrites the
    per-scenario file lists it just produced.
    """
    path = DEMO / "data" / "demo-data.js"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    start, end = text.index("{"), text.rindex("}") + 1
    data = json.loads(text[start:end])
    changed = False
    for incident in data.get("incidents", []):
        folder = ASSETS / incident["key"]
        shots = sorted(f"assets/{incident['key']}/{p.name}" for p in folder.glob("*.png")) \
            if folder.exists() else []
        if shots != incident.get("shots"):
            incident["shots"] = shots
            changed = True
    if not changed:
        return
    path.write_text(
        text[:start] + json.dumps(data, ensure_ascii=True, indent=2) + text[end:],
        encoding="utf-8",
    )
    print("synced demo-data.js with the screenshots on disk")


def main() -> int:
    ap = argparse.ArgumentParser(description="capture real trace-repro screenshots")
    ap.add_argument("--scenarios", nargs="*", default=["checkout", "inventory", "payments"])
    ap.add_argument("--graph", default="memory://shot",
                    help="graph URL used in the console runs (falkordb://… with --falkordb)")
    ap.add_argument("--falkordb", action="store_true",
                    help="also screenshot FalkorDB's own Browser UI (needs a live server)")
    ap.add_argument("--list-manual", action="store_true",
                    help="print the screenshot slots you must capture yourself")
    ap.add_argument("--only", default=None, help="one scenario only")
    args = ap.parse_args()

    if args.list_manual:
        print("Drop your own PNGs into demo/assets/manual/ with these names:\n")
        for name, title, how in MANUAL_SLOTS:
            print(f"  {name+'.png':<22} {title}\n{'':<24}{how}")
        return 0

    scenarios = [args.only] if args.only else args.scenarios
    ASSETS.mkdir(parents=True, exist_ok=True)
    print(f"browser: {find_browser() or 'NOT FOUND'}")

    made = 0
    persistent = args.graph.startswith("falkordb")
    for sc in scenarios:
        print(f"\n[{sc}] real console screenshots", flush=True)
        out = ASSETS / sc

        ok = capture_console([
            f"python -m trace_repro.cli demo --repo demo/{sc} --graph {args.graph}",
        ], out / "console.png",
            title=f"trace-repro demo - demo/{sc}")
        made += len(ok)
        print(f"  console.png {'ok' if ok else 'FAILED'}"
              + (f" ({len(ok)} pages)" if len(ok) > 1 else ""), flush=True)

        # A separate `triage` process needs a graph that outlives the demo
        # process. `memory://` deliberately does not, so this shot only exists
        # against a real FalkorDB.
        if persistent:
            ok = capture_console([
                f"python -m trace_repro.cli triage --repo demo/{sc} --graph {args.graph}",
            ], out / "console-triage.png",
                title=f"trace-repro triage - demo/{sc}")
            made += len(ok)
            print(f"  console-triage.png {'ok' if ok else 'FAILED'}", flush=True)
        else:
            print("  console-triage.png skipped — needs --graph falkordb://… "
                  "(a graph that outlives the process)", flush=True)

    print("\n[product pages] real browser screenshots", flush=True)
    for name, _title, page in BROWSER_SLOTS:
        ok = capture_browser((REPO_ROOT / page).as_uri(),
                             ASSETS / f"{name}.png", width=1600, height=1000)
        made += ok
        print(f"  {name}.png {'ok' if ok else 'FAILED'}", flush=True)

    if args.falkordb:
        graph = args.graph.replace("falkordb://", "").replace("falkordbs://", "")
        if falkordb_alive():
            for name, _title, url in FALKORDB_SLOTS:
                target = url.format(graph=graph)
                ok = capture_browser(target, ASSETS / f"{name}.png", 1600, 1000)
                made += ok
                print(f"  {name}.png {'ok' if ok else 'FAILED'}", flush=True)

            MANUAL.mkdir(parents=True, exist_ok=True)
            cli_out = MANUAL / "falkordb-cli.png"
            ok = capture_console([
                f'docker exec falkordb redis-cli GRAPH.QUERY {graph} "MATCH (n) RETURN labels(n), count(*)"',
                f'docker exec falkordb redis-cli GRAPH.QUERY {graph} "MATCH (s:Span)-[r]->(e:StateEntity) RETURN s.name, type(r), e.key LIMIT 8"',
            ], cli_out, title=f"FalkorDB CLI - {graph}")
            made += len(ok)
            print(f"  manual/falkordb-cli.png {'ok' if ok else 'FAILED'}", flush=True)
        else:
            print("  ! no FalkorDB on localhost:6379 — "
                  "start it, then re-run with --falkordb", flush=True)

    print(f"\n{made} real screenshot(s) written to demo/assets/")
    sync_shot_lists()
    missing = [n for n, _t, _h in MANUAL_SLOTS if not (MANUAL / f"{n}.png").exists()]
    if missing:
        print(f"{len(missing)} slot(s) need a real screenshot from your accounts: "
              + ", ".join(missing))
        print("python demo/screenshot.py --list-manual   # what to capture and where")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
