"""Audio levels for the visualiser, worked out from the file itself rather than from the sound card.

ffmpeg decodes the track once to 16 kHz mono. A background thread turns it into 48 log-spaced bands, 30 times a
second, starting wherever you are playing. The screen asks `bands(t)` for the frame at the playback position, so the
bars line up with what you hear whatever audio backend plays the sound. Without ffmpeg there is nothing to read,
`available()` is False and the screen shows resting bars.
"""
from __future__ import annotations

import math
import shutil
import subprocess
import threading
from array import array

RATE = 16000
FPS = 30
HOP = RATE // FPS
WIN = 1024
BANDS = 48
LOW_HZ, HIGH_HZ = 40.0, 7000.0

try:                                   # much faster when it is there, but never required
    import numpy as _np
except ImportError:                    # pragma: no cover - depends on the machine
    _np = None


def available() -> bool:
    return shutil.which("ffmpeg") is not None


def decode(path: str, timeout: float = 120.0) -> array | None:
    exe = shutil.which("ffmpeg")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-v", "error", "-nostdin", "-i", path, "-vn", "-ac", "1", "-ar", str(RATE),
                            "-f", "s16le", "-"], capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    pcm = array("h")
    pcm.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
    return pcm or None


def _edges() -> list[tuple[int, int]]:
    bin_hz = RATE / WIN
    out = []
    for i in range(BANDS):
        f0 = LOW_HZ * (HIGH_HZ / LOW_HZ) ** (i / BANDS)
        f1 = LOW_HZ * (HIGH_HZ / LOW_HZ) ** ((i + 1) / BANDS)
        lo = int(f0 / bin_hz)
        out.append((lo, max(lo + 1, math.ceil(f1 / bin_hz))))
    return out


_EDGES = _edges()
_HANN = [0.5 - 0.5 * math.cos(2 * math.pi * n / (WIN - 1)) for n in range(WIN)]


def _fft_mags(frame: list[float]) -> list[float]:
    """Magnitudes of bins 0..WIN/2 for one windowed frame."""
    if _np is not None:
        return list(_np.abs(_np.fft.rfft(_np.asarray(frame) * _np.asarray(_HANN))))
    n = WIN
    x = [complex(frame[i] * _HANN[i]) for i in _REV]
    size = 2
    while size <= n:
        half = size // 2
        step = n // size
        for start in range(0, n, size):
            k = 0
            for j in range(start, start + half):
                t = x[j + half] * _TW[k]
                u = x[j]
                x[j] = u + t
                x[j + half] = u - t
                k += step
        size *= 2
    return [abs(c) for c in x[: n // 2 + 1]]


def _bit_reverse(n: int) -> list[int]:
    bits = n.bit_length() - 1
    return [int(format(i, f"0{bits}b")[::-1], 2) for i in range(n)]


_REV = _bit_reverse(WIN)
_TW = [complex(math.cos(-2 * math.pi * k / WIN), math.sin(-2 * math.pi * k / WIN)) for k in range(WIN // 2)]


def frame_bands(samples, gain_db: float = 0.0) -> list[float]:
    """48 values in 0..1 for WIN samples (floats scaled to -1..1). gain_db lifts quiet tracks and tames loud ones."""
    mags = _fft_mags(list(samples))
    out = []
    for i, (lo, hi) in enumerate(_EDGES):
        m = max(mags[lo:hi]) / (WIN / 4)                         # a full-scale sine peaks at 1.0 under a Hann window
        db = 20 * math.log10(m + 1e-9) + gain_db
        v = (db + 66.0) / 50.0                                    # -66 dB is silent, -16 dB is full height
        out.append(min(1.0, max(0.0, v * (1 + 0.3 * i / BANDS))))
    return out


def track_gain(pcm, target_db: float = -20.0) -> float:
    """How many dB to lift or lower a track so its bars use the same height range whether the master is quiet or
    loud. Measured from the loud passages (the 90th percentile of short-window RMS), limited to -12..+18 dB."""
    n = len(pcm)
    if n < RATE:
        return 0.0
    step = RATE // 4
    levels = []
    for i in range(0, n - step, step * 2):                      # every other quarter second is plenty
        seg = pcm[i:i + step]
        levels.append(math.sqrt(sum(s * s for s in seg) / len(seg)) / 32768)
    levels = sorted(l for l in levels if l > 1e-4)
    if not levels:
        return 0.0
    ref = levels[int(len(levels) * 0.9)]
    return max(-12.0, min(18.0, target_db - 20 * math.log10(ref)))


class Analyzer:
    """One track's levels. Create it, call start(), then ask bands(seconds) as often as you like."""

    def __init__(self, path: str):
        self.path = path
        self.pcm: array | None = None
        self.frames: list[list[float] | None] = []
        self.failed = False
        self.gain_db = 0.0
        self._cursor = 0
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._smooth = [0.0] * BANDS
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "Analyzer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    @property
    def ready(self) -> bool:
        return self.pcm is not None

    def _run(self) -> None:
        pcm = decode(self.path)
        if pcm is None or self._stop.is_set():
            self.failed = True
            return
        self.frames = [None] * (max(1, (len(pcm) - WIN) // HOP + 1))
        self.pcm = pcm
        self.gain_db = track_gain(pcm)
        done = 0
        while not self._stop.is_set() and done < len(self.frames):
            with self._lock:
                i = self._cursor
            while i < len(self.frames) and self.frames[i] is not None:
                i += 1
            if i >= len(self.frames):                             # everything ahead is done: fill any gap behind
                i = next((k for k, f in enumerate(self.frames) if f is None), len(self.frames))
                if i >= len(self.frames):
                    break
            seg = self.pcm[i * HOP: i * HOP + WIN]
            if len(seg) < WIN:
                seg = seg + array("h", [0] * (WIN - len(seg)))
            self.frames[i] = frame_bands([s / 32768 for s in seg], self.gain_db)
            done += 1
            with self._lock:
                self._cursor = i + 1
        self._wake.wait(0)

    def bands(self, seconds: float, smooth: bool = True) -> list[float]:
        """Band levels at this playback position, 0..1. Rises quickly and falls slowly, like a real analyser."""
        raw = [0.0] * BANDS
        if self.frames:
            i = max(0, min(len(self.frames) - 1, int(seconds * FPS)))
            f = self.frames[i]
            if f is None:
                with self._lock:
                    if not (i <= self._cursor < i + FPS):         # jumped somewhere new: compute from there next
                        self._cursor = i
            else:
                raw = f
        if not smooth:
            return raw
        self._smooth = [max(r, s * 0.84) if r < s else s + (r - s) * 0.7 for r, s in zip(raw, self._smooth)]
        return list(self._smooth)

    def rest(self) -> list[float]:
        """Fall back towards silence while paused."""
        self._smooth = [s * 0.8 for s in self._smooth]
        return list(self._smooth)

    def wave(self, seconds: float, n: int = 96) -> list[float]:
        """n samples of the waveform around this position, -1..1, for the scope style."""
        if self.pcm is None:
            return [0.0] * n
        start = int(seconds * RATE)
        step = max(1, 512 // n)
        out = []
        for k in range(n):
            j = start + k * step
            out.append(self.pcm[j] / 32768 if 0 <= j < len(self.pcm) else 0.0)
        peak = max(0.25, max(abs(v) for v in out))
        return [v / peak for v in out]
