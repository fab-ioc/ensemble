"""Headless Chrome driven from Python alone, for page tests on a machine
without Node (the other page tests start their CDP script with Node and skip
without it).

Chrome is started with ``--remote-debugging-port=0`` and a seeded profile
(tests/chrome_profile.py); it writes the port it chose to ``DevToolsActivePort``
in that profile. ``Browser.page(w, h, mobile)`` opens a tab of that size and
returns a ``Page`` whose ``eval`` runs an expression in it (awaiting a promise)
and returns the value. The WebSocket client is the few lines CDP needs: text
frames only, masked as a client must, a long message in several frames.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from tests import chrome_profile


class _Ws:
    def __init__(self, url: str):
        assert url.startswith("ws://"), url
        hostport, _, path = url[5:].partition("/")
        host, _, port = hostport.partition(":")
        self.s = socket.create_connection((host, int(port)), timeout=60)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
                        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                        "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.s.recv(1)
            if not chunk:
                raise ConnectionError("no WebSocket handshake answer")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.decode(errors="replace"))

    def _read(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            chunk = self.s.recv(n - len(out))
            if not chunk:
                raise ConnectionError("the WebSocket closed")
            out += chunk
        return out

    def send(self, text: str) -> None:
        data = text.encode()
        head = bytearray([0x81])
        n = len(data)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head += bytes([0x80 | 126]) + n.to_bytes(2, "big")
        else:
            head += bytes([0x80 | 127]) + n.to_bytes(8, "big")
        mask = os.urandom(4)
        self.s.sendall(bytes(head) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> str:
        parts = []
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = int.from_bytes(self._read(2), "big")
            elif n == 127:
                n = int.from_bytes(self._read(8), "big")
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            op = b0 & 0x0F
            if op == 0x8:
                raise ConnectionError("the WebSocket closed")
            if op in (0x9, 0xA):        # ping, pong: nothing to read
                continue
            parts.append(data)
            if b0 & 0x80:
                return b"".join(parts).decode()

    def close(self) -> None:
        try:
            self.s.close()
        except OSError:
            pass


class Page:
    def __init__(self, ws_url: str):
        self.ws = _Ws(ws_url)
        self.n = 0

    def send(self, method: str, params: dict | None = None) -> dict:
        self.n += 1
        my = self.n
        self.ws.send(json.dumps({"id": my, "method": method, "params": params or {}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == my:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result") or {}

    def eval(self, expr: str):
        r = self.send("Runtime.evaluate", {"expression": expr, "awaitPromise": True,
                                           "returnByValue": True})
        if r.get("exceptionDetails"):
            raise RuntimeError(expr[:160] + " :: " + json.dumps(r["exceptionDetails"])[:800])
        return (r.get("result") or {}).get("value")

    def until(self, expr: str, timeout: float = 20.0) -> bool:
        """Waits until ``expr`` is true in the page (an element counts as true)."""
        end = time.time() + timeout
        last = None
        while time.time() < end:
            try:
                v = self.eval(f"!!({expr})")
            except RuntimeError as e:
                v, last = None, e
            if v:
                return v
            time.sleep(0.1)
        raise AssertionError(f"timeout: {expr[:200]} ({last})")

    def goto(self, url: str) -> None:
        self.send("Page.navigate", {"url": url})

    def shot(self, path: str | os.PathLike) -> None:
        data = self.send("Page.captureScreenshot", {"format": "png"})["data"]
        Path(path).write_bytes(base64.b64decode(data))

    def close(self) -> None:
        self.ws.close()


class Browser:
    def __init__(self, parent: str | os.PathLike | None = None):
        self.tmp = tempfile.mkdtemp(prefix="ens-cdp-", dir=parent)
        profile = chrome_profile.new_profile(self.tmp)
        self.proc = subprocess.Popen(
            [chrome_profile.CHROME, "--headless=new", "--use-mock-keychain", "--password-store=basic", "--remote-debugging-port=0",
             f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
             "--disable-gpu", "--disable-extensions", "--hide-scrollbars", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = Path(profile) / "DevToolsActivePort"
        try:
            end = time.time() + 30
            while (time.time() < end and self.proc.poll() is None
                   and not (port_file.exists() and port_file.read_text().strip())):
                time.sleep(0.05)
            if not (port_file.exists() and port_file.read_text().strip()):
                raise RuntimeError("headless Chrome did not start"
                                   + (f" (it ended with {self.proc.returncode})"
                                      if self.proc.poll() is not None else " within 30 s"))
            self.port = int(port_file.read_text().split()[0])
        except BaseException:
            self.close()                # no Chrome or profile left behind
            raise

    def page(self, width: int, height: int, mobile: bool = False) -> Page:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/json/new?about:blank", method="PUT")
        with urllib.request.urlopen(req, timeout=30) as r:
            info = json.loads(r.read())
        p = Page(info["webSocketDebuggerUrl"])
        p.send("Page.enable")
        p.send("Emulation.setDeviceMetricsOverride", {"width": width, "height": height,
                                                      "deviceScaleFactor": 2 if mobile else 1,
                                                      "mobile": mobile})
        if mobile:
            p.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})
        return p

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.tmp, ignore_errors=True)
