"""The Music Assistant client: the group list, retries, and what is remembered. No network: sessions are stubs."""
import json
import os
import time
import unittest

from helpers import TMP, Base, m, state_file


def client(**kw):
    cfg = dict(m.DEFAULTS, state_dir=TMP, ma_token="x", ma_url="http://ma.invalid", **kw)
    c = m.MAClient(cfg, lambda: None)
    c.player_id = "L"
    c.RETRY_WAIT = 0
    return c


def players():
    return [
        {"player_id": "L", "display_name": "ma-player1", "type": "player", "available": True,
         "group_childs": ["L", "D"], "can_group_with": ["S", "D", "X", "K", "O"]},
        {"player_id": "D", "display_name": "ma-player1 display", "type": "display", "available": True},
        {"player_id": "S", "display_name": "Living room", "type": "player", "available": True},
        {"player_id": "X", "display_name": "Kitchen", "type": "player", "available": True},
        {"player_id": "K", "display_name": "Cellar", "type": "player", "available": True},
        {"player_id": "O", "display_name": "Bedroom", "type": "player", "available": False},
    ]


class StubSession:
    """A stand-in for MAClient._session that fails `fail` times and then records the calls it receives."""

    def __init__(self, fail=0):
        self.fail, self.attempts, self.calls = fail, 0, []

    def __call__(self):
        self.attempts += 1
        if self.attempts <= self.fail:
            raise RuntimeError("Network is unreachable")

        class WS:
            def close(self):
                pass

        return WS(), lambda cmd, **args: self.calls.append((cmd, args))


class Rooms(Base):
    def test_the_leader_and_one_row_per_candidate(self):
        rooms = client()._build_rooms(players())
        self.assertEqual(rooms["leader"]["name"], "ma-player1")
        self.assertEqual([r["id"] for r in rooms["rows"]], ["S", "X", "K", "O"])
        self.assertFalse(any(r["member"] for r in rooms["rows"]))

    def test_displays_and_the_leader_never_show_up(self):
        ids = [r["id"] for r in client()._build_rooms(players())["rows"]]
        self.assertNotIn("D", ids)
        self.assertNotIn("L", ids)

    def test_members_come_first_and_offline_players_last(self):
        data = players()
        data[0]["group_childs"] = ["L", "D", "K"]
        rows = client()._build_rooms(data)["rows"]
        self.assertEqual(rows[0]["id"], "K")
        self.assertTrue(rows[0]["member"])
        self.assertEqual(rows[-1]["id"], "O")
        self.assertFalse(rows[-1]["available"])

    def test_an_unknown_leader_gives_no_rows(self):
        rooms = client()._build_rooms([p for p in players() if p["player_id"] != "L"])
        self.assertIsNone(rooms["leader"])
        self.assertEqual(rooms["rows"], [])


class GroupChanges(Base):
    def run_change(self, c, add, fail=0):
        c._session = StubSession(fail)
        done, retries, refreshed = [], [], []
        c.refresh_rooms_async = lambda force=False, **kw: refreshed.append(force)
        c.set_member_async("S", add, done.append, lambda: retries.append(1))
        for _ in range(100):
            if done:
                break
            time.sleep(0.02)
        return c._session, done, retries, refreshed

    def test_add_and_remove_send_the_right_arguments(self):
        session, done, _, refreshed = self.run_change(client(), True)
        self.assertEqual(done, [True])
        self.assertEqual(session.calls, [("players/cmd/set_members", {"target_player": "L", "player_ids_to_add": ["S"]})])
        self.assertEqual(refreshed, [True])                                           # the list is refreshed afterwards
        session, done, _, _ = self.run_change(client(), False)
        self.assertEqual(session.calls, [("players/cmd/set_members", {"target_player": "L", "player_ids_to_remove": ["S"]})])

    def test_a_failing_attempt_is_retried(self):
        session, done, retries, _ = self.run_change(client(), True, fail=2)
        self.assertEqual(done, [True])
        self.assertEqual(len(retries), 2)
        self.assertEqual(len(session.calls), 1)

    def test_it_gives_up_after_three_attempts(self):
        session, done, retries, refreshed = self.run_change(client(), True, fail=5)
        self.assertEqual(done, [False])
        self.assertEqual(session.calls, [])
        self.assertEqual(refreshed, [])


class Playing(Base):
    def play(self, c, fail):
        c._session = StubSession(fail)
        done = []
        c.play_async({"name": "Jazz", "uri": "library://radio/9", "logo": None}, done.append)
        for _ in range(100):
            if done:
                break
            time.sleep(0.02)
        return c._session, done

    def test_playing_a_station_remembers_it(self):
        c = client()
        session, done = self.play(c, 0)
        self.assertEqual(done, [True])
        self.assertEqual(session.calls[0][0], "player_queues/play_media")
        self.assertEqual(session.calls[0][1], {"queue_id": "L", "media": "library://radio/9"})
        self.assertEqual(c.last_played(), {"uri": "library://radio/9", "name": "Jazz"})
        with open(state_file("last_played.json")) as f:
            self.assertEqual(json.load(f)["name"], "Jazz")

    def test_it_retries_and_then_gives_up(self):
        _, done = self.play(client(), 2)
        self.assertEqual(done, [True])
        os.remove(state_file("last_played.json"))                                      # start the next client clean
        c = client()
        _, done = self.play(c, 5)
        self.assertEqual(done, [False])
        self.assertIsNone(c.last_played())


class Remembering(Base):
    def test_the_last_station_survives_a_restart(self):
        from PIL import Image
        c = client()
        c.set_last("library://radio/7", "KINK", Image.new("RGB", (72, 72), (200, 30, 30)))
        again = client()
        self.assertEqual(again.last_played(), {"uri": "library://radio/7", "name": "KINK"})
        self.assertEqual(again.last_logo_for().size, (72, 72))

    def test_a_different_station_never_keeps_the_old_logo(self):
        from PIL import Image
        c = client()
        c.set_last("library://radio/7", "KINK", Image.new("RGB", (72, 72), (200, 30, 30)))
        c.set_last("library://radio/3", "MNM")                                         # no logo known for this one
        self.assertIsNone(c.last_logo_for())
        self.assertFalse(os.path.exists(state_file("last_logo.png")))

    def test_the_same_station_without_a_logo_keeps_its_logo(self):
        from PIL import Image
        c = client()
        c.set_last("library://radio/7", "KINK", Image.new("RGB", (72, 72), (200, 30, 30)))
        c.set_last("library://radio/7", "KINK")
        self.assertIsNotNone(c.last_logo_for())

    def test_the_logo_is_found_in_the_station_list(self):
        from PIL import Image
        c = client()
        c.stations["all"] = [{"name": "Q", "uri": "library://radio/5", "logo": Image.new("RGB", (72, 72))}]
        c.set_last("library://radio/5", "Q")
        self.assertIsNotNone(c.last_logo_for())


class StationList(Base):
    def test_loading_retries_and_an_error_is_remembered(self):
        c = client()
        c._session = StubSession(fail=99)
        c.refresh_async(force=True)
        for _ in range(100):
            if not c.loading:
                break
            time.sleep(0.02)
        self.assertEqual(c.snapshot()["error"], "load_failed")
        c._session = lambda: (type("W", (), {"close": lambda s: None})(), lambda cmd, **a: [])
        c.refresh_async(force=True)                                                    # a tap retries
        for _ in range(100):
            if not c.loading:
                break
            time.sleep(0.02)
        self.assertIsNone(c.snapshot()["error"])


if __name__ == "__main__":
    unittest.main()
