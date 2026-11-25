"""Rebuild all documentation images: python3 tools/doc_shots/run.py.

Needs Python, Node 20+ and Chrome/Chromium. Every process and file used by the
demo lives under a fresh /tmp/ens-docs-* folder; the real hub is untouched.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

from PIL import Image

from demo_hub import Hub, ROOT
from seed import seed, seed_usage


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--chrome-port", type=int, default=9341)
    mac_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    parser.add_argument("--chrome", default=(shutil.which("google-chrome") or shutil.which("chromium")
                                              or shutil.which("chromium-browser")
                                              or (str(mac_chrome) if mac_chrome.exists() else None)))
    args = parser.parse_args()
    if not args.chrome:
        parser.error("Chrome or Chromium is required (--chrome PATH)")
    if not shutil.which("node"):
        parser.error("Node is required")
    base = Path(tempfile.mkdtemp(prefix="ens-docs-"))
    # Its path must have no home folder name or personal data: pages expose
    # demo file paths. tempfile.mkdtemp's default /tmp meets that condition.
    hub = Hub(base, args.port)
    out = ROOT / "docs" / "images"
    out.parent.mkdir(parents=True, exist_ok=True)
    shot_dir = Path(tempfile.mkdtemp(prefix=".images-", dir=out.parent))
    try:
        hub.prepare({"theme": "paper", "operatorNickname": "Alex"})
        seed_usage(hub)
        hub.start()
        state = seed(hub)
        for route in ("/", f"/session?id={state['active']}",
                      f"/fileview?path={quote(state['file'], safe='')}"):
            status, _ = hub.req(route, nav=True)
            if status != 200:
                raise RuntimeError(f"{route}: expected HTTP 200, got {status}")
        state_file = base / "shot-state.json"
        state_file.write_text(json.dumps(state), encoding="utf-8")
        subprocess.run(["node", str(Path(__file__).with_name("shots.mjs")), hub.url,
                        str(state_file), str(shot_dir), args.chrome, str(args.chrome_port)], check=True)
        manifest = json.loads((shot_dir / "manifest.json").read_text(encoding="utf-8"))
        pngs = list(shot_dir.glob("*.png"))
        if len(manifest) != 15 or {x["name"] + ".png" for x in manifest} != {x.name for x in pngs}:
            raise RuntimeError("the screenshot set is incomplete")
        for file in pngs:
            im = Image.open(file)
            # Paper has few colours; a palette PNG keeps screenshots sharp.
            im.quantize(colors=128, method=Image.Quantize.FASTOCTREE).save(file, optimize=True)
        backup = out.parent / ".images-previous"
        if backup.exists():
            shutil.rmtree(backup)
        if out.exists():
            os.replace(out, backup)
        try:
            os.replace(shot_dir, out)
        except OSError:
            if backup.exists():
                os.replace(backup, out)
            raise
        shutil.rmtree(backup, ignore_errors=True)
        print(f"Rebuilt {len(pngs)} Paper screenshots in {out}")
    finally:
        hub.stop()
        shutil.rmtree(base, ignore_errors=True)
        shutil.rmtree(shot_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
