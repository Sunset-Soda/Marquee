"""Where you are in the library, with Back, Forward and Home. Shared by the Qt and GTK frontends.

A view is a collection, a tab and the nodes opened so far (show -> season, or artist -> album). Moving to a different
view pushes the old one on the Back list. Going Back then Forward walks that list like a browser. The search text
is not part of history: it narrows the level you are on, and opening anything clears it.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .library import ALL


@dataclass(frozen=True)
class View:
    collection: str = ALL
    tab: str = "library"
    path: tuple = ()


class Navigator:
    LIMIT = 100

    def __init__(self, start: View | None = None) -> None:
        self.view = start or View()
        self.query = ""
        self._back: list[View] = []
        self._fwd: list[View] = []

    # ---- state ---------------------------------------------------------
    @property
    def collection(self) -> str:
        return self.view.collection

    @property
    def tab(self) -> str:
        return self.view.tab

    @property
    def path(self) -> tuple:
        return self.view.path

    @property
    def drilled(self) -> bool:
        return bool(self.view.path) and self.view.tab == "library"

    @property
    def can_back(self) -> bool:
        return bool(self._back)

    @property
    def can_forward(self) -> bool:
        return bool(self._fwd)

    @property
    def at_home(self) -> bool:
        return self.view == View(self.view.collection) and not self.query

    # ---- moving --------------------------------------------------------
    def _go(self, new: View) -> bool:
        if new == self.view:
            return False
        self._back.append(self.view)
        del self._back[: -self.LIMIT]
        self._fwd.clear()
        self.view = new
        self.query = ""
        return True

    def set_collection(self, cid: str) -> bool:
        return self._go(View(cid, "library", ()))

    def set_tab(self, tab: str) -> bool:
        return self._go(View(self.view.collection, tab, ()))

    def open_node(self, node_id: str) -> bool:
        """Open a show, season, artist or album tile. A season keeps its show above it, an album its artist."""
        kind, _, rest = node_id.partition(":")
        if kind in ("show", "artist"):
            path = (node_id,)
        elif kind == "season":
            path = (f"show:{rest.rpartition(':')[0]}", node_id)
        elif kind == "album":
            path = (f"artist:{rest.partition(':')[0]}", node_id)
        else:
            return False
        return self._go(replace(self.view, tab="library", path=path))

    def up(self) -> bool:
        """One level towards the top of the current collection (the Back button inside a show)."""
        if not self.view.path:
            return False
        return self._go(replace(self.view, path=self.view.path[:-1]))

    def home(self) -> bool:
        """Back to the top of the current collection: its shows, movies or artists."""
        changed = self._go(View(self.view.collection))        # the top of the media type you are in
        if not changed and self.query:
            self.query = ""
            return True
        return changed

    def back(self) -> bool:
        if not self._back:
            return False
        self._fwd.append(self.view)
        self.view = self._back.pop()
        self.query = ""
        return True

    def forward(self) -> bool:
        if not self._fwd:
            return False
        self._back.append(self.view)
        self.view = self._fwd.pop()
        self.query = ""
        return True

    def set_query(self, q: str) -> bool:
        q = q.strip()
        if q == self.query:
            return False
        self.query = q
        return True

    def prune(self, is_dead) -> bool:
        """Drop history that points at something that no longer exists, such as a removed show.
        `is_dead(path)` says whether a path is gone. Returns True when the current view had to change."""
        self._back = [v for v in self._back if not is_dead(v.path)]
        self._fwd = [v for v in self._fwd if not is_dead(v.path)]
        if is_dead(self.view.path):
            self.view = replace(self.view, path=())
            return True
        return False
