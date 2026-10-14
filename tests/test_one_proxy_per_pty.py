"""The hub sees one session object per PTY while the host has it (10-13).

The hub keeps state on a session object (the hub line it typed, its typing
lease) and tells seats apart by identity. The PTY host client hands out a
fresh object per lookup, so without this the hub-line reservation saw
"another seat" every time and every hub line was refused as "stopped before
the message could be typed"."""
import threading
import unittest

import dashboard
import rotation


class _FreshProxy:
    def __init__(self, pty_id, alive=True):
        self.id = pty_id
        self.meta = {}
        self._info = {"id": pty_id, "alive": alive}
        self._info_at = 0.0

    def alive(self):
        return self._info["alive"]


class _FreshPerLookupClient:
    """What backends.ptyhost.Client does: a new Proxy on every call."""

    pid, version = 1, "v"

    def __init__(self):
        self.live = set()
        self.killed = []
        self.lookups = 0

    def create(self, argv, **kwargs):
        pty_id = f"pty-{len(self.live) + 1:08x}"
        self.live.add(pty_id)
        return _FreshProxy(pty_id)

    def get(self, pty_id):
        self.lookups += 1
        return _FreshProxy(pty_id) if pty_id in self.live else None

    def kill(self, pty_id):
        self.killed.append(pty_id)
        return bool(self.live.discard(pty_id) or True)

    def list_sessions(self):
        return [{"id": x} for x in self.live]


class OneProxyPerPtyTest(unittest.TestCase):
    def test_create_and_every_get_return_the_same_object(self):
        host = dashboard.OneProxyPerPty(_FreshPerLookupClient())
        made = host.create(["x"])
        self.assertIs(host.get(made.id), made)
        made.hub_line_typing_at = 42
        self.assertEqual(host.get(made.id).hub_line_typing_at, 42)
        self.assertIsNone(host.get("pty-nobody"))

    def test_a_fresh_lookup_refreshes_what_the_host_said(self):
        client = _FreshPerLookupClient()
        host = dashboard.OneProxyPerPty(client)
        made = host.create(["x"])
        again = host.get(made.id)
        self.assertIs(again, made)
        self.assertEqual(again._info, {"id": made.id, "alive": True})

    def test_kill_and_a_gone_pty_drop_the_object(self):
        client = _FreshPerLookupClient()
        host = dashboard.OneProxyPerPty(client)
        made = host.create(["x"])
        host.kill(made.id)
        self.assertEqual(client.killed, [made.id])
        self.assertIsNone(host.get(made.id))
        client.live.add(made.id)                    # the id comes back (a new host)
        self.assertIsNot(host.get(made.id), made)

    def test_everything_else_reaches_the_client(self):
        client = _FreshPerLookupClient()
        host = dashboard.OneProxyPerPty(client)
        self.assertEqual((host.pid, host.version), (1, "v"))
        host.create(["x"])
        self.assertEqual(len(host.list_sessions()), 1)

    def test_hub_line_reservation_accepts_the_seat_it_was_given(self):
        # Through the real reservation: the participant's recorded PTY looked
        # up again must count as the same seat as the session being typed into.
        client = _FreshPerLookupClient()
        host = dashboard.OneProxyPerPty(client)
        sess = host.create(["x"])
        sess.meta = {"room": "room-1", "identity": "claude"}
        room = {"id": "room-1", "participants": [
            {"identity": "claude", "kind": "agent", "ptyId": sess.id}]}
        saved_get, saved_room = dashboard.ptyrun.get, dashboard.chatroom.get_room
        dashboard.ptyrun.get = host.get
        dashboard.chatroom.get_room = lambda rid, public=True: room if rid == "room-1" else None
        try:
            token = rotation.reserve_hub_line(sess)
            self.assertIsNotNone(token)
            rotation.release_hub_line(sess, token)
            # Another seat recorded for the agent is still refused.
            room["participants"][0]["ptyId"] = host.create(["y"]).id
            self.assertIsNone(rotation.reserve_hub_line(sess))
        finally:
            dashboard.ptyrun.get, dashboard.chatroom.get_room = saved_get, saved_room


if __name__ == "__main__":
    unittest.main()
