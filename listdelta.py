"""Deltas for the lists the pages poll (#228).

``/api/sessions`` and ``/api/projects`` were sent whole on every poll: 1.5 MB
and 1.8 MB every few seconds per open tab, while a poll typically changed nine
rows (live tasks' idle seconds). A page that sends ``since=<rev>`` gets only
what changed since the answer it holds:

    {"rev": R, "full": true,  "items": [...]}                 first load, or an unknown rev
    {"rev": R, "full": false, "order": [keys], "items": [changed or new items]}

An item with a nested list (a project's ``sessions``) is a delta itself when
the page already holds it: its top fields whole, ``sessions`` only the entries
that changed, and ``sessionsOrder`` every entry's key in order
(``"subDelta": true``). A list whose keys repeat is always sent in full.

The feed keeps the last ``keep`` answers' fingerprints (one 8-byte hash per
row), never the rows; a rev it no longer holds gets the full list. The rev is
the fingerprint of the content, so two identical answers share it and a page
holding it gets an empty delta.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict


def _h(obj) -> str:
    return hashlib.blake2b(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode(),
                           digest_size=8).hexdigest()


class Feed:
    def __init__(self, key: str, sub: str = "", sub_key: str = "", keep: int = 32):
        self.key, self.sub, self.sub_key, self.keep = key, sub, sub_key, keep
        self._lock = threading.Lock()
        self._snaps: OrderedDict[str, dict] = OrderedDict()
        self._memo: tuple = (None, None, None)     # (items, rev, snap): the same list object answers at once

    def _kid(self, item, field):
        return str(item.get(field) or "")

    def _snapshot(self, items: list) -> tuple[str, dict | None]:
        snap: dict = {}
        for it in items:
            k = self._kid(it, self.key)
            if not k or k in snap:
                return "", None
            if self.sub and isinstance(it.get(self.sub), list):
                subs: dict | None = {}
                for s in it[self.sub]:
                    sk = self._kid(s, self.sub_key)
                    if not sk or sk in subs:
                        subs = None
                        break
                    subs[sk] = _h(s)
                if subs is None:
                    # Repeated keys: the item goes whole, so it is fingerprinted whole.
                    snap[k] = (_h(it), None)
                    continue
                # The order of the entries is part of the item: a reorder alone sends it.
                top = _h([{f: v for f, v in it.items() if f != self.sub}, list(subs)])
                snap[k] = (top, subs)
            else:
                snap[k] = (_h(it), None)
        rev = _h([[k, v[0], sorted(v[1].items()) if v[1] else None] for k, v in snap.items()])
        return rev, snap

    def answer(self, items: list, since: str = "") -> dict:
        with self._lock:
            if self._memo[0] is items:
                rev, snap = self._memo[1], self._memo[2]
            else:
                rev, snap = self._snapshot(items)
                self._memo = (items, rev, snap)
                if snap is not None:
                    self._snaps[rev] = snap
                    self._snaps.move_to_end(rev)
                    while len(self._snaps) > self.keep:
                        self._snaps.popitem(last=False)
            old = self._snaps.get(since) if since and snap is not None else None
        if old is None:
            return {"rev": rev, "full": True, "items": items}
        out = []
        for it in items:
            k = self._kid(it, self.key)
            was, now = old.get(k), snap[k]
            if was == now:
                continue
            if was is None or not self.sub or was[1] is None or now[1] is None:
                out.append(it)
                continue
            part = {f: v for f, v in it.items() if f != self.sub}
            part[self.sub] = [s for s in it[self.sub] if was[1].get(self._kid(s, self.sub_key)) != now[1][self._kid(s, self.sub_key)]]
            part[self.sub + "Order"] = [self._kid(s, self.sub_key) for s in it[self.sub]]
            part["subDelta"] = True
            out.append(part)
        return {"rev": rev, "full": False, "order": [self._kid(it, self.key) for it in items], "items": out}
