"""Announcements: speak typed text with piper, play saved sound files.

Speech is rendered as the service user into a temp folder Asterisk can read,
then played with ``rpt localplay`` (this node only) or ``rpt playback``
(this node and every connected link). Saving an announcement goes through the
root helper because the sounds folder belongs to root and asterisk.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Awaitable, Callable

SOUND_EXTS = {".ulaw": 8000, ".ul": 8000, ".alaw": 8000, ".gsm": 1650, ".sln": 16000, ".wav": None, ".g722": 8000}
SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,120}$")
ANNOUNCE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
ANNOUNCE_DIR = "announcements"
SCOPES = {"local": "localplay", "links": "playback"}
TEMP_DIR = Path(tempfile.gettempdir()) / "smelite-tts"


class SoundError(ValueError):
    """A sound request was refused."""


def clean_text(text: str, limit: int) -> str:
    text = "".join(ch if unicodedata.category(ch)[0] != "C" else " " for ch in text)
    text = " ".join(text.split())
    if not text:
        raise SoundError("type something to say")
    if len(text) > limit:
        raise SoundError(f"keep it under {limit} characters")
    return text


class Sounds:
    def __init__(self, settings, helper: Callable[[list[str], bytes | None], Awaitable[str]], demo: bool = False):
        self.dir = settings.sounds_dir
        self.voices_dir = settings.voices_dir
        self.piper = settings.piper
        self.sox = settings.sox
        self.max_chars = settings.tts_max_chars
        self.helper = helper
        self.demo = demo
        self._lock = asyncio.Lock()
        self._demo_saved: dict[str, float] = {"announcements/net-tonight": 6.2}

    def tts_ready(self) -> bool:
        if self.demo:
            return True
        return os.access(self.piper, os.X_OK) and os.access(self.sox, os.X_OK) and bool(self.voices())

    def voices(self) -> list[str]:
        if self.demo:
            return ["en_US-amy-low", "en_US-ryan-low"]
        try:
            models = sorted(self.voices_dir.glob("*.onnx"))
        except OSError:
            return []
        return [model.stem for model in models if model.with_name(model.name + ".json").exists()]

    def library(self) -> list[dict]:
        if self.demo:
            found = {"reboot": 2.3, "ip-address": 2.1, **self._demo_saved}
            return [self._entry(name, seconds) for name, seconds in sorted(found.items())]
        entries: dict[str, dict] = {}
        for folder in (self.dir, self.dir / ANNOUNCE_DIR):
            try:
                files = sorted(path for path in folder.iterdir() if path.is_file())
            except OSError:
                continue
            for path in files:
                rate = SOUND_EXTS.get(path.suffix.lower(), 0)
                if rate == 0:
                    continue
                name = path.relative_to(self.dir).with_suffix("").as_posix()
                if not SAFE_NAME.match(name) or ".." in name or name in entries:
                    continue
                seconds = round(path.stat().st_size / rate, 1) if rate else None
                entries[name] = self._entry(name, seconds)
        return list(entries.values())

    @staticmethod
    def _entry(name: str, seconds: float | None) -> dict:
        return {"name": name, "seconds": seconds, "saved": name.startswith(ANNOUNCE_DIR + "/")}

    def resolve(self, name: str) -> str:
        if not any(entry["name"] == name for entry in self.library()):
            raise SoundError("that sound is not in the library")
        return str(self.dir / name)

    async def speak(self, text: str, voice: str) -> tuple[str, float]:
        """Render ``text`` and return (path without extension, seconds)."""
        text = clean_text(text, self.max_chars)
        if voice not in self.voices():
            raise SoundError("pick one of the installed voices")
        if self.demo:
            return str(TEMP_DIR / "demo"), round(len(text) / 14, 1)
        if self._lock.locked():
            raise SoundError("another announcement is being prepared; try again in a moment")
        async with self._lock:
            TEMP_DIR.mkdir(mode=0o755, exist_ok=True)
            os.chmod(TEMP_DIR, 0o755)
            fd, base = tempfile.mkstemp(prefix="tts-", dir=TEMP_DIR)
            os.close(fd)
            os.unlink(base)
            wav, ulaw = base + ".wav", base + ".ul"
            try:
                model = self.voices_dir / f"{voice}.onnx"
                await self._run([self.piper, "--model", str(model), "--output_file", wav], text.encode())
                await self._run([self.sox, wav, "-r", "8000", "-c", "1", "-t", "ul", ulaw])
                os.chmod(ulaw, 0o644)
            except BaseException:
                if os.path.exists(ulaw):
                    os.unlink(ulaw)
                raise
            finally:
                if os.path.exists(wav):
                    os.unlink(wav)
        seconds = round(os.path.getsize(ulaw) / 8000, 1)
        asyncio.get_running_loop().call_later(seconds + 120, _remove, ulaw)
        return base, seconds

    async def save(self, name: str, text: str, voice: str) -> dict:
        if not ANNOUNCE_NAME.match(name):
            raise SoundError("use lowercase letters, numbers, - and _ for the name (up to 40)")
        base, seconds = await self.speak(text, voice)
        if self.demo:
            self._demo_saved[f"{ANNOUNCE_DIR}/{name}"] = seconds
        else:
            try:
                data = Path(base + ".ul").read_bytes()
            finally:
                _remove(base + ".ul")
            await self.helper(["sound-save", name], data)
        return self._entry(f"{ANNOUNCE_DIR}/{name}", seconds)

    async def delete(self, name: str) -> None:
        short = name.removeprefix(ANNOUNCE_DIR + "/")
        if not ANNOUNCE_NAME.match(short):
            raise SoundError("only saved announcements can be deleted")
        if self.demo:
            self._demo_saved.pop(f"{ANNOUNCE_DIR}/{short}", None)
            return
        await self.helper(["sound-delete", short], None)

    async def _run(self, argv: list[str], stdin: bytes | None = None) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise SoundError(f"{Path(argv[0]).name} could not start: {exc.strerror}") from exc
        try:
            _, err = await asyncio.wait_for(proc.communicate(stdin or b""), timeout=90)
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise SoundError(f"{Path(argv[0]).name} took too long") from exc
        if proc.returncode != 0:
            tail = err.decode(errors="replace").strip().splitlines()[-1:] or ["failed"]
            raise SoundError(f"{Path(argv[0]).name}: {tail[0]}")


def _remove(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
