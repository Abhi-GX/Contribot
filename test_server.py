"""
test_server.py — Local integration test for app.py.

Starts the server automatically, runs every endpoint, then stops it.
No Azure/Teams needed. Optionally runs the AI agent if GOOGLE_API_KEY is set.

Usage:
    python test_server.py                # auto-start server, skip agent turn
    python test_server.py --agent        # also runs 3 live agent turns (uses API quota)
    python test_server.py --port 8080    # custom port
    python test_server.py --no-start     # server already running, don't start it
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).parent

# ---------------------------------------------------------------------------
# Load .env
# ---------------------------------------------------------------------------
env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()   # always override — .env is the source of truth

sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# CLI args
# ---------------------------------------------------------------------------
PORT       = 8000
RUN_AGENT  = False
AUTO_START = True

for arg in sys.argv[1:]:
    if arg == "--agent":
        RUN_AGENT = True
    elif arg == "--no-start":
        AUTO_START = False
    elif arg.startswith("--port="):
        PORT = int(arg.split("=")[1])
    elif arg == "--port" and sys.argv.index(arg) + 1 < len(sys.argv):
        PORT = int(sys.argv[sys.argv.index(arg) + 1])

BASE = f"http://localhost:{PORT}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
PASS = "PASS"
FAIL = "FAIL"

def get(path: str, timeout: int = 15) -> tuple[int, dict | str]:
    url = BASE + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:
        return 0, str(e)


def post_json(path: str, data: dict, timeout: int = 15) -> tuple[int, dict | str]:
    url = BASE + path
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, str(e)


def post_multipart(path: str, filename: str, file_bytes: bytes, timeout: int = 20) -> tuple[int, dict | str]:
    """POST a multipart/form-data file upload."""
    boundary = "----TestBoundary7391"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n"
        f"\r\n"
    ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()

    url = BASE + path
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:
        return 0, str(e)


def make_test_xlsx() -> bytes:
    """Build a minimal valid Learn-export .xlsx in memory."""
    try:
        import openpyxl
    except ImportError:
        return b""

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Export"
    # Headers matching what generate_csv.py expects
    ws.append([
        "Name", "Email", "Format", "Contribution Status", "Points", "Verified Points"
    ])
    # One eligible row
    ws.append([
        "Test Mentor", "test_mentor@epam.com",
        "Group Meeting with contributor", "approved", 3.0, 3.0
    ])
    # One ineligible row (wrong format)
    ws.append([
        "Test User 2", "test_user2@epam.com",
        "Individual", "approved", 2.0, 2.0
    ])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Wait for server to be ready
# ---------------------------------------------------------------------------
def wait_for_server(timeout: int = 20) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{BASE}/api/health", timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# Start / stop server
# ---------------------------------------------------------------------------
_server_proc: subprocess.Popen | None = None


def start_server() -> bool:
    global _server_proc
    # Check if already running
    try:
        urllib.request.urlopen(f"{BASE}/api/health", timeout=2)
        print(f"  Server already running on :{PORT}")
        return True
    except Exception:
        pass

    print(f"  Starting app.py on port {PORT}...")
    env = os.environ.copy()
    env["PORT"] = str(PORT)
    _server_proc = subprocess.Popen(
        [sys.executable, "app.py"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    ready = wait_for_server(timeout=20)
    if not ready:
        output = _server_proc.stdout.read().decode(errors="replace")
        print(f"  Server failed to start. Output:\n{output}")
        _server_proc.terminate()
        return False
    print(f"  Server ready at {BASE}")
    return True


def stop_server():
    if _server_proc:
        _server_proc.terminate()
        try:
            _server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _server_proc.kill()
        print("\n  Server stopped.")


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------
results: list[tuple[str, str, str]] = []   # (test_name, status, detail)


def check(name: str, ok: bool, detail: str = ""):
    status = PASS if ok else FAIL
    results.append((name, status, detail))
    mark = "  PASS" if ok else "  FAIL"
    line = f"{mark}  {name}"
    if detail:
        line += f"  [{detail}]"
    print(line)


def run_tests():
    print("\n" + "=" * 60)
    print(f"SERVER TESTS  ({BASE})")
    print("=" * 60)

    # 1. Health check
    print("\n-- Endpoints --")
    code, data = get("/api/health")
    check(
        "GET /api/health",
        code == 200 and isinstance(data, dict) and data.get("status") == "ok",
        f"HTTP {code}, status={data.get('status') if isinstance(data, dict) else '?'}",
    )

    # 2. Stats
    code, data = get("/api/stats")
    check(
        "GET /api/stats",
        code == 200 and isinstance(data, dict) and "people_count" in data,
        f"HTTP {code}, people_count={data.get('people_count') if isinstance(data, dict) else '?'}",
    )
    if isinstance(data, dict):
        print(f"        people_count={data.get('people_count')}  "
              f"total_bonus=${data.get('total_bonus_usd')}")

    # 3. Key pool status
    code, data = get("/api/keys/status")
    check(
        "GET /api/keys/status",
        code == 200 and isinstance(data, dict) and "keys" in data,
        f"HTTP {code}, keys={len(data.get('keys', [])) if isinstance(data, dict) else '?'}",
    )
    if isinstance(data, dict) and data.get("keys"):
        for k in data["keys"]:
            print(f"        Key {k['key_index']}: {k['state']}  "
                  f"(failures={k['fail_count']})")

    # 4. Admin UI
    code, body = get("/")
    check(
        "GET / (admin UI)",
        code == 200 and isinstance(body, str) and "Contribution Bot" in body,
        f"HTTP {code}, length={len(body) if isinstance(body, str) else 0}",
    )

    # 5. Bot endpoint — any HTTP response proves the route exists and is registered.
    #    Without real Teams auth the adapter returns 401/403/500 depending on SDK version.
    code, _ = post_json("/api/messages", {})
    check(
        "POST /api/messages (unauthenticated, route reachable)",
        code in (200, 401, 403, 415, 500),
        f"HTTP {code} (any response = route is registered)",
    )

    # 6. Upload test
    print("\n-- Upload --")
    xlsx_bytes = make_test_xlsx()
    if xlsx_bytes:
        code, data = post_multipart("/upload", "test_export.xlsx", xlsx_bytes)
        ok = (
            code == 200
            and isinstance(data, dict)
            and data.get("success") is True
            and data.get("people_count", 0) >= 1
        )
        detail = (
            f"HTTP {code}, "
            f"success={data.get('success') if isinstance(data, dict) else '?'}, "
            f"people_count={data.get('people_count') if isinstance(data, dict) else '?'}"
        )
        check("POST /upload (test .xlsx)", ok, detail)
        if isinstance(data, dict) and data.get("people"):
            for p in data["people"][:3]:
                print(f"        {p['name']} — {p['eligible_points']} pts  ${p['bonus_usd']:.2f}")
    else:
        check("POST /upload (test .xlsx)", False, "openpyxl not available — skipped")

    # 7. Stats after upload (people_count should be >= 1 from test file)
    code, data = get("/api/stats")
    check(
        "GET /api/stats after upload",
        code == 200 and isinstance(data, dict) and data.get("people_count", 0) >= 1,
        f"people_count={data.get('people_count') if isinstance(data, dict) else '?'}",
    )


def run_agent_tests():
    print("\n" + "=" * 60)
    print("AGENT TESTS  (direct, uses GOOGLE_API_KEY)")
    print("=" * 60)

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GOOGLE_API_KEYS", "").split(",")[0].strip()
    if not api_key:
        print("\n  Skipped — no GOOGLE_API_KEY / GOOGLE_API_KEYS set.")
        print("  Set it in .env and re-run with --agent to test AI responses.")
        return

    from agent import run_agent
    from agent.tools import get_contribution

    email = "saikrishna_tammi@epam.com"
    prefetched = get_contribution(email)

    print(f"\n  Email: {email}")
    print(f"  Pre-fetched data found: {prefetched.get('found', False)}")

    history = []
    turns = [
        ("Personal bonus query", "what's my bonus?"),
        ("Follow-up session count", "how many sessions is that?"),
        ("Program rules", "how does the program work?"),
    ]

    for label, msg in turns:
        print(f"\n  [{label}]")
        print(f"  USER: {msg}")
        try:
            reply, history = run_agent(msg, email, history, prefetched)
            print(f"  BOT:  {reply[:200]}{'...' if len(reply) > 200 else ''}")
            check(f"Agent turn: {label}", bool(reply and len(reply) > 5), "")
        except Exception as e:
            check(f"Agent turn: {label}", False, str(e)[:80])


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def print_summary():
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    passed = sum(1 for _, s, _ in results if s == PASS)
    failed = sum(1 for _, s, _ in results if s == FAIL)
    for name, status, detail in results:
        mark = "  PASS" if status == PASS else "  FAIL"
        print(f"{mark}  {name}" + (f"  [{detail}]" if detail else ""))
    print()
    print(f"  {passed} passed / {failed} failed out of {len(results)} tests")
    if failed:
        print("\n  Some tests FAILED. Check the output above.")
    else:
        print("\n  All tests passed. Safe to deploy.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    server_started = False

    try:
        if AUTO_START:
            print("\nStarting server...")
            if not start_server():
                print("\nERROR: Could not start server. Aborting tests.")
                sys.exit(1)
            server_started = True
        else:
            print(f"\nUsing existing server at {BASE}")
            if not wait_for_server(timeout=5):
                print(f"ERROR: No server found at {BASE}. "
                      "Start it with  python app.py  or remove --no-start.")
                sys.exit(1)

        run_tests()

        if RUN_AGENT:
            run_agent_tests()

        print_summary()

    finally:
        if server_started and _server_proc:
            stop_server()

    failed_count = sum(1 for _, s, _ in results if s == FAIL)
    sys.exit(1 if failed_count else 0)
