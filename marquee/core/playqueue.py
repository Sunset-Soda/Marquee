"""Play order for music: shuffle and repeat.

`PlayQueue` holds the tracks that will play (an album, then the artist's later albums), the order they play in, and
where it is. Shuffle keeps the current track and mixes everything else; repeat is off, all, or one.
"""
from __future__ import annotations

import random

REPEAT_MODES = ("off", "all", "one")


class PlayQueue:
    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.ids: list[str] = []         # in album order
        self.order: list[str] = []       # in play order
        self.pos = -1
        self.shuffle = False
        self.repeat = "off"

    @property
    def current(self) -> str:
        return self.order[self.pos] if 0 <= self.pos < len(self.order) else ""

    def start(self, ids: list[str], current: str) -> None:
        self.ids = list(ids) if current in ids else [current]
        self._arrange(current)

    def _arrange(self, current: str) -> None:
        if self.shuffle:
            rest = [i for i in self.ids if i != current]
            self.rng.shuffle(rest)
            self.order = [current] + rest
            self.pos = 0
        else:
            self.order = list(self.ids)
            self.pos = self.order.index(current) if current in self.order else 0

    def set_shuffle(self, on: bool) -> None:
        cur = self.current
        self.shuffle = bool(on)
        if cur:
            self._arrange(cur)

    def set_repeat(self, mode: str) -> None:
        self.repeat = mode if mode in REPEAT_MODES else "off"

    def cycle_repeat(self) -> str:
        self.repeat = REPEAT_MODES[(REPEAT_MODES.index(self.repeat) + 1) % len(REPEAT_MODES)]
        return self.repeat

    def play(self, track_id: str) -> None:
        """Note that this track is now playing (picked from the queue or the library)."""
        if track_id in self.order:
            self.pos = self.order.index(track_id)

    def next(self, auto: bool = False) -> str:
        """The track after this one. `auto` is True when the track ended by itself: repeat-one then plays it again,
        while a press of Next always moves on. '' at the end of the queue unless repeat is on."""
        if not self.order:
            return ""
        if auto and self.repeat == "one":
            return self.current
        if self.pos + 1 < len(self.order):
            self.pos += 1
            return self.current
        if self.repeat == "off":
            return ""
        # repeat all, or Next pressed on the last track under repeat one: go round again
        start = self._fresh_start(self.current) if self.shuffle else self.ids[0]
        self._arrange(start)
        return self.current

    def _fresh_start(self, avoid: str) -> str:
        choices = [i for i in self.ids if i != avoid] or self.ids
        return self.rng.choice(choices)

    def previous(self) -> str:
        """The track before this one, or '' when there is none (the caller then restarts this one)."""
        if self.pos > 0:
            self.pos -= 1
            return self.current
        if self.repeat != "off" and len(self.order) > 1:
            self.pos = len(self.order) - 1
            return self.current
        return ""
