import unittest
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, make_mocked_request
from multidict import CIMultiDict

from smelite.server import _cookie_name, _session_tokens, forwarded


class CookieTests(unittest.TestCase):
    def tokens(self, *headers):
        request = make_mocked_request("GET", "/", headers=CIMultiDict(("Cookie", value) for value in headers))
        return _session_tokens(request)

    def test_survives_malformed_cookies_from_other_apps(self):
        header = 'prefs={"theme":"dark","x":1}; supermon61=abc; smelite=tok-1'
        self.assertEqual(self.tokens(header), ["tok-1"])

    def test_collects_every_candidate(self):
        self.assertEqual(self.tokens("smelite=old; a=b", "smelite=new"), ["old", "new"])
        self.assertEqual(self.tokens("other=1"), [])

    def test_https_uses_its_own_cookie(self):
        plain = make_mocked_request("GET", "/", headers=CIMultiDict(Cookie="smelite=a; __Secure-smelite=b"))
        secure = plain.clone(scheme="https")
        self.assertEqual(_cookie_name(plain), "smelite")
        self.assertEqual(_session_tokens(plain), ["a"])
        self.assertEqual(_cookie_name(secure), "__Secure-smelite")
        self.assertEqual(_session_tokens(secure), ["b"])


class ForwardedTests(AioHTTPTestCase):
    trusted = {"127.0.0.1", "::1"}

    async def get_application(self):
        async def echo(request):
            return web.json_response({"scheme": request.scheme, "remote": request.remote})

        app = web.Application(middlewares=[forwarded])
        app["engine"] = SimpleNamespace(settings=SimpleNamespace(trusted_proxies=self.trusted))
        app.router.add_get("/", echo)
        return app

    async def test_trusted_proxy_headers_apply(self):
        headers = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "6.6.6.6, 10.8.0.20"}
        data = await (await self.client.get("/", headers=headers)).json()
        self.assertEqual(data, {"scheme": "https", "remote": "10.8.0.20"})

    async def test_no_headers_keeps_peer(self):
        data = await (await self.client.get("/")).json()
        self.assertEqual(data["scheme"], "http")
        self.assertIn(data["remote"], self.trusted)


class UntrustedForwardedTests(ForwardedTests):
    trusted = set()

    async def test_trusted_proxy_headers_apply(self):
        headers = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "10.8.0.20"}
        data = await (await self.client.get("/", headers=headers)).json()
        self.assertEqual(data["scheme"], "http")
        self.assertNotEqual(data["remote"], "10.8.0.20")

    async def test_no_headers_keeps_peer(self):
        data = await (await self.client.get("/")).json()
        self.assertEqual(data["scheme"], "http")


if __name__ == "__main__":
    unittest.main()
