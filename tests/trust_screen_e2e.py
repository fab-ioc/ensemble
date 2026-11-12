"""End-to-end check of ED-340 on a throwaway hub with real Codex agents.

Not part of the suite: it starts two Codex seats (an engineer and a reviewer
that runs on mention), which costs a little plan allowance and needs Codex
logged in. Run:

    python3 tests/trust_screen_e2e.py [--port 8812] [--no-prevent]

The hub runs on --port with its own state: HOME, USERPROFILE and CODEX_HOME
point at a temporary folder; a copy of the Codex login goes there and is
deleted afterwards. The real ``~/.codex/config.toml`` is never written (the
script checks it is byte-identical afterwards). It makes a git project, a
worktree task with both seats, mentions the reviewer, and passes when the
reviewer's verdict reaches the chat with no key pressed and no seat stopped on
a trust screen. ``--no-prevent`` switches off the hub's pre-trust in that hub
alone, so the screen shows and ``first_run.watch`` must answer it.
"""
import argparse, base64, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import first_run  # noqa: E402
ap = argparse.ArgumentParser()
ap.add_argument("--port", default="8812")
ap.add_argument("--no-prevent", action="store_true")
A = ap.parse_args()
REPO, PORT, NOPREVENT = str(ROOT), A.port, A.no_prevent
S = tempfile.mkdtemp(prefix="e340-")
H = os.path.join(S, "home"); BASE = f"http://127.0.0.1:{PORT}"
REAL_CFG = Path.home() / ".codex" / "config.toml"
REAL_BEFORE = REAL_CFG.read_bytes() if REAL_CFG.exists() else None
os.makedirs(os.path.join(H, ".codex"))
shutil.copy2(Path.home() / ".codex" / "auth.json", os.path.join(H, ".codex", "auth.json"))
LAUNCH = f"""
import sys
sys.path.insert(0, {REPO!r})
sys.argv = ['dashboard.py', '--port', {PORT!r}, '--log', {os.path.join(S, 'hub.log')!r}]
import dashboard
dashboard.peer_process.from_agent = lambda *a, **k: ''
{'import agents.codex; agents.codex.CodexAgent.ensure_trusted = lambda self, cwd: True' if NOPREVENT else ''}
dashboard.main()
"""
def req(path, body=None, timeout=30):
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return json.loads(f.read())
def screen(pty):
    try:
        with urllib.request.urlopen(BASE + "/api/pty/stream?id=" + pty, timeout=5) as st:
            for line in st:
                if line.startswith(b"data: "):
                    raw = base64.b64decode(line[6:].strip()).decode("utf-8", "replace")
                    return re.sub(r"\x1b\[[0-9;?<>=]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b.", " ", raw)
    except Exception as e:
        return f"<no screen: {e}>"
    return ""
env = dict(os.environ, HOME=H, USERPROFILE=H, CODEX_HOME=os.path.join(H, ".codex"), ENSEMBLE_BIND="127.0.0.1")
hub = subprocess.Popen([sys.executable, "-c", LAUNCH], env=env, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=S)
out = {}
try:
    for _ in range(90):
        try:
            cfg = req("/api/config", timeout=3); break
        except Exception:
            time.sleep(1)
    out["stateDir"] = cfg.get("stateDir")
    assert cfg["stateDir"].startswith(H), cfg["stateDir"]
    code = os.path.join(H, "code", "proj"); os.makedirs(code, exist_ok=True)
    subprocess.run(["git", "init", "-q", code], check=True)
    open(os.path.join(code, "README.md"), "w").write("e2e\n")
    subprocess.run(["git", "-C", code, "add", "."], check=True)
    subprocess.run(["git", "-C", code, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], check=True)
    proj = req("/api/projects/new", {"path": code, "name": "E2E Proj", "kind": "code"})["project"]
    made = req("/api/room/new", {"title": "Trust e2e", "projectId": proj["id"], "workspace": "worktree",
                                 "task": "Test task for an automated check. Do not change any file. "
                                         "Send one short chat message saying ok, then stop.",
                                 "agents": [{"agent": "codex", "role": "engineer"},
                                            {"agent": "codex", "role": "reviewer"}]}, timeout=120)
    rid = made["room"]["id"]
    parts = [p for p in made["room"]["participants"] if p.get("kind") == "agent"]
    out["seats"] = [(p["identity"], p.get("role")) for p in parts]
    owner = next(p for p in parts if not p.get("role", "").startswith("reviewer"))
    rev = next(p for p in parts if p.get("role", "").startswith("reviewer"))
    time.sleep(20)
    room = req(f"/api/room?id={rid}")
    o = next(p for p in room["participants"] if p["identity"] == owner["identity"])
    scr = screen(o.get("ptyId", ""))
    out["ownerCwd"] = o.get("cwd"); out["ownerTrustScreen"] = bool(first_run.find(scr))
    out["ownerScreenTail"] = first_run.squash(scr)[-300:]
    req("/api/room/say", {"roomId": rid, "to": rev["identity"],
                          "text": f"@{rev['identity']} please review: nothing changed yet, reply with a one-line verdict."})
    seen, said, rscr = False, None, ""
    for i in range(150):
        time.sleep(2)
        room = req(f"/api/room?id={rid}")
        r = next(p for p in room["participants"] if p["identity"] == rev["identity"])
        if r.get("ptyId"):
            rscr = screen(r["ptyId"])
            seen = seen or bool(first_run.find(rscr))
        said = next((m for m in room.get("messages", []) if m.get("from") == rev["identity"]), None)
        if said:
            break
    out["reviewerTrustScreenSeen"] = seen
    out["reviewerReplied"] = bool(said); out["reviewerText"] = (said or {}).get("text", "")[:200]
    out["reviewerSecs"] = 2 * (i + 1)
    if not said:
        out["reviewerScreenTail"] = first_run.squash(rscr)[-400:]
    att = req("/api/attention")
    out["attentionFirstRun"] = [a for a in json.dumps(att).split('"cause"') if "first_run" in a[:20]]
    cfgt = open(os.path.join(H, ".codex", "config.toml")).read()
    out["firstRunLog"] = [l.strip() for l in open(os.path.join(S, "hub.log")) if "[first-run]" in l]
    out["trustEntries"] = re.findall(r'\[projects\."([^"]+)"\]', cfgt)
finally:
    try:
        for s in req("/api/ptys"):
            sid = s.get("id") if isinstance(s, dict) else s
            req("/api/pty/kill", {"id": sid})
    except Exception as e:
        out["killErr"] = str(e)
    hub.terminate()
    try: hub.wait(15)
    except Exception: hub.kill()
    with __import__("contextlib").suppress(OSError):
        os.remove(os.path.join(H, ".codex", "auth.json"))
    out["realConfigUntouched"] = (REAL_CFG.read_bytes() if REAL_CFG.exists() else None) == REAL_BEFORE
    print(json.dumps(out, indent=1))
    ok = (out.get("reviewerReplied") and not out.get("ownerTrustScreen")
          and not out.get("reviewerTrustScreenSeen") and out.get("realConfigUntouched")
          and (out.get("firstRunLog") if NOPREVENT else True))
    shutil.rmtree(S, ignore_errors=True)
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
