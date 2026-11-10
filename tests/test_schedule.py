import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from smelite.actions import ActionLog
from smelite.astdb import NodeDirectory
from smelite.auth import AuthStore
from smelite.config import NodeConfig, Settings
from smelite.monitor import Engine
from smelite.schedule import Job, ScheduleStore, days_label, due_minutes, validate

NODES = ["546051", "1999"]
COMMANDS = {"rpt cmd @ status 12", "rpt cmd @ ilink 3 %"}
SOUNDS = {"announcements/net-tonight"}


def job(**fields):
    base = {"node": "546051", "action": "connect", "at": "20:00", "days": [1], "owner": "op", "target": "27339"}
    base.update(fields)
    return Job(**base)


def check(item):
    return validate(item, nodes=NODES, commands=COMMANDS, sounds=SOUNDS)


class ValidateTests(unittest.TestCase):
    def test_link_actions_need_a_node_number(self):
        self.assertEqual(check(job()).target, "27339")
        for bad in ("", "abc", "546051"):
            with self.assertRaises(ValueError):
                check(job(target=bad))

    def test_unknown_node_time_or_days_are_refused(self):
        for fields in ({"node": "2000"}, {"at": "24:00"}, {"at": "8pm"}, {"days": []}, {"days": [7, True]}, {"action": "reboot"}):
            with self.assertRaises(ValueError, msg=fields):
                check(job(**fields))

    def test_fields_that_do_not_apply_are_cleared(self):
        played = check(job(action="play", sound="announcements/net-tonight", scope="links", permanent=True, template="x"))
        self.assertEqual((played.target, played.permanent, played.template, played.scope), ("", False, "", "links"))
        dropped = check(job(action="disconnect_all", sound="announcements/net-tonight"))
        self.assertEqual((dropped.target, dropped.sound), ("", ""))
        self.assertEqual(check(job(days=[3, 1, 3])).days, [1, 3])

    def test_sounds_and_commands_must_exist(self):
        with self.assertRaises(ValueError):
            check(job(action="play", sound="../../etc/passwd"))
        with self.assertRaises(ValueError):
            check(job(action="command", template="core restart now"))
        with self.assertRaises(ValueError):
            check(job(action="command", template="rpt cmd @ ilink 3 %", target=""))
        self.assertEqual(check(job(action="command", template="rpt cmd @ status 12")).target, "")

    def test_days_label(self):
        self.assertEqual(days_label(list(range(7))), "Every day")
        self.assertEqual(days_label([0, 1, 2, 3, 4]), "Weekdays")
        self.assertEqual(days_label([1, 3]), "Tue Thu")


class TimingTests(unittest.TestCase):
    def test_each_minute_is_due_once(self):
        self.assertEqual(due_minutes(datetime(2026, 11, 10, 20, 0, 5), datetime(2026, 11, 10, 20, 0, 15)), [])
        self.assertEqual(
            due_minutes(datetime(2026, 11, 10, 19, 59, 55), datetime(2026, 11, 10, 20, 0, 5)),
            [datetime(2026, 11, 10, 20, 0)],
        )

    def test_catch_up_is_limited(self):
        minutes = due_minutes(datetime(2026, 11, 10, 18, 0), datetime(2026, 11, 10, 20, 0, 30))
        self.assertEqual(len(minutes), 5)
        self.assertEqual(minutes[-1], datetime(2026, 11, 10, 20, 0))

    def test_runs_at_matches_day_and_time(self):
        tuesday_net = job(days=[1], at="20:00")
        self.assertTrue(tuesday_net.runs_at(datetime(2026, 11, 10, 20, 0)))
        self.assertFalse(tuesday_net.runs_at(datetime(2026, 11, 11, 20, 0)))
        self.assertFalse(tuesday_net.runs_at(datetime(2026, 11, 10, 20, 1)))
        tuesday_net.enabled = False
        self.assertFalse(tuesday_net.runs_at(datetime(2026, 11, 10, 20, 0)))


class StoreAndRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        settings = Settings(demo=True, data_dir=data)
        settings.nodes = [NodeConfig(node="546051", host="demo", port=0, user="demo", password="")]
        self.auth = AuthStore(data / "smelite.db")
        self.auth.create_user("op", "long-password", "operator", ["546051"])
        self.store = ScheduleStore(data / "schedule.db")
        self.log = ActionLog(data / "actions.db")
        directory = NodeDirectory(data / "astdb.txt", data / "asdb.txt", "", 15)
        self.engine = Engine(settings, self.auth, directory, None, self.log, self.store)

    def tearDown(self):
        self.auth.close()
        self.store.close()
        self.log.close()
        self.tmp.cleanup()

    def test_store_round_trip(self):
        saved = self.store.add(check(job(note="  Tuesday   net ")))
        self.assertEqual(self.store.get(saved.id).note, "Tuesday net")
        self.store.set_enabled(saved.id, False)
        self.assertFalse(self.store.get(saved.id).enabled)
        self.store.finished(saved.id, 100.0, "ran")
        self.assertEqual(self.store.get(saved.id).last_result, "ran")
        self.store.delete(saved.id)
        self.assertEqual(self.store.jobs(), [])

    def test_job_runs_as_owner_and_is_logged(self):
        saved = self.store.add(check(job()))
        result = asyncio.run(self.engine.run_scheduled(saved))
        self.assertTrue(result.startswith("ran rpt cmd 546051 ilink 3 27339"), result)
        (entry,) = self.log.recent()
        self.assertEqual((entry["user"], entry["action"], entry["node"]), ("op", "scheduled connect", "546051"))
        self.assertEqual(self.store.get(saved.id).last_result, result)

    def test_job_stops_when_owner_loses_access(self):
        saved = self.store.add(check(job()))
        self.auth.delete_user("op")
        self.auth.create_user("op", "long-password", "operator", ["1999"])
        self.assertIn("may not control", asyncio.run(self.engine.run_scheduled(saved)))
        self.auth.delete_user("op")
        self.assertIn("was removed", asyncio.run(self.engine.run_scheduled(saved)))


if __name__ == "__main__":
    unittest.main()
