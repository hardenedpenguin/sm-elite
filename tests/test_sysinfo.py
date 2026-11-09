import asyncio
import unittest

from smelite import sysinfo


class SysinfoTests(unittest.TestCase):
    def test_parse_database(self):
        raw = (
            "Output: /denylist/546050/1999      : added from SM-Elite\n"
            "/allowlist/546050/2000 : \n"
            "/546050/lastconnect : 27339\n"
            "3 results found.\n"
        ).replace("Output: ", "")
        rows = sysinfo.parse_database(raw)
        self.assertEqual(rows[0], {"key": "denylist/546050/1999", "value": "added from SM-Elite"})
        self.assertEqual(rows[1]["value"], "")
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(sysinfo.parse_database(raw, limit=2)), 2)

    def test_host_facts_shape(self):
        facts = sysinfo.host_facts()
        for key in ("hostname", "os", "kernel", "cpu", "cores", "load", "memory", "disks"):
            self.assertIn(key, facts)
        self.assertGreater(facts["memory"]["total"], 0)
        self.assertTrue(all(d["total"] >= d["used"] for d in facts["disks"]))

    def test_services_with_no_names(self):
        self.assertEqual(asyncio.run(sysinfo.services([])), [])


if __name__ == "__main__":
    unittest.main()
