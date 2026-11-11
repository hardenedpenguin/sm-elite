import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from smelite import themes
from smelite.auth import AuthStore
from smelite.server import site_theme_set

THEME_JS = Path(__file__).resolve().parent.parent / "web" / "js" / "theme.js"
COLORS = {key: "#123abc" for key in themes.COLOR_KEYS}


class CleanTests(unittest.TestCase):
    def test_built_in_and_custom_themes(self):
        self.assertIsNone(themes.clean(None))
        self.assertEqual(themes.clean({"id": "seafoam", "colors": {"bg": "x"}}), {"id": "seafoam"})
        custom = themes.clean({"id": "custom", "label": "  Club   colors ", "colors": {**COLORS, "bg": "#ABCDEF", "extra": "#000000"}})
        self.assertEqual(custom["label"], "Club colors")
        self.assertEqual(custom["colors"]["bg"], "#abcdef")
        self.assertNotIn("extra", custom["colors"])

    def test_bad_themes_are_refused(self):
        for bad in ("dark", {"id": "rainbow"}, {"id": "custom", "colors": {**COLORS, "text": "red"}},
                    {"id": "custom", "colors": {key: value for key, value in COLORS.items() if key != "heat"}},
                    {"id": "custom", "colors": {**COLORS, "bg": "#fff;}body{"}}):
            with self.assertRaises(ValueError, msg=bad):
                themes.clean(bad)

    def test_matches_the_browser_side(self):
        source = THEME_JS.read_text(encoding="utf-8")
        built_in = re.findall(r'^\s+id: "([a-z-]+)", label:', source, re.M)
        self.assertEqual(tuple(built_in), themes.BUILT_IN)
        keys = re.findall(r'\{ key: "([a-z-]+)", label:', source)
        self.assertEqual(sorted(keys), sorted(themes.COLOR_KEYS))


@web.middleware
async def token_user(request, handler):
    token = request.headers.get("X-Token")
    request["token"] = token
    request["user"] = request.app["engine"].auth.user_for_token(token) if token else None
    try:
        return await handler(request)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)


class SiteThemeTests(AioHTTPTestCase):
    async def get_application(self):
        self.tmp = tempfile.TemporaryDirectory()
        auth = AuthStore(Path(self.tmp.name) / "smelite.db")
        auth.create_user("ada", "long-password", "admin")
        auth.create_user("op", "long-password", "operator")
        self.recorded = []
        self.engine = SimpleNamespace(
            auth=auth,
            record=lambda user, action, **fields: self.recorded.append((user, action, fields.get("detail"))),
        )
        self.ada = auth.create_session(auth.get_user("ada"))
        self.op = auth.create_session(auth.get_user("op"))
        app = web.Application(middlewares=[token_user])
        app["engine"] = self.engine
        app.router.add_post("/api/site/theme", site_theme_set)
        return app

    async def asyncTearDown(self):
        await super().asyncTearDown()
        self.engine.auth.close()
        self.tmp.cleanup()

    async def post(self, token, theme):
        return await self.client.post("/api/site/theme", headers={"X-Token": token}, json={"theme": theme})

    async def test_only_admins_set_it(self):
        self.assertEqual((await self.post(self.op, {"id": "light"})).status, 401)
        self.assertIsNone(self.engine.auth.get_setting("theme"))

    async def test_set_custom_then_clear(self):
        response = await self.post(self.ada, {"id": "custom", "label": "Club", "colors": COLORS})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["site_theme"]["label"], "Club")
        self.assertIn('"Club"', self.engine.auth.get_setting("theme"))
        self.assertEqual((await self.post(self.ada, {"id": "rainbow"})).status, 400)
        self.assertEqual((await self.post(self.ada, None)).status, 200)
        self.assertIsNone(self.engine.auth.get_setting("theme"))
        self.assertEqual([entry[2] for entry in self.recorded], ["Club", "stock dark"])


if __name__ == "__main__":
    unittest.main()
