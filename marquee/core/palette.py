"""Colours for the Now Playing screen, taken from the album art. Pure Python, shared by the frontends.

From a cover we find the dominant colour and a contrasting accent. The backdrop is a dark, strong version of them.
The visualiser bars take the accent hue but are lightened until they hold at least 6.5:1 contrast against the
backdrop behind them, so they always stand out and still belong to the picture.
"""
from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass
from typing import Iterable

MIN_CONTRAST = 6.5


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def rgb_to_hsl(r: float, g: float, b: float) -> tuple[float, float, float]:
    """r, g, b are 0..255. Returns hue in degrees, saturation and lightness in 0..1."""
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return h * 360, s, l


def hsl_to_rgb(h: float, s: float, l: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360, _clamp(l, 0, 1), _clamp(s, 0, 1))
    return round(r * 255), round(g * 255), round(b * 255)


def rel_lum(c: tuple[int, int, int]) -> float:
    def lin(v: int) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2])


def contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    x, y = rel_lum(a), rel_lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


def mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def hexcolor(c: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % c


@dataclass
class Palette:
    c1: str          # backdrop glow, top left
    c2: str          # backdrop glow, bottom right
    base: str        # backdrop base
    bar: str         # visualiser, tip
    bar2: str        # visualiser, foot
    contrast: float  # worst contrast of the two bar colours against the backdrop
    c3: str = ""     # two more glows, so a cover with several colours shows several
    c4: str = ""

    def as_dict(self) -> dict:
        return {"c1": self.c1, "c2": self.c2, "c3": self.c3 or self.c2, "c4": self.c4 or self.c1,
                "base": self.base, "bar": self.bar, "bar2": self.bar2}


def build(D: tuple[float, float], A: tuple[float, float], extra: tuple = ()) -> Palette:
    """D and A are (hue, saturation) of the dominant and the accent colour; extra holds up to two more.

    The backdrop is vivid, as on Apple Music and Spotify: the cover's own colours at a strong mid lightness, over a
    deeper base. The bars are lifted until they hold their contrast against it."""
    neutral_d, neutral_a = D[1] < 0.12, A[1] < 0.12
    sd = D[1] if neutral_d else _clamp(D[1], 0.45, 0.9)
    sa = A[1] if neutral_a else _clamp(A[1], 0.45, 0.9)
    ex = [(h, s if s < 0.12 else _clamp(s, 0.45, 0.9)) for h, s in extra[:2]]
    base = hsl_to_rgb(D[0], D[1] if neutral_d else _clamp(D[1] * 0.7, 0.2, 0.6), 0.15)

    def lift(h: float, sat: float, l0: float, under) -> tuple[int, int, int]:
        l = l0
        c = hsl_to_rgb(h, sat, l)
        while contrast(c, under) < MIN_CONTRAST and l < 0.97:
            l += 0.02
            c = hsl_to_rgb(h, sat, l)
        return c

    # Start vivid. Bright hues (yellow, orange) are darkened a step at a time until the bars can hold their contrast.
    glow = 0.40
    while True:
        c1 = hsl_to_rgb(D[0], sd, glow)
        c2 = hsl_to_rgb(A[0], sa, glow - 0.04)
        more = [hsl_to_rgb(h, s, glow - 0.06) for h, s in ex]
        while len(more) < 2:                # covers with fewer colours reuse the ones they have
            more.append(c2 if len(more) == 0 else c1)
        under = mix(base, mix(c1, c2, 0.5), 0.6)
        bar = lift(A[0], A[1] if neutral_a else _clamp(A[1], 0.55, 0.95), 0.72, under)
        bar2 = lift(D[0], D[1] if neutral_d else _clamp(D[1], 0.5, 0.9), 0.62, under)
        worst = min(contrast(bar, under), contrast(bar2, under))
        if worst >= MIN_CONTRAST or glow <= 0.22:
            break
        glow -= 0.02
    return Palette(hexcolor(c1), hexcolor(c2), hexcolor(base), hexcolor(bar), hexcolor(bar2), worst,
                   hexcolor(more[0]), hexcolor(more[1]))


def from_pixels(pixels: Iterable[tuple[int, int, int, int]]) -> Palette | None:
    """pixels are (r, g, b, a) from a small square crop of the cover. None when there is nothing to read."""
    buckets: dict[int, list[float]] = {}
    for r, g, b, a in pixels:
        if a < 200:
            continue
        k = (r >> 4) << 8 | (g >> 4) << 4 | (b >> 4)
        e = buckets.setdefault(k, [0, 0, 0, 0])
        e[0] += 1
        e[1] += r
        e[2] += g
        e[3] += b
    if not buckets:
        return None
    colours = []
    for n, r, g, b in buckets.values():
        h, s, l = rgb_to_hsl(r / n, g / n, b / n)
        colours.append((n * (0.25 + s) * (0.25 if l < 0.1 or l > 0.92 else 1.0), h, s))
    colours.sort(reverse=True)
    dom = colours[0]
    picked = []                                   # distinct, lively colours after the dominant one
    for score, h, s in colours[1:]:
        if s < 0.3 or score < dom[0] * 0.12:
            continue
        if all(min(abs(h - ph), 360 - abs(h - ph)) >= 35 for _, ph, _ in [dom] + picked):
            picked.append((score, h, s))
        if len(picked) == 3:
            break
    if picked:
        return build((dom[1], dom[2]), (picked[0][1], picked[0][2]), tuple((h, s) for _, h, s in picked[1:]))
    if dom[2] < 0.12:
        return build((dom[1], dom[2]), (dom[1], dom[2]))
    return build((dom[1], dom[2]), ((dom[1] + 28) % 360, max(dom[2], 0.45)))


def fallback(title: str) -> Palette:
    """For a track with no cover: a stable colour from its title."""
    h = 0
    for ch in title:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    hue = h % 360
    return build((hue, 0.5), ((hue + 45) % 360, 0.55))
