"""Three short synthesised tracks so the player works before any files are added."""
from __future__ import annotations

import math
import struct
from pathlib import Path

from .library import data_dir

TRACKS = [("Harbor Lights", "Marquee Samples"), ("Night Bus", "Marquee Samples"), ("Paper Lanterns", "Marquee Samples")]
SECONDS = 20
RATE = 22050


def synth_wav(n: int) -> bytes:
    sc = [[0, 2, 4, 7, 9], [0, 3, 5, 7, 10], [0, 2, 3, 7, 8]][n]
    base = [220.0, 196.0, 174.6][n]
    step = 60 / [96, 76, 112][n] / 2
    total = RATE * SECONDS
    data = [0.0] * total
    seed = (n + 1) * 9301

    def rnd() -> float:
        nonlocal seed
        seed = (seed * 49297 + 233280) % 233280
        return seed / 233280

    for k in range(int(SECONDS / step)):
        f = base * (2 if rnd() < 0.3 else 1) * 2 ** (sc[int(rnd() * len(sc))] / 12)
        t0 = int(k * step * RATE)
        for i in range(int(step * 1.6 * RATE)):
            if t0 + i >= total:
                break
            t = i / RATE
            env = math.exp(-t * 4.5) * min(1.0, i / 200)
            data[t0 + i] += env * (math.sin(math.tau * f * t) * 0.6 + math.sin(2 * math.tau * f * t) * 0.2
                                    + math.sin(3 * math.tau * f * t) * 0.08) * 0.5
        if k % 4 == 0:
            for j in range(int(step * 4 * RATE)):
                if t0 + j >= total:
                    break
                bt = j / RATE
                data[t0 + j] += math.exp(-bt * 1.1) * min(1.0, j / 300) * math.sin(math.tau * base / 2 * bt) * 0.32

    fade = int(RATE * 1.5)
    pcm = bytearray()
    for z, v in enumerate(data):
        v *= (total - z) / fade if z > total - fade else 1.0
        pcm += struct.pack("<h", int(max(-1.0, min(1.0, v * 0.9)) * 32767))
    header = (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16)
              + b"data" + struct.pack("<I", len(pcm)))
    return header + bytes(pcm)


def ensure_file(n: int) -> Path:
    folder = data_dir() / "samples"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"sample-{n + 1}.wav"
    if not path.exists():
        path.write_bytes(synth_wav(n))
    return path
