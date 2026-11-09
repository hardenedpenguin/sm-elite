import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from smelite.auth import AuthStore, setup_code, setup_code_matches
from smelite.server import setup


class SetupCodeTests(unittest.TestCase):
    def test_code_is_private_stable_and_forgiving(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "setup-code"
            code = setup_code(path)
            self.assertRegex(code, r"^[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(setup_code(path), code)
            self.assertTrue(setup_code_matches(path, code.lower().replace("-", " ")))
            self.assertFalse(setup_code_matches(path, ""))
            self.assertFalse(setup_code_matches(path, "AAAA-AAAA-AAAA"))


class SetupEndpointTests(AioHTTPTestCase):
    async def get_application(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.engine = SimpleNamespace(
            settings=SimpleNamespace(demo=False, data_dir=data),
            auth=AuthStore(data / "smelite.db"),
            login_failures={},
        )
        self.code = setup_code(data / "setup-code")
        app = web.Application()
        app["engine"] = self.engine
        app.router.add_post("/api/setup", setup)
        return app

    async def asyncTearDown(self):
        await super().asyncTearDown()
        self.engine.auth.close()
        self.tmp.cleanup()

    async def post(self, code):
        return await self.client.post("/api/setup", json={"username": "owner", "password": "long-password", "code": code})

    async def test_wrong_code_is_refused(self):
        response = await self.post("WRONG-CODE-0000")
        self.assertEqual(response.status, 403)
        self.assertEqual(self.engine.auth.user_count(), 0)

    async def test_right_code_creates_admin_once(self):
        response = await self.post(self.code)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.engine.auth.user_count(), 1)
        self.assertFalse((Path(self.tmp.name) / "setup-code").exists())
        self.assertEqual((await self.post(self.code)).status, 409)

    async def test_guessing_is_rate_limited(self):
        for _ in range(8):
            await self.post("NOPE-NOPE-NOPE")
        self.assertEqual((await self.post(self.code)).status, 429)


if __name__ == "__main__":
    unittest.main()
