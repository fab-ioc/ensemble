"""A stand-in for ``codex app-server`` (tests/test_codex_pools.py).

    fake_codex_app_server.py <mode> <pid file> [--shim]

``mode``: ``ok`` answers the rate-limit read from the fixture beside this file,
``old`` refuses it as an unknown method, ``hang`` never answers anything.
``--shim`` runs the server as a child of this process, as the npm shim does on
Windows, so a test can see that the whole tree is killed. The serving process
writes its pid to ``<pid file>``.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

mode, pid_file = sys.argv[1], sys.argv[2]
if "--shim" in sys.argv:
    sys.exit(subprocess.call([sys.executable, __file__, mode, pid_file]))

Path(pid_file).write_text(str(os.getpid()), encoding="utf-8")
if mode == "hang":
    time.sleep(120)
    sys.exit(0)

answer = json.loads((Path(__file__).parent / "codex_app_server_rate_limits.json")
                    .read_text(encoding="utf-8"))
for line in sys.stdin:
    message = json.loads(line)
    if message.get("method") == "initialize":
        print(json.dumps({"id": message["id"], "result": {"userAgent": "fake"}}), flush=True)
        print(json.dumps({"method": "remoteControl/status/changed", "params": {}}), flush=True)
    elif message.get("method") == "account/rateLimits/read":
        if mode == "old":
            print(json.dumps({"id": message["id"], "error": {
                "code": -32600, "message": "Invalid request: unknown variant"}}), flush=True)
        else:
            print(json.dumps({"id": message["id"], "result": answer}), flush=True)
time.sleep(120)      # like the real one, it does not leave by itself
