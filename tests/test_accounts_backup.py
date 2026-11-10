import io
import json
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiohttp import FormData, web
from aiohttp.test_utils import AioHTTPTestCase

from smelite import backup
from smelite.actions import ActionLog
from smelite.admin import AdminTools
from smelite.auth import AuthStore
from smelite.history import HistoryStore
from smelite.schedule import Job, ScheduleStore
from smelite.server import (
    account_password,
    settings_backup,
    settings_inspect,
    settings_restore,
    users_update,
)


class AccountStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.auth = AuthStore(Path(self.tmp.name) / "smelite.db")
        self.auth.create_user("ada", "long-password", "admin")

    def tearDown(self):
        self.auth.close()
        self.tmp.cleanup()

    def test_set_password_keeps_only_the_current_session(self):
        user = self.auth.get_user("ada")
        kept, other = self.auth.create_session(user), self.auth.create_session(user)
        self.auth.set_password("ada", "another-password", keep_token=kept)
        self.assertIsNotNone(self.auth.user_for_token(kept))
        self.assertIsNone(self.auth.user_for_token(other))
        self.assertIsNone(self.auth.verify("ada", "long-password"))
        self.assertIsNotNone(self.auth.verify("ada", "another-password"))
        with self.assertRaises(ValueError):
            self.auth.set_password("ada", "short")
        with self.assertRaises(ValueError):
            self.auth.set_password("nobody", "long-password")

    def test_update_user_changes_role_and_nodes(self):
        self.auth.update_user("ada", "operator", ["1998"])
        user = self.auth.get_user("ada")
        self.assertEqual((user.role, user.can_command("1998"), user.can_command("1999")), ("operator", True, False))
        with self.assertRaises(ValueError):
            self.auth.update_user("ada", "root", None)
        with self.assertRaises(ValueError):
            self.auth.update_user("nobody", "viewer", None)


class BackupFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.conf = root / "conf"
        self.conf.mkdir()
        (self.conf / "nodes.ini").write_text("[1998]\nhost = 127.0.0.1\n")
        (self.conf / "favorites-club.ini").write_text("[general]\n")
        (self.conf / "dvswitch.ini.before-import").write_text("old")
        (self.conf / "notes.txt").write_text("not config")
        self.auth = AuthStore(root / "smelite.db")
        self.auth.create_user("ada", "long-password", "admin")
        self.auth.create_session(self.auth.get_user("ada"))
        self.tables = {"smelite.db": AuthStore.tables}

    def tearDown(self):
        self.auth.close()
        self.tmp.cleanup()

    def test_round_trip_leaves_out_sessions_and_stray_files(self):
        blob = backup.create(self.conf, {"smelite.db": self.auth}, "0.0.1", "node-a")
        with backup.read(blob, self.tables) as archive:
            self.assertEqual(sorted(archive.config), ["favorites-club.ini", "nodes.ini"])
            self.assertEqual((archive.users, archive.admins, archive.groups()), (1, 1, ["config", "accounts"]))
            db = sqlite3.connect(archive.data["smelite.db"])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
            db.close()
            folder = archive.data["smelite.db"].parent
        self.assertFalse(folder.exists())

    def archive(self, entries, manifest=None):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            data = json.dumps(manifest or {"app": "sm-elite", "format": 1}).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
            for info, data in entries:
                tar.addfile(info, io.BytesIO(data) if data is not None else None)
        return buffer.getvalue()

    def entry(self, name, data=b"x", kind=tarfile.REGTYPE):
        info = tarfile.TarInfo(name)
        info.type = kind
        info.size = len(data) if kind == tarfile.REGTYPE else 0
        if kind == tarfile.SYMTYPE:
            info.linkname = "/etc/shadow"
        return info, data if kind == tarfile.REGTYPE else None

    def test_hostile_or_foreign_files_are_refused(self):
        bad = [
            b"not a tarball",
            self.archive([self.entry("config/../../etc/passwd")]),
            self.archive([self.entry("config/rpt.conf")]),
            self.archive([self.entry("config/nodes.ini", kind=tarfile.SYMTYPE)]),
            self.archive([self.entry("config/nodes.ini", b"a\x00b")]),
            self.archive([self.entry("data/smelite.db", b"not sqlite")]),
            self.archive([], manifest={"app": "other", "format": 1}),
            self.archive([], manifest={"app": "sm-elite", "format": 99}),
        ]
        for blob in bad:
            with self.assertRaises(backup.BackupError):
                backup.read(blob, self.tables).__exit__()

    def test_accounts_without_an_admin_are_refused(self):
        self.auth.update_user("ada", "viewer", None)
        blob = backup.create(self.conf, {"smelite.db": self.auth}, "0.0.1", "node-a")
        with self.assertRaisesRegex(backup.BackupError, "no admin"):
            backup.read(blob, self.tables)


@web.middleware
async def token_user(request, handler):
    token = request.headers.get("X-Token")
    request["token"] = token
    request["user"] = request.app["engine"].auth.user_for_token(token) if token else None
    try:
        return await handler(request)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)


class EndpointTests(AioHTTPTestCase):
    async def get_application(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        conf = root / "conf"
        conf.mkdir()
        (conf / "nodes.ini").write_text("[1998]\n")
        self.recorded = []
        auth = AuthStore(root / "smelite.db")
        auth.create_user("ada", "long-password", "admin")
        auth.create_user("op", "long-password", "operator")
        self.engine = SimpleNamespace(
            settings=SimpleNamespace(demo=True, config_dir=conf),
            auth=auth,
            schedule=ScheduleStore(root / "schedule.db"),
            history=HistoryStore(root / "history.db"),
            actions=ActionLog(root / "actions.db"),
            admin=AdminTools(True, root, root / "messages", "", demo=True, smelite_dir=conf),
            login_failures={},
            record=lambda user, action, **fields: self.recorded.append((user, action, fields.get("detail", ""))),
        )
        self.ada = auth.create_session(auth.get_user("ada"))
        self.op = auth.create_session(auth.get_user("op"))
        app = web.Application(middlewares=[token_user])
        app["engine"] = self.engine
        app.router.add_post("/api/users/{name}", users_update)
        app.router.add_post("/api/account/password", account_password)
        app.router.add_post("/api/admin/settings-backup", settings_backup)
        app.router.add_post("/api/admin/settings-inspect", settings_inspect)
        app.router.add_post("/api/admin/settings-restore", settings_restore)
        return app

    async def asyncTearDown(self):
        await super().asyncTearDown()
        for store in (self.engine.auth, self.engine.schedule, self.engine.history, self.engine.actions):
            store.close()
        self.tmp.cleanup()

    async def post(self, path, token, **kwargs):
        return await self.client.post(path, headers={"X-Token": token}, **kwargs)

    async def test_change_own_password_needs_the_current_one(self):
        other = self.engine.auth.create_session(self.engine.auth.get_user("op"))
        wrong = await self.post("/api/account/password", self.op, json={"current": "guess-guess", "password": "brand-new-pass"})
        self.assertEqual(wrong.status, 403)
        right = await self.post("/api/account/password", self.op, json={"current": "long-password", "password": "brand-new-pass"})
        self.assertEqual(right.status, 200)
        self.assertIsNotNone(self.engine.auth.user_for_token(self.op))
        self.assertIsNone(self.engine.auth.user_for_token(other))
        self.assertIsNotNone(self.engine.auth.verify("op", "brand-new-pass"))
        self.assertEqual(self.recorded[-1][:2], ("op", "change password"))

    async def test_admin_edits_users_but_keeps_one_admin(self):
        demote = await self.post("/api/users/ada", self.ada, json={"role": "operator", "nodes": ""})
        self.assertEqual(demote.status, 400)
        limited = await self.post("/api/users/op", self.ada, json={"role": "operator", "nodes": "1998", "password": "reset-password"})
        self.assertEqual(limited.status, 200)
        user = self.engine.auth.verify("op", "reset-password")
        self.assertFalse(user.can_command("1999"))
        self.assertIsNone(self.engine.auth.user_for_token(self.op))
        self.assertEqual(self.recorded[-1][:2], ("ada", "edit user"))
        refused = await self.post("/api/users/ada", self.op, json={"role": "viewer"})
        self.assertEqual(refused.status, 401)

    async def test_backup_needs_admin_and_password(self):
        self.assertEqual((await self.post("/api/admin/settings-backup", self.op, json={"password": "long-password"})).status, 403)
        self.assertEqual((await self.post("/api/admin/settings-backup", self.ada, json={"password": "wrong"})).status, 403)

    async def test_backup_inspect_and_restore_accounts_and_schedule(self):
        self.engine.schedule.add(Job(node="1998", action="connect", at="20:00", days=[1], owner="op", target="2000"))
        response = await self.post("/api/admin/settings-backup", self.ada, json={"password": "long-password"})
        self.assertEqual(response.status, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        blob = await response.read()

        self.engine.auth.create_user("later", "long-password", "viewer")
        self.engine.schedule.delete(self.engine.schedule.jobs()[0].id)

        form = FormData()
        form.add_field("file", blob, filename="backup.tar.gz")
        info = await (await self.post("/api/admin/settings-inspect", self.ada, data=form)).json()
        self.assertEqual((info["users"], info["admins"], info["jobs"]), (2, 1, 1))
        self.assertEqual([group["id"] for group in info["groups"]], ["config", "accounts", "history"])

        form = FormData()
        form.add_field("password", "long-password")
        form.add_field("parts", "accounts,config")
        form.add_field("file", blob, filename="backup.tar.gz")
        result = await self.post("/api/admin/settings-restore", self.ada, data=form)
        body = await result.json()
        self.assertEqual(result.status, 200, body)
        self.assertEqual(body["restored"], ["1 config files", "2 users and 1 scheduled actions"])
        self.assertTrue(body["logged_out"])
        self.assertEqual(body["restart"], "demo: restart skipped")
        self.assertIsNone(self.engine.auth.get_user("later"))
        self.assertIsNone(self.engine.auth.user_for_token(self.ada))
        self.assertEqual(len(self.engine.schedule.jobs()), 1)
        self.assertEqual(self.recorded[-1][:2], ("ada", "restore settings backup"))

    async def test_restore_refuses_wrong_password_and_missing_parts(self):
        blob = backup.create(self.engine.settings.config_dir, {"smelite.db": self.engine.auth}, "0.0.1", "node-a")
        cases = [("wrong", "accounts", 403), ("long-password", "history", 400), ("long-password", "everything", 400)]
        for password, parts, status in cases:
            form = FormData()
            form.add_field("password", password)
            form.add_field("parts", parts)
            form.add_field("file", blob, filename="backup.tar.gz")
            self.assertEqual((await self.post("/api/admin/settings-restore", self.ada, data=form)).status, status)


if __name__ == "__main__":
    unittest.main()
