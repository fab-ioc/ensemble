"""A stand-in for `claude` and `codex` in the documentation's demo hub.

It never talks to a model. As an agent it draws a quiet terminal screen and
waits for typed lines; as `codex app-server` it answers the plan-usage read
with made-up numbers, so the usage chip has something to show.

    fake_agent.py claude|codex [the agent's own arguments]
"""
import json
import os
import re
import sys
import time
from pathlib import Path

kind = sys.argv[1]
args = sys.argv[2:]
if "--version" in args:
    print("2.1.0 (Claude Code)" if kind == "claude" else "codex-cli 0.80.0")
    sys.exit(0)

if kind == "claude" and args[:2] == ["auth", "status"]:
    print('{"loggedIn": true}')
    sys.exit(0)
if kind == "codex" and args[:2] == ["login", "status"]:
    print("Logged in with a demo account")
    sys.exit(0)

if kind == "codex" and args[-3:] == ["mcp", "list", "--json"]:
    print("[]")  # The disposable agent has no configured external servers.
    sys.exit(0)

if kind == "codex" and args[:1] == ["app-server"]:
    week = int(time.time()) + 4 * 86400
    pool = {"limitId": "codex", "limitName": None, "normalModelSlug": None,
            "primary": {"usedPercent": 26, "windowDurationMins": 10080, "resetsAt": week},
            "secondary": None, "credits": None, "individualLimit": None,
            "spendControlReached": False, "planType": "pro", "rateLimitReachedType": None}
    answer = {"ordinaryUsageAllowed": True, "rateLimits": pool,
              "rateLimitsByLimitId": {"codex": pool}, "rateLimitResetCredits": None,
              "accountId": "demo", "rateLimitUpsell": None}
    for line in sys.stdin:
        msg = json.loads(line)
        if msg.get("method") == "initialize":
            print(json.dumps({"id": msg["id"], "result": {"userAgent": "demo"}}), flush=True)
        elif msg.get("method") == "account/rateLimits/read":
            print(json.dumps({"id": msg["id"], "result": answer}), flush=True)
    time.sleep(120)
    sys.exit(0)

# What the seeder needs to act as this agent: its token and its session.
_log = os.environ.get("DOC_AGENTS_LOG")
if _log:
    token = os.environ.get("CHAT_TOKEN", "")
    if "--mcp-config" in args:
        try:
            cfg = json.load(open(args[args.index("--mcp-config") + 1], encoding="utf-8"))
            token = cfg["mcpServers"]["ensemble"]["headers"]["Authorization"].split()[-1]
        except (OSError, ValueError, KeyError, IndexError):
            pass
    sid = args[args.index("--session-id") + 1] if "--session-id" in args else ""
    if "--resume" in args:
        sid = args[args.index("--resume") + 1]
    with open(_log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": kind, "pid": os.getpid(), "cwd": os.getcwd(), "sid": sid,
                            "token": token, "identity": os.environ.get("ENSEMBLE_IDENTITY", ""),
                            "room": os.environ.get("ENSEMBLE_ROOM", ""), "at": time.time()}) + "\n")

DIM, BOLD, ACC, OFF = "\x1b[2m", "\x1b[1m", "\x1b[38;5;173m", "\x1b[0m"


def transcript_line(role: str, text: str) -> None:
    """A turn in this session's transcript, where Claude Code keeps it
    (~/.claude/projects/<folder>/<session>.jsonl): the hub reads a PO's chat
    and every point from there."""
    if kind != "claude" or not sid:
        return
    folder = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000Z"
    msg = ({"role": "user", "content": text} if role == "user" else
           {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": text}]})
    rec = {"type": role, "timestamp": stamp, "sessionId": sid, "cwd": os.getcwd(), "uuid": os.urandom(8).hex(),
           "message": msg}
    with open(folder / f"{sid}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


name = "Claude Code" if kind == "claude" else "Codex"
out = sys.stdout
out.write(f"{ACC}{BOLD}* {name}{OFF} {DIM}(demo stand-in){OFF}\r\n")
out.write(f"{DIM}  cwd: {os.path.basename(os.getcwd())}{OFF}\r\n\r\n")
for line in ("> Read the spec and the board", f"  {DIM}Read SPEC.md (42 lines){OFF}",
             f"  {DIM}Read src/search.py (118 lines){OFF}", "> Write the ingredient search",
             f"  {DIM}Edit src/search.py (+31 -4){OFF}",
             f"  {DIM}Run python -m unittest tests.test_search   OK (12 tests){OFF}",
             "> Commit and ask for a review", ""):
    out.write(line + "\r\n")
out.write(f"{ACC}* Waiting for the reviewer...{OFF}\r\n\r\n> ")
out.flush()

# Raw input: a bracketed paste may hold several lines; Enter outside one sends.
try:
    import tty
    tty.setraw(sys.stdin.fileno())
except Exception:          # noqa: BLE001 — not a terminal: read it as it comes
    pass
fd = sys.stdin.fileno()
buf, paste, parts = b"", False, []
while True:
    try:
        chunk = os.read(fd, 4096)
    except OSError:
        chunk = b""
    if not chunk:
        time.sleep(1)
        continue
    buf += chunk
    while buf:
        if paste:
            end = buf.find(b"\x1b[201~")
            if end < 0:
                break
            parts.append(buf[:end])
            buf, paste = buf[end + 6:], False
            continue
        start, enter = buf.find(b"\x1b[200~"), buf.find(b"\r")
        if start >= 0 and (enter < 0 or start < enter):
            parts.append(buf[:start])
            buf, paste = buf[start + 6:], True
            continue
        if enter < 0:
            break
        parts.append(buf[:enter])
        buf = buf[enter + 1:]
        text = b"".join(parts).decode("utf-8", "replace").replace("\r\n", "\n").strip()
        parts = []
        if text:
            transcript_line("user", text)
            out.write(f"\r\n{DIM}  (received){OFF} {text.splitlines()[0][:100]}\r\n> ")
            out.flush()
