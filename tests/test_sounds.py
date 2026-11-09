import asyncio
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from smelite import sounds
from smelite.sounds import SoundError, Sounds, clean_text
from test_admin import load_helper

FAKE_PIPER = """#!/bin/sh
while [ "$1" ]; do [ "$1" = --output_file ] && out=$2; shift; done
cat > "$out"
"""
FAKE_SOX = """#!/bin/sh
head -c 16000 /dev/zero > "$8"
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
