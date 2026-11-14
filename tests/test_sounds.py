import asyncio
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from aiohttp import FormData, web
from aiohttp.test_utils import AioHTTPTestCase

from smelite import monitor, sounds
from smelite.astdb import NodeDirectory
from smelite.auth import AuthStore
from smelite.config import Settings
from smelite.monitor import Engine
from smelite.server import sounds_upload
from smelite.sounds import SoundError, Sounds, clean_text
from test_admin import load_helper

FAKE_PIPER = """#!/bin/sh
while [ "$1" ]; do [ "$1" = --output_file ] && out=$2; shift; done
cat > "$out"
"""
FAKE_SOX = """#!/bin/sh
[ -n "$SOX_ARGS" ] && echo "$@" > "$SOX_ARGS"
prev=
for arg; do [ "$prev" = ul ] && out=$arg; prev=$arg; done
head -c "${SOX_BYTES:-16000}" /dev/zero > "$out"
"""


def script(path: Path, body: str) -> str:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class SoundsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.dir = root / "sounds"
        (self.dir / "announcements").mkdir(parents=True)
        (self.dir / "reboot.ulaw").write_bytes(b"\0" * 8000)
        (self.dir / "announcements" / "net.ulaw").write_bytes(b"\0" * 16000)
        (self.dir / "bad name.ulaw").write_bytes(b"\0")
        (self.dir / "notes.txt").write_text("x")
        voices = root / "voices"
        voices.mkdir()
        (voices / "en_US-amy-low.onnx").write_bytes(b"")
        (voices / "en_US-amy-low.onnx.json").write_text("{}")
        (voices / "orphan.onnx").write_bytes(b"")
        settings = SimpleNamespace(
            sounds_dir=self.dir,
            voices_dir=voices,
            piper=script(root / "piper", FAKE_PIPER),
            sox=script(root / "sox", FAKE_SOX),
            tts_max_chars=50,
        )
        self.helper_calls = []

        async def helper(args, data):
            self.helper_calls.append((args, data))
            return "ok"

        self.sounds = Sounds(settings, helper)
        self.temp_patch = mock.patch.object(sounds, "TEMP_DIR", root / "tts")
        self.temp_patch.start()

    def tearDown(self):
        self.temp_patch.stop()
        self.tmp.cleanup()

    def test_clean_text(self):
        self.assertEqual(clean_text("  hello\n\tworld\x07 ", 50), "hello world")
        with self.assertRaises(SoundError):
            clean_text("   ", 50)
        with self.assertRaises(SoundError):
            clean_text("x" * 51, 50)

    def test_library_and_voices(self):
        names = {entry["name"]: entry for entry in self.sounds.library()}
        self.assertEqual(set(names), {"reboot", "announcements/net"})
        self.assertEqual(names["announcements/net"]["seconds"], 2.0)
        self.assertTrue(names["announcements/net"]["saved"])
        self.assertEqual(self.sounds.voices(), ["en_US-amy-low"])
        self.assertTrue(self.sounds.tts_ready())

    def test_resolve_only_library_names(self):
        self.assertEqual(self.sounds.resolve("reboot"), str(self.dir / "reboot"))
        for bad in ("../etc/passwd", "bad name", "/tmp/x", "missing"):
            with self.assertRaises(SoundError):
                self.sounds.resolve(bad)

    def test_speak_renders_mulaw_and_cleans_up(self):
        async def run():
            base, seconds = await self.sounds.speak("Net tonight at eight", "en_US-amy-low")
            return base, seconds

        base, seconds = asyncio.run(run())
        self.assertEqual(seconds, 2.0)
        self.assertTrue(os.path.exists(base + ".ul"))
        self.assertFalse(os.path.exists(base + ".wav"))
        self.assertEqual(os.stat(base + ".ul").st_mode & 0o777, 0o644)
        with self.assertRaises(SoundError):
            asyncio.run(self.sounds.speak("hi", "orphan"))

    def test_save_sends_audio_to_helper(self):
        entry = asyncio.run(self.sounds.save("net-tonight", "Net tonight", "en_US-amy-low"))
        self.assertEqual(entry["name"], "announcements/net-tonight")
        (args, data), = self.helper_calls
        self.assertEqual(args, ["sound-save", "net-tonight"])
        self.assertEqual(len(data), 16000)
        with self.assertRaises(SoundError):
            asyncio.run(self.sounds.save("../evil", "x", "en_US-amy-low"))
        with self.assertRaises(SoundError):
            asyncio.run(self.sounds.delete("reboot/../x"))

    def test_upload_converts_with_forced_type_and_saves(self):
        args_file = Path(self.tmp.name) / "sox-args"
        with mock.patch.dict(os.environ, {"SOX_ARGS": str(args_file)}):
            entry = asyncio.run(self.sounds.upload("club-id", "Club ID.MP3", b"ID3fake"))
        self.assertEqual(entry, {"name": "announcements/club-id", "seconds": 2.0, "saved": True})
        (args, data), = self.helper_calls
        self.assertEqual(args, ["sound-save", "club-id"])
        self.assertEqual(len(data), 16000)
        argv = args_file.read_text().split()
        self.assertEqual(argv[:2], ["-t", "mp3"])
        self.assertEqual(argv[-6:], ["-t", "ul", argv[-4], "trim", "0", "601"])
        self.assertFalse(Path(argv[2]).exists())

    def test_upload_refusals(self):
        for name, filename, data in (
            ("../evil", "a.mp3", b"x"),
            ("Club", "a.mp3", b"x"),
            ("club", "a.ogg", b"x"),
            ("club", "a.mp3.sh", b"x"),
            ("club", "a.wav", b""),
            ("club", "a.wav", b"x" * (sounds.MAX_UPLOAD_BYTES + 1)),
        ):
            with self.assertRaises(SoundError, msg=(name, filename)):
                asyncio.run(self.sounds.upload(name, filename, data))
        with mock.patch.dict(os.environ, {"SOX_BYTES": str(8000 * 601)}), self.assertRaises(SoundError):
            asyncio.run(self.sounds.upload("long", "long.wav", b"RIFF"))
        self.sounds.sox = script(Path(self.tmp.name) / "bad-sox", "#!/bin/sh\necho \"sox FAIL formats: /tmp/x\" >&2\nexit 2\n")
        with self.assertLogs("smelite", "WARNING"), self.assertRaisesRegex(SoundError, "^that file could not be read as MP3 audio$"):
            asyncio.run(self.sounds.upload("junk", "junk.mp3", b"junk"))
        self.assertEqual(self.helper_calls, [])


@web.middleware
async def token_user(request, handler):
    token = request.headers.get("X-Token")
    request["user"] = request.app["engine"].auth.user_for_token(token) if token else None
    try:
        return await handler(request)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)


class UploadEndpointTests(AioHTTPTestCase):
    async def get_application(self):
        self.tmp = tempfile.TemporaryDirectory()
        auth = AuthStore(Path(self.tmp.name) / "smelite.db")
        auth.create_user("ada", "long-password", "admin")
        auth.create_user("op", "long-password", "operator")
        self.recorded = []
        settings = SimpleNamespace(sounds_dir=Path(self.tmp.name), voices_dir=Path(self.tmp.name),
                                   piper="/bin/false", sox="/bin/false", tts_max_chars=50)
        self.engine = SimpleNamespace(
            auth=auth,
            sounds=Sounds(settings, None, demo=True),
            record=lambda user, action, **fields: self.recorded.append((action, fields.get("target"), fields.get("detail"))),
        )
        self.ada = auth.create_session(auth.get_user("ada"))
        self.op = auth.create_session(auth.get_user("op"))
        app = web.Application(middlewares=[token_user])
        app["engine"] = self.engine
        app.router.add_post("/api/sounds/upload", sounds_upload)
        return app

    async def asyncTearDown(self):
        await super().asyncTearDown()
        self.engine.auth.close()
        self.tmp.cleanup()

    async def upload(self, token, name, filename, data):
        form = FormData()
        form.add_field("name", name)
        form.add_field("file", data, filename=filename, content_type="audio/mpeg")
        return await self.client.post("/api/sounds/upload", headers={"X-Token": token}, data=form)

    async def test_admins_upload_and_it_is_logged(self):
        self.assertEqual((await self.upload(self.op, "net", "net.mp3", b"x" * 32000)).status, 403)
        response = await self.upload(self.ada, "Net", "net.mp3", b"x" * 32000)
        self.assertEqual(response.status, 200)
        body = await response.json()
        self.assertEqual(body["saved"]["name"], "announcements/net")
        self.assertIn("announcements/net", [item["name"] for item in body["library"]])
        self.assertEqual(self.recorded, [("upload sound", "announcements/net", "net.mp3")])
        self.assertEqual((await self.upload(self.ada, "net", "net.exe", b"x")).status, 400)


class PoliteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        settings = Settings(demo=True, data_dir=data)
        self.auth = AuthStore(data / "smelite.db")
        directory = NodeDirectory(data / "astdb.txt", data / "asdb.txt", "", 15)
        self.recorded = []
        self.engine = Engine(settings, self.auth, directory)
        self.engine.record = lambda user, action, **fields: self.recorded.append((action, fields.get("detail")))
        self.played = []

        async def run_command(command, local):
            self.played.append(command)
            return "OK"

        self.engine.run_command = run_command
        patches = [mock.patch.object(monitor, "POLITE_POLL", 0.01), mock.patch.object(monitor, "POLITE_CLEAR", 0.03)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    async def asyncTearDown(self):
        await self.engine.close()
        self.auth.close()
        self.tmp.cleanup()

    async def test_clear_node_plays_now(self):
        self.assertTrue(await self.engine.play_sound("1998", "announcements/net", "local", "ada", polite=True))
        self.assertEqual(self.played, ["rpt localplay 1998 announcements/net"])

    async def test_busy_node_waits_then_plays(self):
        self.engine.hub.snapshots["1998"] = {"rx_keyed": True}
        self.assertFalse(await self.engine.play_sound("1998", "announcements/net", "links", "ada", polite=True))
        with self.assertRaises(ValueError):
            await self.engine.play_sound("1998", "announcements/net", "links", "ada", polite=True)
        await asyncio.sleep(0.1)
        self.assertEqual(self.played, [])
        self.engine.hub.snapshots["1998"] = {"rx_keyed": False}
        await asyncio.sleep(0.2)
        self.assertEqual(self.played, ["rpt playback 1998 announcements/net"])
        self.assertEqual(self.engine._polite, {})

    async def test_priority_ignores_a_busy_node_and_polite_gives_up(self):
        self.engine.hub.snapshots["1998"] = {"remote_keyed": True}
        self.assertTrue(await self.engine.play_sound("1998", "announcements/net", "local", "ada"))
        with mock.patch.object(monitor, "POLITE_WAIT", 0.1):
            await self.engine.play_sound("1998", "announcements/net", "local", "ada", polite=True, label="net")
            await asyncio.sleep(0.3)
        self.assertEqual(len(self.played), 1)
        self.assertEqual(self.recorded, [("sound not played", "the node stayed busy for 5 minutes")])


class HelperSoundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.helper = load_helper()
        self.helper.ANNOUNCEMENTS = Path(self.tmp.name) / "announcements"

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_and_delete(self):
        with mock.patch("sys.stdin", mock.Mock(buffer=io.BytesIO(b"\x7f" * 100))), \
                mock.patch("sys.stdout", io.StringIO()):
            self.helper.sound_save("net")
        target = self.helper.ANNOUNCEMENTS / "net.ulaw"
        self.assertEqual(target.read_bytes(), b"\x7f" * 100)
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)
        with mock.patch("sys.stdout", io.StringIO()):
            self.helper.sound_delete("net")
        self.assertFalse(target.exists())
        for bad in ("../x", "Net", ""):
            with self.assertRaises(SystemExit), mock.patch("sys.stderr", io.StringIO()):
                self.helper.sound_delete(bad)


if __name__ == "__main__":
    unittest.main()
