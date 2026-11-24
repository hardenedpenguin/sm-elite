"""Node status input checks, password checks off the event loop, AMI back-off, and web hardening."""

import asyncio
import importlib.util
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from smelite import ami
from smelite.admin import tail_lines
from smelite.auth import AuthStore, User
from smelite.server import SECURITY_HEADERS, _recent_failures, command, guard, headers, websocket

STATUS_SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "ast_node_status_update.py"


def load_status_script():
    # The updater imports requests at the top; a stub is enough for these checks.
    stub = None if "requests" in sys.modules else types.ModuleType("requests")
    with mock.patch.dict(sys.modules, {"requests": stub} if stub else {}):
        spec = importlib.util.spec_from_file_location("smelite_node_status", STATUS_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class NodeStatusInputTests(unittest.TestCase):
    def setUp(self):
        self.script = load_status_script()

    def test_only_plain_node_numbers_are_kept(self):
        values = ["1998", "$(touch${IFS}/tmp/x)", "", "2000", "1998", "`id`", "12a"]
        with mock.patch("builtins.print"):
            self.assertEqual(self.script.clean_nodes(values), ["1998", "2000"])

    def test_alert_quotes_and_backslashes_are_escaped(self):
        self.assertEqual(
            self.script.alert_command("1998", 'Flood "Warning" a\\b\n'),
            'rpt set variable 1998 ALERT="Flood \\"Warning\\" a\\\\b"',
        )

    def test_alert_is_set_without_a_shell(self):
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.script.subprocess, "run", side_effect=run), mock.patch("builtins.print"):
            status = self.script.update_node_variables("1998", "Up 1 day", "0.1", "40C", "Sunny", "10%", "$(reboot)")
        self.assertEqual(status, "ok")
        for argv, kwargs in calls:
            self.assertIsInstance(argv, list)
            self.assertNotIn("bash", argv)
            self.assertFalse(kwargs.get("shell"))
        self.assertEqual(calls[-1][0], ["/usr/sbin/asterisk", "-rx", 'rpt set variable 1998 ALERT="$(reboot)"'])

    def test_bad_node_is_refused_before_anything_runs(self):
        with mock.patch.object(self.script.subprocess, "run") as run, mock.patch("builtins.print"):
            status = self.script.update_node_variables("$(id)", "", "", "", "", "", "")
        self.assertEqual(status, "skip_rpt")
        run.assert_not_called()


class NodeStatusUnitTests(unittest.TestCase):
    UNIT = STATUS_SCRIPT.parent.parent / "debian" / "sm-elite.sm-elite-node-status.service"

    def test_updater_runs_as_asterisk_not_root(self):
        import configparser

        unit = configparser.ConfigParser(strict=False, interpolation=None)
        unit.optionxform = str
        unit.read(self.UNIT)
        service = unit["Service"]
        self.assertEqual(service["User"], "asterisk")
        self.assertEqual(service["Group"], "asterisk")
        self.assertEqual(service["NoNewPrivileges"], "yes")


class AuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.auth = AuthStore(Path(self.tmp.name) / "smelite.db")
        self.auth.create_user("admin", "correct horse", "admin")

    def tearDown(self):
        self.auth.close()
        self.tmp.cleanup()

    async def test_verify_async_matches_verify(self):
        self.assertEqual((await self.auth.verify_async("admin", "correct horse")).name, "admin")
        self.assertIsNone(await self.auth.verify_async("admin", "wrong password"))
        self.assertIsNone(await self.auth.verify_async("nobody", "correct horse"))

    async def test_hashing_runs_off_the_event_loop(self):
        with mock.patch("smelite.auth.asyncio.to_thread", wraps=asyncio.to_thread) as to_thread:
            await self.auth.verify_async("admin", "correct horse")
        to_thread.assert_called_once()

    def test_unknown_names_still_check_a_hash(self):
        with mock.patch("smelite.auth._verify_password", return_value=False) as check:
            self.assertIsNone(self.auth.verify("nobody", "x"))
        check.assert_called_once()

    def test_expired_sessions_are_pruned(self):
        user = self.auth.get_user("admin")
        old = self.auth.create_session(user)
        self.auth._db.execute("UPDATE sessions SET expires = 0 WHERE token = ?", (old,))
        self.auth._db.commit()
        fresh = self.auth.create_session(user)
        tokens = [row[0] for row in self.auth._db.execute("SELECT token FROM sessions")]
        self.assertEqual(tokens, [fresh])


class AmiBackoffTests(unittest.IsolatedAsyncioTestCase):
    HOST = {"host": "192.0.2.1", "port": 5038, "user": "admin", "password": "x"}

    async def test_failed_host_is_not_retried_right_away(self):
        pool = ami.AmiPool(retry_after=30)
        connect = mock.AsyncMock(side_effect=ami.AmiError("AMI connect failed"))
        with mock.patch.object(ami.AmiClient, "connect", connect):
            with self.assertRaises(ami.AmiError):
                await pool.call(self.HOST, mock.AsyncMock())
            with self.assertRaises(ami.AmiDown):
                await pool.call(self.HOST, mock.AsyncMock())
        self.assertEqual(connect.await_count, 1)

    async def test_retry_after_the_wait(self):
        pool = ami.AmiPool(retry_after=30)
        pool._down[ami.AmiPool.key(self.HOST)] = time.monotonic() - 31
        client = SimpleNamespace(alive=True)
        with mock.patch.object(ami.AmiClient, "connect", mock.AsyncMock(return_value=client)):
            self.assertEqual(await pool.call(self.HOST, mock.AsyncMock(return_value="ok")), "ok")
        self.assertEqual(pool._down, {})


class TailTests(unittest.TestCase):
    def test_reads_only_the_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages"
            path.write_text("".join(f"line {n}\n" for n in range(5000)))
            text = tail_lines(path, 3, block=64)
            self.assertEqual(text, "line 4997\nline 4998\nline 4999\n")
            self.assertEqual(tail_lines(path, 10_000).count("\n"), 5000)

    def test_empty_and_unterminated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages"
            path.write_text("")
            self.assertEqual(tail_lines(path, 5), "")
            path.write_text("a\nb")
            self.assertEqual(tail_lines(path, 1), "b")


class FailureTrackingTests(unittest.TestCase):
    def test_stale_peers_are_dropped(self):
        now = time.time()
        engine = SimpleNamespace(login_failures={"old": [now - 900], "new": [now - 10]})
        self.assertEqual(_recent_failures(engine, "new", now), [now - 10])
        self.assertEqual(set(engine.login_failures), {"new"})


class WebHardeningTests(AioHTTPTestCase):
    async def get_application(self):
        settings = SimpleNamespace(public="read", monitored_ids=lambda: ["1998"], commands=[])
        self.engine = SimpleNamespace(
            settings=settings,
            auth=SimpleNamespace(user_for_token=lambda token: None),
            hub=SimpleNamespace(add=lambda ws: None, remove=lambda ws: None, snapshots={}),
            run_command=mock.AsyncMock(return_value="OK"),
            record=lambda *args, **kwargs: None,
        )
        app = web.Application(middlewares=[headers, guard])
        app["engine"] = self.engine

        @web.middleware
        async def operator(request, handler):
            request["user"] = User("op", "operator", None)
            return await handler(request)

        async def page(_request):
            return web.Response(text="ok")

        app.router.add_get("/", page)
        app.router.add_get("/ws", websocket)
        sub = web.Application(middlewares=[operator])
        sub["engine"] = self.engine
        sub.router.add_post("/command", command)
        app.add_subapp("/op", sub)
        return app

    async def test_security_headers_are_sent(self):
        response = await self.client.get("/")
        for key, value in SECURITY_HEADERS.items():
            self.assertEqual(response.headers.get(key), value)
        self.assertIn("frame-ancestors 'self'", response.headers["Content-Security-Policy"])

    async def test_cross_site_websocket_is_rejected(self):
        response = await self.client.get("/ws", headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status, 403)

    async def test_same_site_websocket_connects(self):
        host = f"{self.server.host}:{self.server.port}"
        socket = await self.client.ws_connect("/ws", headers={"Origin": f"http://{host}"})
        await socket.close()

    async def test_command_on_an_unconfigured_node_is_refused(self):
        body = {"node": "424242", "action": "connect", "target": "2000"}
        response = await self.client.post("/op/command", json=body)
        self.assertEqual(response.status, 403)
        self.engine.run_command.assert_not_awaited()
        response = await self.client.post("/op/command", json={**body, "node": "1998"})
        self.assertEqual(response.status, 200)
        self.engine.run_command.assert_awaited_once()



class VerifyRaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.auth = AuthStore(Path(self.tmp.name) / "smelite.db")
        self.auth.create_user("op", "old password", "operator")

    def tearDown(self):
        self.auth.close()
        self.tmp.cleanup()

    async def verify_while(self, change):
        real = asyncio.to_thread

        async def to_thread(func, *args):
            result = await real(func, *args)
            change()
            return result

        with mock.patch("smelite.auth.asyncio.to_thread", to_thread):
            return await self.auth.verify_async("op", "old password")

    async def test_reset_during_the_check_fails_the_old_password(self):
        self.assertIsNone(await self.verify_while(lambda: self.auth.set_password("op", "new password")))

    async def test_removed_during_the_check_fails(self):
        self.assertIsNone(await self.verify_while(lambda: self.auth.delete_user("op")))

    async def test_role_change_during_the_check_uses_the_current_row(self):
        user = await self.verify_while(lambda: self.auth.update_user("op", "viewer", ["1998"]))
        self.assertEqual((user.role, user.nodes), ("viewer", ("1998",)))


class ParallelLoginTests(AioHTTPTestCase):
    async def get_application(self):
        from smelite.server import account_password, login

        async def slow_wrong(name, password):
            await asyncio.sleep(0.05)
            return None if password != "right" else User(name, "operator", None)

        self.engine = SimpleNamespace(
            login_failures={},
            auth=SimpleNamespace(
                verify_async=slow_wrong,
                get_user=lambda name: None,
                set_password=lambda *args, **kwargs: None,
            ),
            record=lambda *args, **kwargs: None,
        )

        @web.middleware
        async def session(request, handler):
            request["user"] = User("op", "operator", None) if request.path == "/password" else None
            request["token"] = "t"
            return await handler(request)

        app = web.Application(middlewares=[session])
        app["engine"] = self.engine
        app.router.add_post("/login", login)
        app.router.add_post("/password", account_password)
        return app

    async def test_parallel_failures_each_count(self):
        responses = await asyncio.gather(*(
            self.client.post("/login", json={"username": "op", "password": "guess"}) for _ in range(20)
        ))
        statuses = sorted(response.status for response in responses)
        self.assertEqual(statuses.count(401), 8)
        self.assertEqual(statuses.count(429), 12)
        self.assertEqual(len(self.engine.login_failures["127.0.0.1"]), 8)

    async def test_parallel_password_checks_each_count(self):
        responses = await asyncio.gather(*(
            self.client.post("/password", json={"current": "guess", "password": "x" * 8}) for _ in range(12)
        ))
        self.assertEqual(sorted(r.status for r in responses).count(403), 8)

    async def test_right_password_gives_its_slot_back(self):
        response = await self.client.post("/password", json={"current": "right", "password": "x" * 8})
        self.assertEqual(response.status, 200)
        self.assertEqual(self.engine.login_failures.get("127.0.0.1"), [])


class HelperStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_is_bounded_when_a_child_keeps_the_pipes(self):
        import os
        import signal

        from smelite.admin import _stop

        # The shell ignores SIGTERM, and its child keeps stdout open after SIGKILL,
        # like sudo and the root helper.
        proc = await asyncio.create_subprocess_exec(
            "sh", "-c", 'trap "" TERM; sleep 20; :',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True,
        )
        try:
            started = time.monotonic()
            await _stop(proc, wait=0.3)
            self.assertLess(time.monotonic() - started, 2)
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    async def test_helper_timeout_raises_admin_error(self):
        from smelite.admin import AdminError, AdminTools

        tools = AdminTools(True, Path("/nonexistent"), Path("/nonexistent"), "/nonexistent")
        proc = mock.Mock(pid=1, terminate=mock.Mock(), kill=mock.Mock())
        proc.communicate = mock.AsyncMock(side_effect=asyncio.TimeoutError)
        proc.wait = mock.AsyncMock(return_value=0)
        with mock.patch("smelite.admin.asyncio.create_subprocess_exec", mock.AsyncMock(return_value=proc)):
            with self.assertRaises(AdminError):
                await tools.run_helper(["check"])
        proc.terminate.assert_called_once()


class RestartResultTests(unittest.IsolatedAsyncioTestCase):
    async def restart(self, error):
        from smelite.monitor import Engine

        engine = SimpleNamespace(run_command=mock.AsyncMock(side_effect=error))
        return await Engine.asterisk_action(engine, "asterisk_restart", "1998", "admin")

    async def test_drop_after_sending_counts_as_restarting(self):
        self.assertIn("restarting", await self.restart(ami.AmiError("AMI connection dropped")))

    async def test_no_connection_is_an_error(self):
        for error in (ami.AmiDown("down"), ami.AmiNotSent("AMI connect failed")):
            with self.assertRaises(ami.AmiNotSent):
                await self.restart(error)

    async def test_failed_connect_is_reported_as_not_sent(self):
        pool = ami.AmiPool()
        host = {"host": "192.0.2.1", "port": 5038, "user": "admin", "password": "x"}
        with mock.patch.object(ami.AmiClient, "connect", mock.AsyncMock(side_effect=ami.AmiError("refused"))):
            with self.assertRaises(ami.AmiNotSent):
                await pool.call(host, mock.AsyncMock())


if __name__ == "__main__":
    unittest.main()
