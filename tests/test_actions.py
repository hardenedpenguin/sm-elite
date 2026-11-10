import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from smelite.actions import ActionLog
from smelite.auth import AuthStore
from smelite.server import login


class ActionLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = ActionLog(Path(self.tmp.name) / "actions.db", keep_days=30)

    def tearDown(self):
        self.log.close()
        self.tmp.cleanup()

    def test_newest_first_with_paging(self):
        for n in range(5):
            self.log.add("op", "connect", node="546051", target=str(2000 + n), now=1000.0 + n)
        rows = self.log.recent(2)
        self.assertEqual([row["target"] for row in rows], ["2004", "2003"])
        older = self.log.recent(10, before=rows[-1]["at"])
        self.assertEqual([row["target"] for row in older], ["2002", "2001", "2000"])

    def test_long_and_multiline_text_is_clipped(self):
        self.log.add("op", "speak", detail="line one\nline two " + "x" * 400, now=1000.0)
        (row,) = self.log.recent()
        self.assertNotIn("\n", row["detail"])
        self.assertLessEqual(len(row["detail"]), 200)

    def test_old_entries_are_pruned(self):
        day = 86400
        self.log.add("op", "connect", now=1000.0)
        self.log.add("op", "disconnect", now=1000.0 + 31 * day)
        self.assertEqual([row["action"] for row in self.log.recent()], ["disconnect"])


class LoginLogTests(AioHTTPTestCase):
    async def get_application(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.recorded = []
        auth = AuthStore(data / "smelite.db")
        auth.create_user("owner", "long-password", "admin")
        self.engine = SimpleNamespace(
            auth=auth,
            login_failures={},
            record=lambda user, action, **fields: self.recorded.append((user, action, fields.get("detail", ""))),
        )
        app = web.Application()
        app["engine"] = self.engine
        app.router.add_post("/api/login", login)

        @web.middleware
        async def no_user(request, handler):
            request["user"] = None
            return await handler(request)

        app.middlewares.append(no_user)
        return app

    async def asyncTearDown(self):
        await super().asyncTearDown()
        self.engine.auth.close()
        self.tmp.cleanup()

    async def test_logins_are_logged_without_unknown_names(self):
        await self.client.post("/api/login", json={"username": "owner", "password": "wrong-password"})
        await self.client.post("/api/login", json={"username": "s3cret-typed-here", "password": "x"})
        await self.client.post("/api/login", json={"username": "owner", "password": "long-password"})
        self.assertEqual(self.recorded, [
            ("owner", "login failed", ""),
            ("", "login failed", "unknown username"),
            ("owner", "log in", ""),
        ])


if __name__ == "__main__":
    unittest.main()
