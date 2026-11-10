import tempfile
import unittest
from pathlib import Path

from smelite.commands import CommandError, action_command, check_command
from smelite.favorites import add_favorite, parse_favorites, remove_favorite, render_favorites, save_file, load_file
from smelite.stats import Sample, StatsCache, fold_activity, _Memory


class CommandTests(unittest.TestCase):
    def test_link_and_dtmf(self):
        self.assertEqual(action_command("connect", "1998", "27339"), "rpt cmd 1998 ilink 3 27339")
        self.assertEqual(
            action_command("connect", "1998", "27339", permanent=True),
            "rpt cmd 1998 ilink 13 27339",
        )
        self.assertEqual(action_command("disconnect", "1998", "27339"), "rpt cmd 1998 ilink 11 27339")
        self.assertEqual(action_command("disconnect_all", "1998"), "rpt cmd 1998 ilink 6")
        self.assertEqual(action_command("dtmf", "1998", digits="*712"), "rpt fun 1998 *712")

    def test_template_and_rejects_injection(self):
        built = action_command("custom", "1998", "27339", template="rpt cmd @ ilink 3 %")
        self.assertEqual(built, "rpt cmd 1998 ilink 3 27339")
        for bad in ("rpt cmd 1998 ilink 3 1; reboot", "echo hi", "rpt fun 1998 $(id)", "core show version\n"):
            with self.assertRaises(CommandError):
                check_command(bad)


class FavoriteTests(unittest.TestCase):
    def test_round_trip_and_edit(self):
        text = '\n'.join([
            "[general]",
            'label[] = "East Coast Reflector"',
            'cmd[] = "rpt cmd %node% ilink 3 27339"',
            'label[] = "=== Nets ==="',
            'cmd[] = ""',
        ])
        items = parse_favorites(text)
        self.assertEqual(items[0].node, "27339")
        self.assertTrue(items[1].header)
        items = add_favorite(items, "28404", "San Diego")
        with self.assertRaises(ValueError):
            add_favorite(items, "28404", "again")
        rendered = render_favorites(items)
        again = parse_favorites(rendered)
        self.assertEqual([item.node for item in again], ["27339", None, "28404"])
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            save_file(directory, "favorites.ini", items)
            loaded = load_file(directory, "favorites.ini")
            self.assertEqual(loaded[2].label, "San Diego")
            with self.assertRaises(ValueError):
                load_file(directory, "../secrets.ini")
        removed = remove_favorite(items, "27339")
        self.assertEqual([item.node for item in removed if item.node], ["28404"])

    def test_supermon_ng_layout(self):
        text = '\n'.join([
            "[general]",
            'label[] = "Enhanced Parrot Plano, Tx 55553"',
            'label[] = "East Coast AllStar HUB 27339"',
            'label[] = "Nets"',
            'cmd[] = "rpt cmd %node% ilink 13 55553"',
            'cmd[] = "rpt cmd %node% ilink 3 27339"',
            'cmd[] = ""',
            "",
            "[546055]",
            'label = "KC5HWB Ham Radio 2.0 Hub 43136"',
            'cmd = "rpt cmd 546055 ilink 13 43136"',
        ])
        items = parse_favorites(text)
        self.assertEqual([item.node for item in items], ["55553", "27339", None, "43136"])
        self.assertEqual(items[1].label, "East Coast AllStar HUB 27339")
        self.assertEqual([item.section for item in items], ["general"] * 3 + ["546055"])
        rendered = render_favorites(add_favorite(items, "2135", "WAN"))
        self.assertIn('[546055]\nlabel = "KC5HWB Ham Radio 2.0 Hub 43136"\ncmd = "rpt cmd 546055 ilink 13 43136"', rendered)
        again = parse_favorites(rendered)
        self.assertEqual([(item.node, item.section) for item in again],
                         [("55553", "general"), ("27339", "general"), (None, "general"), ("2135", "general"), ("43136", "546055")])


class AccessListTests(unittest.TestCase):
    def test_commands_match_the_asl3_dialplan_keys(self):
        from smelite.access import del_command, parse_lists, put_command, show_command

        self.assertEqual(show_command("allow", "546050"), "database show allowlist/546050")
        self.assertEqual(put_command("deny", "546050", "12345", "spammer"), 'database put denylist/546050 12345 "spammer"')
        self.assertEqual(put_command("denylist", "546050", "12345"), 'database put denylist/546050 12345 "added from SM-Elite"')
        self.assertEqual(del_command("allow", "546050", "546055"), "database del allowlist/546050 546055")
        for bad in (("deny", "546050", "12a"), ("deny", "5460;50", "1"), ("block", "546050", "1")):
            with self.assertRaises(CommandError):
                put_command(*bad)
        for comment in ('x" ; rpt', "a$b", "x" * 51):
            with self.assertRaises(CommandError):
                put_command("deny", "546050", "1", comment)

        raw = "\n".join([
            "/allowlist/546050/546055                          : current link",
            "/allowlist/546051/2000                            : other node",
            "/denylist/546050/12345                            : spammer",
            "/denylist/546050/2001                             : added from SM-Elite",
            "2 results found.",
        ])
        lists = parse_lists(raw, "546050")
        self.assertEqual(lists["allowlist"], [{"node": "546055", "comment": "current link"}])
        self.assertEqual([row["node"] for row in lists["denylist"]], ["2001", "12345"])


class NodeOrderTests(unittest.TestCase):
    def test_public_nodes_first_then_private_each_numeric(self):
        from smelite.config import node_order

        nodes = ["1999", "546056", "1001", "2000", "546051", "27339", "local", "1998"]
        self.assertEqual(
            sorted(nodes, key=node_order),
            ["2000", "27339", "546051", "546056", "1001", "1998", "1999", "local"],
        )


class DirectorySearchTests(unittest.TestCase):
    def test_ranks_exact_then_prefix_and_needs_every_word(self):
        from smelite.astdb import search_directory

        rows = {
            "27339": {"call": "W2ECR", "desc": "East Coast HUB1", "location": "Wilmington, NC"},
            "45192": {"call": "W2ECR", "desc": "East Coast HUB2", "location": "Wilmington, NC"},
            "2560": {"call": "K6JSI", "desc": "WIN System Hub", "location": "Los Angeles, CA"},
            "25601": {"call": "N0ONE", "desc": "Repeater", "location": "Somewhere"},
            "546050": {"call": "W5GLE", "desc": "", "location": "Alvin, Texas"},
        }
        items, total = search_directory(rows, "2560")
        self.assertEqual([item["node"] for item in items], ["2560", "25601"])
        items, total = search_directory(rows, "east coast hub2")
        self.assertEqual((total, items[0]["node"]), (1, "45192"))
        items, _ = search_directory(rows, "w5gle")
        self.assertEqual(items[0]["location"], "Alvin, Texas")
        items, total = search_directory(rows, "east", limit=1)
        self.assertEqual((len(items), total), (1, 2))
        self.assertEqual(search_directory(rows, "  "), ([], 0))


class ActivityTests(unittest.TestCase):
    def test_keyed_flag_and_txtime_delta(self):
        sample = Sample(node="1", txtime=10, keyups=1, keyed=True, at=100)
        memory, pct = fold_activity(None, sample, 100)
        self.assertEqual(pct, 100)
        quiet = Sample(node="1", txtime=10, keyups=1, keyed=False, at=110)
        _, pct = fold_activity(memory, quiet, 110)
        self.assertEqual(pct, 50)
        busy = Sample(node="1", txtime=15, keyups=2, keyed=False, at=120)
        _, pct = fold_activity(_Memory(10, 1, 110, 0), busy, 120)
        self.assertEqual(pct, 25)
        cache = StatsCache()
        seen = cache.observe(Sample(node="9", txtime=100, keyups=4, uptime=1000, keyed=False, links=3, busy_pct=10, active=True, at=50), 50)
        self.assertEqual(seen.tx_pct, 0)
        seen = cache.observe(Sample(node="9", txtime=110, keyups=5, uptime=1010, keyed=False, links=3, busy_pct=11, active=True, web=True, at=60), 60)
        self.assertGreater(seen.tx_pct, 0)
        self.assertTrue(seen.web)


if __name__ == "__main__":
    unittest.main()
