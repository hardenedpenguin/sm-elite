import tempfile
import unittest
from pathlib import Path

from smelite.history import HistoryStore, KeyTracker


def snap(rx=False, conns=(), online=True):
    return {"node": "546050", "online": online, "rx_keyed": rx, "conns": list(conns)}


def conn(node, ptt, since_key=-1, since_unkey=-1):
    return {"node": node, "desc": f"desc {node}", "ptt": ptt, "since_key": since_key, "since_unkey": since_unkey}


class TrackerTests(unittest.TestCase):
    def test_link_keyup_uses_sawstat_timers(self):
        tracker = KeyTracker()
        done, changed = tracker.observe(snap(conns=[conn("546055", True, since_key=3)]), now=100)
        self.assertEqual(done, [])
        self.assertTrue(changed)
        live = tracker.live("546050")
        self.assertEqual(live[0].started, 97)
        self.assertEqual(live[0].as_dict(100)["seconds"], 3)

        done, changed = tracker.observe(snap(conns=[conn("546055", True, since_key=5)]), now=102)
        self.assertEqual((done, changed), ([], False))

        done, changed = tracker.observe(snap(conns=[conn("546055", False, since_unkey=1)]), now=110)
        self.assertTrue(changed)
        self.assertEqual(done[0].source, "546055")
        self.assertEqual(done[0].ended, 109)
        self.assertEqual(done[0].as_dict()["seconds"], 12)
        self.assertEqual(tracker.live("546050"), [])

    def test_stale_unkey_timer_never_ends_before_last_keyed_poll(self):
        tracker = KeyTracker()
        tracker.observe(snap(conns=[conn("27339", True, since_key=2)]), now=100)
        tracker.observe(snap(conns=[conn("27339", True, since_key=2)]), now=105)
        done, _ = tracker.observe(snap(conns=[conn("27339", False, since_unkey=40)]), now=106)
        self.assertEqual(done[0].started, 98)
        self.assertEqual(done[0].ended, 105)

    def test_local_receiver_and_offline_close(self):
        tracker = KeyTracker()
        tracker.observe(snap(rx=True), now=50)
        done, _ = tracker.observe(snap(online=False), now=58)
        self.assertEqual(done[0].source, "local")
        self.assertEqual(done[0].desc, "Local radio")
        self.assertEqual(done[0].ended - done[0].started, 8)

    def test_drops_blips(self):
        tracker = KeyTracker()
        tracker.observe(snap(rx=True), now=10)
        done, changed = tracker.observe(snap(), now=10.2)
        self.assertTrue(changed)
        self.assertEqual(done, [])


class StoreTests(unittest.TestCase):
    def test_store_recent_and_prune(self):
        tracker = KeyTracker()
        with tempfile.TemporaryDirectory() as tmp:
            store = HistoryStore(Path(tmp) / "history.db", keep_days=1)
            tracker.observe(snap(rx=True), now=1000)
            done, _ = tracker.observe(snap(), now=1004)
            store.add(done[0])
            tracker.observe(snap(conns=[conn("2560", True, since_key=0)]), now=200000)
            done, _ = tracker.observe(snap(conns=[conn("2560", False, since_unkey=0)]), now=200006)
            store.add(done[0])
            rows = store.recent(["546050"])
            self.assertEqual([row["source"] for row in rows], ["2560", "local"])
            self.assertEqual(rows[0]["seconds"], 6)
            self.assertEqual(store.recent(["1999"]), [])
            self.assertEqual(store.prune(now=200010), 1)
            self.assertEqual(len(store.recent(["546050"])), 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
