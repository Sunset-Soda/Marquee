"""The Twitch tab: sign-in, the channels you follow, and playing one, with the network work off the UI thread.
See core/twitch.py."""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import Property, QObject, Signal, Slot

from ..core import twitch as tw
from ..core.youtube import cookie_spec

LIVE_EVERY = 20                         # seconds: the least time between two reads of who is live
FOLLOWING_EVERY = 15 * 60               # the follow list itself changes rarely


def _log(msg: str) -> None:
    print(f"marquee: twitch: {msg}", flush=True)


class TwitchController(QObject):
    changed = Signal()
    streamReady = Signal(str)           # a channel's stream address is known (read it from `stream`)
    streamFailed = Signal(str, str)     # a channel could not be opened: its name, and why
    _loaded = Signal(object, object)    # live, following
    _loadFailed = Signal(str)
    _loginState = Signal(str, str)      # kind ("done" | "failed"), message
    _played = Signal(int, str, object)
    _playFailed = Signal(int, str, str)

    def __init__(self, youtube, parent=None):
        super().__init__(parent)
        self._yt = youtube                          # shares its yt-dlp and the browser you chose for YouTube
        self._store = tw.Store()
        self._api = tw.Twitch(self._store)
        self._live: list[dict] = []
        self._following: list[dict] = []
        self._busy = False
        self._error = ""
        self._loaded_at = 0.0
        self._following_at = 0.0
        self._login: dict | None = None             # the sign-in in progress: {user_code, url, ...}
        self._login_stop = threading.Event()
        self._stream: dict = {}
        self._channel = ""
        self._token = 0
        self._loaded.connect(self._on_loaded)
        self._loadFailed.connect(self._on_load_failed)
        self._loginState.connect(self._on_login_state)
        self._played.connect(self._on_played)
        self._playFailed.connect(self._on_play_failed)

    # ---- what the page shows -----------------------------------------------------------------
    @Property(bool, notify=changed)
    def signedIn(self):
        return self._store.signed_in

    @Property(str, notify=changed)
    def userName(self):
        return self._store.user.get("name", "")

    @Property(str, notify=changed)
    def userImage(self):
        return self._store.user.get("image", "")

    @Property(str, notify=changed)
    def clientId(self):
        return self._store.client_id

    @Property(bool, notify=changed)
    def loggingIn(self):
        return self._login is not None

    @Property(str, notify=changed)
    def loginCode(self):
        return (self._login or {}).get("user_code", "")

    @Property(str, notify=changed)
    def loginUrl(self):
        return (self._login or {}).get("url", "")

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def error(self):
        return self._error

    @Property("QVariantList", notify=changed)
    def live(self):
        return self._live

    @Property("QVariantList", notify=changed)
    def offline(self):
        on = {s["login"] for s in self._live}
        return [c for c in self._following if c["login"] not in on]

    @Property(bool, notify=changed)
    def adFree(self):
        return self._store.adfree

    @Property(str, notify=changed)
    def proxy(self):
        return self._store.proxy

    @Property(bool, notify=changed)
    def browserLogin(self):
        return self._store.browser_login

    @Property(bool, notify=changed)
    def hasBrowser(self):
        return bool(getattr(self._yt._dlp, "browser", ""))

    @Property(bool, notify=changed)
    def listView(self):
        return self._store.list_view

    @Slot(bool)
    def setListView(self, on: bool):
        self._store.list_view = bool(on)
        self._store.save()
        self.changed.emit()

    @Property(str, notify=changed)
    def quality(self):
        return self._store.quality

    @Property("QVariantMap", notify=changed)
    def stream(self):
        return self._stream

    @Property(str, notify=changed)
    def channel(self):
        return self._channel

    # ---- settings ----------------------------------------------------------------------------
    @Slot(str)
    def setClientId(self, text: str):
        text = text.strip()
        if text != self._store.client_id:
            self._store.client_id = text
            if self._store.signed_in:
                self._store.sign_out()                   # a login belongs to one application
            self._store.save()
            self._error = ""
            self.changed.emit()

    @Slot(bool)
    def setAdFree(self, on: bool):
        self._store.adfree = bool(on)
        self._store.save()
        self.changed.emit()

    @Slot(str)
    def setProxy(self, text: str):
        self._store.proxy = text.strip()
        self._store.save()
        self.changed.emit()

    @Slot(bool)
    def setBrowserLogin(self, on: bool):
        self._store.browser_login = bool(on)
        self._store.save()
        self.changed.emit()

    @Slot(str)
    def setQuality(self, quality: str):
        self._store.quality = quality if quality.isdigit() else "best"
        self._store.save()
        self.changed.emit()

    # ---- signing in --------------------------------------------------------------------------
    @Slot()
    def signIn(self):
        if self._login is not None:
            return
        self._error = ""

        def start():
            try:
                login = self._api.start_login()
            except tw.TwitchError as e:
                self._loginState.emit("failed", str(e))
                return
            self._login = login
            self.changed.emit()
            self._login_stop.clear()
            while not self._login_stop.is_set() and time.time() < login["expires"]:
                self._login_stop.wait(login["interval"])
                if self._login_stop.is_set():
                    return
                try:
                    if self._api.poll_login(login):
                        self._loginState.emit("done", "")
                        return
                except tw.TwitchError as e:
                    self._loginState.emit("failed", str(e))
                    return
            self._loginState.emit("failed", "The code ran out before it was used. Press Sign in to get a new one.")
        threading.Thread(target=start, daemon=True).start()

    @Slot()
    def cancelSignIn(self):
        self._login_stop.set()
        self._login = None
        self.changed.emit()

    def _on_login_state(self, kind: str, message: str):
        self._login = None
        if kind == "done":
            self._error = ""
            self._following_at = self._loaded_at = 0.0
            self.changed.emit()
            self.refresh()
        else:
            self._error = message
            self.changed.emit()

    @Slot()
    def signOut(self):
        self._store.sign_out()
        self._live, self._following = [], []
        self.changed.emit()

    # ---- who is live -------------------------------------------------------------------------
    @Slot()
    def refresh(self):
        """Read who is live (and, now and then, the whole follow list). Called when the page opens and every little while."""
        if not self._store.signed_in or self._busy:
            return
        now = time.time()
        if now - self._loaded_at < LIVE_EVERY:
            return
        self._busy = True
        self.changed.emit()
        need_following = now - self._following_at > FOLLOWING_EVERY or not self._following

        def work():
            try:
                live = self._api.live()
                following = self._api.following() if need_following else None
            except tw.TwitchError as e:
                self._loadFailed.emit(str(e))
                return
            except Exception as e:
                _log(f"refresh: {e!r}")
                self._loadFailed.emit("Couldn’t read your channels from Twitch.")
                return
            self._loaded.emit(live, following)
        threading.Thread(target=work, daemon=True).start()

    def _on_loaded(self, live, following):
        self._busy = False
        self._error = ""
        self._live = live
        self._loaded_at = time.time()
        if following is not None:
            self._following = following
            self._following_at = self._loaded_at
        self.changed.emit()

    def _on_load_failed(self, message: str):
        self._busy = False
        self._error = message
        self.changed.emit()

    # ---- playing -----------------------------------------------------------------------------
    @Slot(result=bool)
    def adPending(self):
        """A commercial break is listed ahead of the channel."""
        st = getattr(self, "_ads", None)
        return bool(st and st.pending())

    @Slot()
    def advance(self):
        """The commercial break has played: the channel carries on from where it ended."""
        st = getattr(self, "_ads", None)
        if st:
            st.advance()

    @Slot(str)
    def play(self, text: str):
        """Open a channel: a name from your list, or a name or twitch.tv link you typed."""
        login = tw.channel_login(text)
        if not login:
            self._error = "That isn’t a Twitch channel name or link."
            self.changed.emit()
            return
        self._token += 1
        token = self._token
        self._channel, self._stream, self._error = login, {}, ""
        self.changed.emit()
        dlp = self._yt._dlp
        spec = cookie_spec(dlp.browser) if getattr(dlp, "browser", "") else ""

        def work():
            try:
                info = tw.resolve(dlp.exe, login, self._store, spec)
            except tw.TwitchError as e:
                self._playFailed.emit(token, login, str(e))
                return
            except Exception as e:
                _log(f"{login}: {e!r}")
                self._playFailed.emit(token, login, "Couldn’t open that channel.")
                return
            try:                                            # breaks and the channel are separate videos: play each as its own playlist
                text = tw.fetch_playlist(info["url"])
                state = tw.AdState()
                starts_in_ad = state.open(text, info["url"])
                px = self._yt._proxy
                self._ads = state
                info = dict(info, raw=info["url"], startsInAd=starts_in_ad, adUrl=px.add_hls(info["url"], "ad", state), url=px.add_hls(info["url"], "main", state))
                if starts_in_ad:
                    _log(f"{login}: the stream opens with a commercial break; playing it, then the channel")
            except Exception as e:
                _log(f"{login}: playlist read failed: {e!r}")
            self._played.emit(token, login, info)
        threading.Thread(target=work, daemon=True).start()

    def _on_played(self, token: int, login: str, info):
        if token != self._token:
            return                                          # you opened something else meanwhile
        _log(f"{login}: {info['height']}p" + (", ad-free route" if info["adfree"] else "") + (f" ({info['note']})" if info["note"] else ""))
        self._stream = dict(info, login=login)
        self.changed.emit()
        self.streamReady.emit(login)
        self.probe("when it opened")

    def _on_play_failed(self, token: int, login: str, message: str):
        if token != self._token:
            return
        _log(f"{login}: {message}")
        self._error = message
        self.changed.emit()
        self.streamFailed.emit(login, message)

    @Slot(str)
    def probe(self, why: str):
        """Print what the current stream's playlist holds (for finding out why a stream stalls or stays black)."""
        url = self._stream.get("raw") or self._stream.get("url", "")
        if not url:
            return

        def work():
            _log(f"playlist check ({why}):")
            for line in tw.playlist_summary(url):
                _log("  " + line)
        threading.Thread(target=work, daemon=True).start()

    @Slot()
    def stop(self):
        self._token += 1
        self._channel, self._stream = "", {}
        self.changed.emit()
