import asyncio
import hashlib
import importlib.machinery
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from smelite.admin import AdminError, AdminTools, digest

HELPER = Path(__file__).resolve().parent.parent / "deploy" / "smelite-admin"


def load_helper():
    loader = importlib.machinery.SourceFileLoader("smelite_admin_helper", str(HELPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class AdminToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "rpt.conf").write_text("[546050]\nduplex = 1\n")
        self.log = self.dir / "messages.log"
        self.log.write_text("".join(f"line {n}\n" for n in range(500)))
        self.tools = AdminTools(True, self.dir, self.log, "/nonexistent/helper")

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_known_files_are_listed_and_read(self):
        self.assertEqual([f["name"] for f in self.tools.files()], ["rpt.conf"])
        data = self.tools.read("rpt.conf")
        self.assertEqual(data["sha256"], digest(b"[546050]\nduplex = 1\n"))
        with self.assertRaises(AdminError):
            self.tools.read("manager.conf")
        with self.assertRaises(AdminError):
            self.tools.read("../../etc/shadow")

    def test_tail_log_returns_last_lines(self):
        text = self.tools.tail_log(20)
        self.assertTrue(text.startswith("line 480\n"))
        self.assertTrue(text.endswith("line 499\n"))

    def test_missing_helper_reports_error(self):
        self.assertFalse(asyncio.run(self.tools.check()))

    def test_demo_write_detects_conflicts(self):
        tools = AdminTools(True, self.dir, self.log, "x", demo=True)
        original = tools.read("iax.conf")
        asyncio.run(tools.write("iax.conf", "new\r\n", original["sha256"], "tester"))
        self.assertEqual(tools.read("iax.conf")["text"], "new\n")
        with self.assertRaises(AdminError):
            asyncio.run(tools.write("iax.conf", "again", original["sha256"], "tester"))

    def test_demo_backups_compare_and_restore(self):
        tools = AdminTools(True, self.dir, self.log, "x", demo=True)
        original = tools.read("rpt.conf")
        asyncio.run(tools.write("rpt.conf", original["text"] + "duplex = 2\n", original["sha256"], "t"))
        (backup,) = asyncio.run(tools.backups("rpt.conf"))
        compare = asyncio.run(tools.backup_compare("rpt.conf", backup["stamp"]))
        self.assertIn("-duplex = 2", compare["diff"])
        self.assertFalse(compare["same"])
        current = tools.read("rpt.conf")
        asyncio.run(tools.restore("rpt.conf", backup["stamp"], current["sha256"], "t"))
        self.assertEqual(tools.read("rpt.conf")["text"], original["text"])
        with self.assertRaises(AdminError):
            asyncio.run(tools.backup_compare("rpt.conf", "../x"))


class HelperWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.target = root / "rpt.conf"
        self.target.write_text("old\n")
        os.chmod(self.target, 0o640)
        self.helper = load_helper()
        self.helper.FILES = {"rpt.conf": self.target}
        self.helper.BACKUPS = root / "backups"

    def tearDown(self):
        self.tmp.cleanup()

    def run_write(self, data: bytes, expected: str):
        stdin = mock.Mock(buffer=io.BytesIO(data))
        with mock.patch("sys.stdin", stdin), mock.patch("sys.stdout", io.StringIO()) as out:
            self.helper.write("rpt.conf", expected)
        return out.getvalue().strip()

    def test_write_backs_up_and_keeps_mode(self):
        sha = hashlib.sha256(b"old\n").hexdigest()
        backup = Path(self.run_write(b"new\n", sha))
        self.assertEqual(self.target.read_text(), "new\n")
        self.assertEqual(backup.read_text(), "old\n")
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o640)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_list_show_and_restore_backup(self):
        sha_old = hashlib.sha256(b"old\n").hexdigest()
        backup = Path(self.run_write(b"new\n", sha_old))
        stamp = backup.name.removeprefix("rpt.conf.")
        with mock.patch("sys.stdout", io.StringIO()) as out:
            self.helper.list_backups("rpt.conf")
        self.assertEqual(out.getvalue(), f"{stamp}\t4\n")
        shown = io.BytesIO()
        with mock.patch("sys.stdout", mock.Mock(buffer=shown)):
            self.helper.show_backup("rpt.conf", stamp)
        self.assertEqual(shown.getvalue(), b"old\n")
        sha_new = hashlib.sha256(b"new\n").hexdigest()
        with mock.patch("sys.stdout", io.StringIO()):
            self.helper.restore("rpt.conf", stamp, sha_new)
        self.assertEqual(self.target.read_text(), "old\n")
        for name, bad in (("rpt.conf", "../../etc/shadow"), ("rpt.conf", "20260101-000000"), ("manager.conf", stamp)):
            with self.assertRaises(SystemExit), mock.patch("sys.stderr", io.StringIO()):
                self.helper.show_backup(name, bad)

    def test_write_refuses_stale_and_bad_input(self):
        with self.assertRaises(SystemExit) as stale:
            self.run_write(b"new\n", "0" * 64)
        self.assertEqual(stale.exception.code, 3)
        sha = hashlib.sha256(b"old\n").hexdigest()
        for bad in (b"a\x00b", b"\xff\xfe"):
            with self.assertRaises(SystemExit), mock.patch("sys.stderr", io.StringIO()):
                self.run_write(bad, sha)
        self.assertEqual(self.target.read_text(), "old\n")


if __name__ == "__main__":
    unittest.main()
