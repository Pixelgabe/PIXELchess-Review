"""Retro move sounds, generated locally and played through a system player.

No third-party audio dependency is needed: each sound is a small square-wave
chiptune tune rendered into a WAV file in the hyprchess cache directory and
played with a platform player (`afplay`, `paplay`, `pw-play`, `aplay`,
`ffplay`, `mpv`, `cvlc` or `play`, or `winsound` on Windows).  When no player
is available, or the app is not attached to a terminal, the sound is skipped.
"""

from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import sys
import wave
from functools import lru_cache
from pathlib import Path

SAMPLE_RATE = 22050
KIND_FILE = {"move": "move.wav", "capture": "capture.wav", "check": "check.wav", "mate": "mate.wav"}
_SOUND_SEQUENCES = {
    "move": ((520.0, 0.055), (388.0, 0.06)),
    "capture": ((196.0, 0.06), (146.0, 0.07)),
    "check": ((740.0, 0.055), (990.0, 0.07)),
    "mate": ((392.0, 0.08), (330.0, 0.08), (261.0, 0.14)),
}
_PLAYER_COMMANDS = (
    ("paplay", ()),
    ("pw-play", ()),
    ("aplay", ("-q",)),
    ("ffplay", ("-nodisp", "-autoexit", "-loglevel", "quiet")),
    ("mpv", ("--no-video", "--really-quiet", "--frames=1",)),
    ("cvlc", ("--play-and-exit", "--quiet",)),
    ("play", ("-nq",)),
    ("canberra-gtk-play", ("-f",)),
)


def _samples(kind: str) -> list[int]:
    """Render one retro square-wave sequence as 16-bit mono PCM samples."""
    samples: list[int] = []
    for frequency, seconds in _SOUND_SEQUENCES[kind]:
        count = int(SAMPLE_RATE * seconds)
        for i in range(count):
            t = i / SAMPLE_RATE
            attack = min(1.0, t / (0.002 / seconds)) if t < 0.002 else 1.0
            envelope = max(0.0, min(1.0, attack * math.exp(-t * 26.0)))
            level = 0.9 * (1.0 if int(t * frequency) % 2 == 0 else -1.0) * envelope
            value = max(-1.0, min(1.0, level))
            samples.append(int(round(value * 32767)))
    return samples


def _ensure_wav(kind: str) -> str | None:
    cache = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "hyprchess"
    path = cache / KIND_FILE[kind]
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(SAMPLE_RATE)
                handle.writeframes(b"".join(struct.pack("<h", sample) for sample in _samples(kind)))
        return str(path)
    except OSError:
        return None


@lru_cache(maxsize=4)
def _sound_path(kind: str) -> str | None:
    return _ensure_wav(kind)


@lru_cache(maxsize=1)
def _player_command() -> list[str] | None:
    if os.name == "nt":
        return None
    for command, flags in _PLAYER_COMMANDS:
        if shutil.which(command):
            return [command, *flags]
    return None


def play_sound(kind: str = "move") -> None:
    """Play one generated retro move sound; silently no-op when not audible or playing."""
    if os.environ.get("HYPRCHESS_SOUND", "1").strip().lower() in ("0", "false", "no", "off"):
        return
    if not sys.stdout.isatty():
        return
    path = _sound_path(kind)
    if path is None:
        return
    if os.name == "nt":
        try:
            import winsound

            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            return
        except (OSError, RuntimeError):
            return
    command = _player_command()
    if command is None:
        return
    try:
        subprocess.Popen(
            [*command, str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        return
