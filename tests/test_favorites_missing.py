import tempfile
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from smelite.server import favorites_get, favorites_watch


@web.middleware
async def anonymous(request, handler):
    request["user"] = None
    return await handler(request)


class MissingFavoritesTests(AioHTTPTestCase):
    async def get_application(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = SimpleNamespace(
            settings=SimpleNamespace(public="read", favorites_dir=Path(self.tmp.name) / "removed"),
            favorite_file="favorites.ini",
        )
        app = web.Application(middlewares=[anonymous])
        app["engine"] = self.engine
        app.router.add_get("/api/favorites", favorites_get)
        app.router.add_post("/api/favorites/watch", favorites_watch)
        return app

    async def asyncTearDown(self):
        await super().asyncTearDown()
        self.tmp.cleanup()

    async def test_missing_file_is_not_found(self):
        response = await self.client.post("/api/favorites/watch", json={"file": "favorites.ini"})
        self.assertEqual(response.status, 404)
        self.assertEqual((await response.json())["error"], "favorites file was not found")
        self.assertEqual((await self.client.get("/api/favorites?file=favorites.ini")).status, 404)

    async def test_watch_follows_a_file_that_exists(self):
        folder = self.engine.settings.favorites_dir
        folder.mkdir()
        (folder / "favorites-net.ini").write_text("[general]\n")
        response = await self.client.post("/api/favorites/watch", json={"file": "favorites-net.ini"})
        self.assertEqual(response.status, 200)
        self.assertEqual(self.engine.favorite_file, "favorites-net.ini")
