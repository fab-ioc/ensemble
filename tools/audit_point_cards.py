"""Point mentions in every chat, and where their chips land (#206).
Read-only: reads the hub's state (~/.ensemble) and transcripts, writes nothing.

    py tools/audit_point_cards.py [--examples N]

Every balloon of every room (its messages, and a solo chat's transcript turns
of all its sessions) is read by static/pointrefs.js's refsIn, as the pages read
it; each mention is resolved with dashboard.point_ref. A mention "lands on the
person's message" before #206 always; after, unless its point has an open
question card or the text says "the Pn card" and the point has a card. Open
cards are split by whether asks.open_in still lists them (Needs you) or the
person has written in the chat since. Takes ~10 minutes on the live state.
"""
import collections
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import asks          # noqa: E402
import chatroom      # noqa: E402
import dashboard     # noqa: E402
import points        # noqa: E402

points._save = lambda *a, **k: None      # noqa: E731 — never write a ledger

REFS_JS = r"""
const fs = require('fs'), path = require('path'), os = require('os');
const [repo, inFile, outFile] = process.argv.slice(1);
globalThis.TaskCard = require(path.join(repo, 'static', 'taskcard.js'));
const P = require(path.join(repo, 'static', 'pointrefs.js'));
const projects = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.ensemble', 'projects.json'), 'utf8'));
const ctxFor = own => ({ projects: projects.map(p => ({ id: p.id, key: p.key, name: p.name,
  aliases: [p.name, p.key, String(p.name || '').split(/\s+/)[0]].filter(Boolean) })), own, nouns: [] });
const out = [];
for (const t of JSON.parse(fs.readFileSync(inFile, 'utf8')))
  for (const r of P.refsIn(t.text, ctxFor(t.project)))
    out.push({ room: t.room, id: r.id, project: r.project || t.project, card: !!r.card,
      at: t.text.slice(Math.max(0, r.start - 40), r.end + 40).replace(/\s+/g, ' ') });
fs.writeFileSync(outFile, JSON.stringify(out));
"""


def balloons() -> list[dict]:
    out = []
    for summary in chatroom.list_rooms():
        rid = summary["id"]
        room = chatroom.get_room(rid)
        if not room:
            continue
        pid = dashboard.room_project(rid) or ""
        texts = [m.get("text") or "" for m in room.get("messages") or []]
        agents = chatroom.agent_participants(room)
        if room.get("mode") == "solo" or len(agents) == 1:
            sids = set()
            for p in agents:
                sids.add(p.get("sessionId") or "")
                for r in p.get("rotations") or []:
                    sids.update((r.get("fromSessionId") or "", r.get("toSessionId") or ""))
            for sid in sids - {""}:
                try:
                    texts += [t.get("text") or "" for t in (dashboard.solo_turns(room, sid) or {}).values()]
                except Exception as e:      # noqa: BLE001 — a transcript that cannot be read is skipped
                    print(f"skip {rid} {sid}: {e!r}", file=sys.stderr)
        out += [{"room": rid, "project": pid, "text": t} for t in texts if "P" in t]
    return out


def mentions(texts: list[dict]) -> list[dict]:
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "in.json"), os.path.join(d, "out.json")
        Path(src).write_text(json.dumps(texts), encoding="utf-8")
        subprocess.run(["node", "-e", REFS_JS, str(REPO), src, dst], check=True)
        return json.loads(Path(dst).read_text(encoding="utf-8"))


def main() -> None:
    examples = int(sys.argv[sys.argv.index("--examples") + 1]) if "--examples" in sys.argv else 5
    found = mentions(balloons())
    refs, c, ex = {}, collections.Counter(), collections.defaultdict(list)
    needs_you: dict = {}
    for m in found:
        k = (m["project"], m["id"])
        if k not in refs:
            refs[k] = dashboard.point_ref(m["id"], m["project"])
        p = refs[k]
        c["mentions"] += 1
        if not p:
            c["mentions of no single point"] += 1
            continue
        card = p.get("card")
        lands_card = bool(card and (card["open"] or m["card"]))
        if card and card["open"]:
            if p["roomId"] not in needs_you:
                s = next((s for s in chatroom.list_rooms() if s["id"] == p["roomId"]), None)
                needs_you[p["roomId"]] = {a["mid"] for a in asks.open_in(s)} if s else set()
            listed = card["mid"] in needs_you[p["roomId"]]
            c["open card: before on the person's message"] += 1
            c["open card: after on the person's message"] += 0 if lands_card else 1
            c["open card, still in Needs you" if listed else "open card, person wrote since"] += 1
            if len(ex["open"]) < examples:
                ex["open"].append(m["id"] + ": " + m["at"])
        elif card:
            c["answered card"] += 1
        if m["card"]:
            c["'Pn card' phrase"] += 1
            c["'Pn card' phrase: after on a card"] += 1 if lands_card else 0
    pts = [p for p in refs.values() if p]
    c["points"] = len(pts)
    c["points with a card"] = sum(1 for p in pts if p.get("card"))
    c["points with an open card"] = sum(1 for p in pts if p.get("card") and p["card"]["open"])
    print(json.dumps(dict(c), indent=1))
    for k, v in ex.items():
        print(f"== {k}")
        for x in v:
            print("  ", x)


if __name__ == "__main__":
    main()
