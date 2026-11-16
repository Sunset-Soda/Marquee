"""Automatic subtitle sync: line a subtitle file up with the speech in the video.

How it works
  1. ffmpeg reads the audio, keeps the speech band (300 Hz to 3.4 kHz) and reports its loudness every 50 ms.
     Loud enough means somebody is probably talking: one bit per 50 ms, "speech" or "not".
  2. The subtitle gives the same kind of signal: a bit is set while a cue is on screen.
  3. Each signal is held in one big Python integer, so sliding one over the other and counting agreeing bits is
     a shift, an AND and a popcount. That makes trying every offset within two minutes either way take a moment.
  4. Subtitles made for a different frame rate drift further out of step as the film goes on. The common frame
     rate mismatches (23.976, 24, 25 fps) are tried as well, and one is used only when it clearly fits better.

The result is `(delay, scale)`: a cue at time c should be shown at `c * scale + delay`. No numpy needed.
"""
from __future__ import annotations

import math
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

from .subtitles import Cue

STEP = 0.05                      # seconds per bit
MAX_SHIFT = 120.0                # how far out of step a subtitle may be and still be found
SCALES = (1.0, 1.001001, 0.999001, 1.042709, 0.959041, 1.041667, 0.96)   # 23.976/24/25 fps in every pairing
MIN_CONFIDENCE = 5.0             # how far above the other offsets the best one must stand, in standard deviations
IN_SYNC = 0.15                   # seconds: closer than this is left alone

def _log(msg: str) -> None:
    print("marquee: sync: " + msg, file=sys.stderr, flush=True)


_LEVEL = re.compile(r"RMS_level=(-?[\d.]+|-inf|inf|nan)")


@dataclass
class Result:
    delay: float                 # seconds, positive shows subtitles later
    scale: float                 # 1.0 unless the frame rate differs
    confidence: float
    changed: bool                # False when the subtitle was already in step


def available() -> bool:
    return shutil.which("ffmpeg") is not None


# ---------------------------------------------------------------- the speech signal
def _has_centre(video: str) -> bool:
    """Surround soundtracks keep the dialogue in the centre channel, away from the music and the laughter."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channel_layout,channels",
                              "-of", "default=nw=1", video], capture_output=True, text=True, timeout=30, errors="replace").stdout
    except (OSError, subprocess.SubprocessError):
        return False
    m = re.search(r"channels=(\d+)", out)
    lay = re.search(r"channel_layout=(\S+)", out)
    return bool(m and int(m.group(1)) >= 3 and (not lay or "unknown" not in lay.group(1)) and (not lay or "FC" in lay.group(1) or lay.group(1).startswith(("3.", "4.", "5.", "6.", "7."))))


def speech_levels(video: str, timeout: float = 900.0, centre: bool | None = None) -> list[float]:
    """Loudness of the speech band in dB, one value per 50 ms. [] when ffmpeg could not read the audio."""
    if centre is None:
        centre = _has_centre(video)
    chain = "pan=mono|c0=FC," if centre else ""
    af = (chain + "aresample=8000,highpass=f=300,lowpass=f=3400,asetnsamples=n=400:p=0,"
          "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-")
    try:
        run = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", video, "-vn", "-sn", "-dn"] + ([] if centre else ["-ac", "1"])
                             + ["-af", af, "-f", "null", "-"], capture_output=True, text=True, timeout=timeout, errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        _log(f"ffmpeg did not run: {e}")
        return []
    out = run.stdout
    if centre and "RMS_level" not in out:                 # the layout had no centre channel after all: use everything
        return speech_levels(video, timeout, centre=False)
    if "RMS_level" not in out:
        _log("ffmpeg gave no loudness values: " + (run.stderr or "").strip()[-300:])
    levels = []
    for m in _LEVEL.finditer(out):
        try:
            v = float(m.group(1))
        except ValueError:
            v = -90.0
        levels.append(-90.0 if math.isnan(v) or v < -90 else min(v, 0.0))
    return levels


def _popcount(x: int) -> int:
    return x.bit_count()


def speech_bits(levels: list[float], close: int = 5, cover: float = 0.0) -> int:
    """Turn loudness into speech / not speech, one bit per step.

    A sitcom has laughter and music as loud as the talking, so a fixed loudness line calls most of it speech. The
    subtitle says how much of the time people really talk (`cover`, the share of the video the cues are on screen),
    so when it is given only that share of the loudest steps counts as speech. Without it the threshold sits
    between the quiet floor and the loud parts of the file."""
    if len(levels) < 200:
        return 0
    ordered = sorted(levels)
    lo, hi = ordered[int(len(ordered) * 0.25)], ordered[int(len(ordered) * 0.92)]
    if hi - lo < 6:                                   # no real difference between loud and quiet: nothing to follow
        return 0
    thr = lo + 0.45 * (hi - lo)
    if cover > 0:
        keep = max(0.15, min(0.6, cover * 0.95))
        thr = max(thr, ordered[int(len(ordered) * (1 - keep))])
    bits = 0
    for i, v in enumerate(levels):
        if v >= thr:
            bits |= 1 << i
    # speech has gaps between words that a subtitle does not: close the short ones
    wide = bits
    for k in range(1, close + 1):
        wide |= (bits << k) | (bits >> k)
    return wide


def cue_bits(cues: list[Cue], scale: float = 1.0, size: int = 0) -> int:
    bits = 0
    for c in cues:
        a, b = int(c.start * scale / STEP), int(math.ceil(c.end * scale / STEP))
        if b > a and a >= 0:
            bits |= ((1 << (b - a)) - 1) << a
    return bits & ((1 << size) - 1) if size else bits


# ---------------------------------------------------------------- finding the offset
def _scores(cues: list[Cue], scale: float, speech: int, size: int, around: int | None = None) -> list[tuple[int, float]]:
    """Agreement between the subtitle (moved by each shift) and the speech, for every shift in range."""
    sub = cue_bits(cues, scale, size)
    if not sub:
        return []
    density = _popcount(speech) / size
    top = int(MAX_SHIFT / STEP)
    rng = range(-top, top + 1) if around is None else range(around - 8, around + 9)
    out = []
    for k in rng:
        moved = (sub << k) if k >= 0 else (sub >> -k)
        moved &= (1 << size) - 1
        n = _popcount(moved)
        if n < 40:
            continue
        out.append((k, _popcount(moved & speech) - n * density))
    return out


def _edges(cues: list[Cue], scale: float, raw: int, size: int, k: int) -> int:
    """Settle the shift to the step: where cues begin and end should be where speech begins and ends."""
    mask = (1 << size) - 1
    on, off = raw & ~(raw << 1), raw & ~(raw >> 1)
    on_w, off_w = on, off
    for j in range(1, 3):
        on_w |= (on << j) | (on >> j)
        off_w |= (off << j) | (off >> j)
    starts = ends = 0
    for c in cues:
        a, b = int(c.start * scale / STEP), int(c.end * scale / STEP)
        if a >= 0:
            starts |= 1 << a
            ends |= 1 << max(0, b)
    best = []
    for kk in range(k - 24, k + 25):
        sh = (lambda x: (x << kk) & mask) if kk >= 0 else (lambda x: x >> -kk)
        best.append((_popcount(sh(starts) & on_w), kk))     # laughter and music trail off, so only the starts are trusted
    top = max(v for v, _kk in best)
    prof = {kk: v for v, kk in best}
    _log("cue starts that land on a speech start, by shift from the match: " + ", ".join(
        f"{(kk - k) * STEP:+.1f}s {prof[kk] * 100 // max(1, len(cues))}%" for kk in range(k - 20, k + 21, 5) if kk in prof))
    if top < 20:
        return k
    near = [kk for v, kk in best if v >= top * 0.97]              # the best few: take the middle of them
    return round(sum(near) / len(near))


def _confidence(scores: list[tuple[int, float]], best: float) -> float:
    vals = [s for _k, s in scores]
    if len(vals) < 30:
        return 0.0
    mean = sum(vals) / len(vals)
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals)) or 1e-9
    return (best - mean) / sd


def fit(cues: list[Cue], levels: list[float]) -> Result | None:
    """Find the delay and scale that make `cues` follow the speech in `levels`. None when nothing fits convincingly."""
    size = len(levels)
    cover = _popcount(cue_bits(cues, 1.0, size)) / size if size else 0.0
    speech = speech_bits(levels, 5, cover)
    if not speech or len(cues) < 20:
        _log(f"nothing to compare: {len(levels)} loudness values, {_popcount(speech) if speech else 0} speech steps, {len(cues)} cues")
        return None
    tried = []
    for scale in SCALES:
        scores = _scores(cues, scale, speech, size)
        if not scores:
            continue
        k, best = max(scores, key=lambda e: e[1])
        tried.append((best, scale, k, _confidence(scores, best)))
    if not tried:
        _log("no usable shifts")
        return None
    _log(f"{len(levels) * STEP / 60:.1f} min of audio, speech {_popcount(speech) / len(levels):.0%} of it, {len(cues)} cues; "
         "confidence by frame rate: " + ", ".join(f"x{t[1]:.4f} {t[3]:.1f} (shift {t[2] * STEP:+.1f}s)" for t in tried))
    base = next((t for t in tried if t[1] == 1.0), None)
    top = max(tried)
    # a different frame rate has to win clearly, because it also fits a little better by chance
    pick = top if base is None or top[0] > base[0] * 1.2 + 5 else base
    _best, scale, k, conf = pick
    if scale != 1.0:
        # 25 / 24 and 25 / 23.976 are a tenth of a percent apart: look in between, then snap to a real frame-rate ratio
        trial = []
        for j in range(-5, 6):
            sc = scale + j * 0.0004
            scores = _scores(cues, sc, speech, size)
            if scores:
                kk, v = max(scores, key=lambda e: e[1])
                trial.append((v, sc, kk))
        if trial:
            top = max(v for v, _sc, _kk in trial)
            close = [t for t in trial if t[0] >= top * 0.98]          # within noise of the best: a real ratio wins
            _v, scale, k = min(close, key=lambda t: min(abs(x - t[1]) for x in SCALES[1:]))
            near = min(SCALES, key=lambda x: abs(x - scale))
            if abs(near - scale) <= 0.0006:
                scale = near
                k = max(_scores(cues, scale, speech, size), key=lambda e: e[1])[0]
    if conf < MIN_CONFIDENCE:
        _log(f"best match is not convincing ({conf:.1f}, needs {MIN_CONFIDENCE})")
        return None
    # The search used speech with its gaps closed, so several neighbouring shifts tie. Settle it on the plain
    # speech signal, taking the middle of the best ones.
    k = _edges(cues, scale, speech_bits(levels, 0, cover), size, k)
    delay = round(k * STEP, 2)
    return Result(delay, scale, round(conf, 1), abs(delay) > IN_SYNC or scale != 1.0)


def sync(video: str, cues: list[Cue]) -> Result | None:
    """Read the video's audio and fit the cues to it. None when ffmpeg is missing or no confident match exists."""
    if not available():
        return None
    return fit(cues, speech_levels(video))


# ---------------------------------------------------------------- using the result
def wanted(item, track_id: str, prefs: dict, embedded: bool = False) -> bool:
    """Whether to sync this track on its own: once per track, never for tracks inside the video (those are already
    timed to it), and not when switched off in the settings."""
    return bool(prefs.get("sub_autosync", True)) and not embedded and item.sub_synced != track_id and item.exists


def apply(lib, item, track_id: str, res: Result | None) -> None:
    """Remember the timing found for this video. A subtitle that was already in step is only marked as checked."""
    item.sub_synced = track_id
    if res and res.changed:
        item.sub_delay, item.sub_scale = res.delay, res.scale
    lib.save()


def describe(res: Result | None) -> str:
    if res is None:
        return "Couldn’t line these subtitles up with the speech. Use Show Earlier / Later to adjust them by hand."
    if not res.changed:
        return "Subtitles already line up with the speech."
    msg = f"Subtitles synced: {res.delay:+.1f} s"
    if res.scale != 1.0:
        msg += f", stretched {abs(res.scale - 1) * 100:.1f}% for the frame rate"
    return msg
