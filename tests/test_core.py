import os
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marquee.core import artwork, desktop, iptv, youtube, library, lyrics, opensubs, palette, parse, playqueue, samples, spectrum, subsync, subtitles, tv  # noqa: E402
import json  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_movie(self):
        r = parse.parse_name("The.Matrix.1999.1080p.BluRay.x264.mkv", "video")
        self.assertEqual((r["kind"], r["title"], r["sub"]), ("movie", "The Matrix", "1999"))

    def test_movie_that_is_a_year(self):
        r = parse.parse_name("1917.mp4", "video")
        self.assertEqual((r["title"], r["sub"]), ("1917", ""))

    def test_show(self):
        r = parse.parse_name("Mr.Robot.S02E05.720p.mkv", "video")
        self.assertEqual((r["kind"], r["show"], r["season"], r["episode"]), ("show", "Mr Robot", 2, 5))
        self.assertEqual((r["title"], r["sub"]), ("Episode 5", "Mr Robot · S2 · E5"))

    def test_show_year_and_formats(self):
        r = parse.parse_name("Ted (2024) S01E01 Pilot.mkv", "video")
        self.assertEqual((r["show"], r["year"], r["title"]), ("Ted", 2024, "Pilot"))
        r = parse.parse_name("Ted.1x05.mkv", "video")
        self.assertEqual((r["show"], r["season"], r["episode"]), ("Ted", 1, 5))

    def test_folder_layout(self):
        r = parse.parse_path("/tv/Ted (2024)/Season 2/Ted - 07 - Some Title.mkv")
        self.assertEqual((r["show"], r["year"], r["season"], r["episode"], r["title"]), ("Ted", 2024, 2, 7, "Some Title"))
        self.assertEqual(parse.parse_path("/films/Heat.1995.mkv")["kind"], "movie")

    def test_music(self):
        r = parse.parse_name("03 - Radiohead - Weird Fishes.flac", "audio")
        self.assertEqual((r["kind"], r["sub"], r["title"]), ("music", "Radiohead", "Weird Fishes"))

    def test_media_of(self):
        self.assertEqual(parse.media_of("a.MKV"), "video")
        self.assertEqual(parse.media_of("a.flac"), "audio")
        self.assertIsNone(parse.media_of("a.txt"))


def id3_frame(fid: bytes, body: bytes) -> bytes:
    return fid + struct.pack(">I", len(body)) + b"\0\0" + body


def synchsafe(n: int) -> bytes:
    return bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127])


class Id3Tests(unittest.TestCase):
    def test_title_artist_cover(self):
        pic = b"\xff\xd8\xff\xe0fakejpeg"
        frames = (id3_frame(b"TIT2", b"\x03Weird Fishes") + id3_frame(b"TPE1", b"\x03Radiohead")
                  + id3_frame(b"APIC", b"\x00image/jpeg\x00\x03Cover\x00" + pic))
        blob = b"ID3\x03\x00\x00" + synchsafe(len(frames)) + frames + b"\0" * 20
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(blob)
        try:
            tags = parse.read_id3(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(tags["title"], "Weird Fishes")
        self.assertEqual(tags["artist"], "Radiohead")
        self.assertEqual(tags["pic"], pic)
        self.assertEqual(tags["pic_mime"], "image/jpeg")

    def test_no_tag(self):
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(b"\0" * 64)
        try:
            self.assertIsNone(parse.read_id3(f.name))
        finally:
            os.unlink(f.name)


class DesktopTests(unittest.TestCase):
    def test_families(self):
        self.assertEqual(desktop.detect({"XDG_CURRENT_DESKTOP": "KDE"}).family, "kde")
        self.assertEqual(desktop.detect({"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}).family, "gnome")
        self.assertEqual(desktop.detect({"XDG_CURRENT_DESKTOP": "X-Cinnamon"}).family, "other")
        self.assertEqual(desktop.detect({"XDG_CURRENT_DESKTOP": "LXQt"}).family, "lxqt")
        self.assertEqual(desktop.detect({"KDE_FULL_SESSION": "true"}).family, "kde")
        self.assertEqual(desktop.detect({}).family, "other")

    def test_choice(self):
        both = {"qt": True, "gtk": True}
        kde, gnome, xfce = (desktop.detect({"XDG_CURRENT_DESKTOP": n}) for n in ("KDE", "GNOME", "XFCE"))
        self.assertEqual(desktop.choose_toolkit(kde, "auto", both), "qt")
        self.assertEqual(desktop.choose_toolkit(gnome, "auto", both), "gtk")
        self.assertEqual(desktop.choose_toolkit(xfce, "auto", both), "gtk")
        self.assertEqual(desktop.choose_toolkit(gnome, "qt", both), "qt")
        self.assertEqual(desktop.choose_toolkit(kde, "auto", {"qt": False, "gtk": True}), "gtk")
        with self.assertRaises(RuntimeError):
            desktop.choose_toolkit(kde, "auto", {"qt": False, "gtk": False})

    def test_qt_env_is_not_forced_on_kde(self):
        env: dict = {}
        desktop.prepare_qt_environment(desktop.detect({"XDG_CURRENT_DESKTOP": "KDE"}), env)
        self.assertEqual(env, {})
        env = {}
        desktop.prepare_qt_environment(desktop.detect({"XDG_CURRENT_DESKTOP": "GNOME"}), env)
        self.assertEqual(env, {"QT_QPA_PLATFORMTHEME": "xdgdesktopportal"})
        env = {"QT_QPA_PLATFORMTHEME": "qt6ct"}
        desktop.prepare_qt_environment(desktop.detect({"XDG_CURRENT_DESKTOP": "GNOME"}), env)
        self.assertEqual(env, {"QT_QPA_PLATFORMTHEME": "qt6ct"})


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = {k: os.environ.get(k) for k in ("XDG_DATA_HOME", "XDG_CACHE_HOME")}
        os.environ["XDG_DATA_HOME"] = os.path.join(self.tmp.name, "data")
        os.environ["XDG_CACHE_HOME"] = os.path.join(self.tmp.name, "cache")
        self.media = Path(self.tmp.name) / "media"
        self.media.mkdir()
        for name in ("Heat.1995.1080p.mkv", "Show.S01E02.mp4", "Artist - Song.mp3", "notes.txt"):
            (self.media / name).write_bytes(b"x")

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_add_scan_save_load(self):
        lib = library.Library()
        new, skipped = lib.add_paths([self.media], probe=False)
        self.assertEqual((len(new), skipped), (3, 0))
        again, skipped = lib.add_paths([self.media], probe=False)
        self.assertEqual((len(again), skipped), (0, 3))
        loaded = library.Library().load()
        self.assertEqual(sorted(i.kind for i in loaded.items), ["movie", "music", "show"])

    def test_filters(self):
        lib = library.Library()
        lib.add_paths([self.media], probe=False)
        movie = next(i for i in lib.items if i.kind == "movie")
        movie.fav = True
        movie.pos, movie.dur = 600, 7200
        self.assertEqual([i.title for i in lib.visible("movie")], ["Heat"])
        self.assertEqual([i.title for i in lib.visible(tab="favorites")], ["Heat"])
        self.assertEqual([i.title for i in lib.visible(tab="continue")], ["Heat"])
        self.assertEqual([i.title for i in lib.visible(query="song")], ["Song"])
        movie.pos = 7195
        self.assertEqual(lib.visible(tab="continue"), [])

    def test_cover_and_remove(self):
        lib = library.Library()
        new, _ = lib.add_paths([self.media / "Heat.1995.1080p.mkv"], probe=False)
        lib.set_cover_bytes(new[0], b"\x89PNG\r\n\x1a\nxxxx")
        self.assertTrue(new[0].cover.endswith(".png") and os.path.exists(new[0].cover))
        cover = new[0].cover
        lib.remove(new[0])
        self.assertFalse(os.path.exists(cover))
        self.assertEqual(lib.items, [])

    def test_samples_seed_once(self):
        lib = library.Library()
        self.assertTrue(lib.ensure_samples())
        self.assertFalse(lib.ensure_samples())
        self.assertEqual(len(lib.items), 3)
        self.assertTrue(all(i.exists and i.sample for i in lib.items))


def png(tag: str) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + tag.encode()


class FakeTvmaze:
    """Stands in for the network. `ted_poster=False` gives a show with season art but no show poster."""

    def __init__(self, ted_poster=True):
        self.calls = []
        self.ted_poster = ted_poster

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if "/search/shows" in url:
            return json.dumps([
                {"score": 0.95, "show": {"id": 2, "name": "Ted Lasso", "premiered": "2020-08-14", "image": {"original": "http://img/lasso.png"}}},
                {"score": 0.90, "show": {"id": 1, "name": "Ted", "premiered": "2024-01-11",
                                         "image": {"original": "http://img/ted.png"} if self.ted_poster else None}},
            ]).encode()
        if url.endswith("/shows/1/seasons"):
            return json.dumps([{"number": 1, "name": "", "image": {"original": "http://img/s1.png"}},
                               {"number": 2, "name": "", "image": None}]).encode()
        if url.endswith("/shows/1/episodes"):
            return json.dumps([{"season": 1, "number": 1, "name": "Pilot", "airdate": "2024-01-11"},
                               {"season": 1, "number": 2, "name": "Second Wind", "airdate": "2024-01-11"},
                               {"season": 1, "number": 3, "name": "Three's Company", "airdate": "2024-01-11"},
                               {"season": 2, "number": 1, "name": "Return", "airdate": "2024-12-01"}]).encode()
        if url.startswith("http://img/"):
            return png(url.rsplit("/", 1)[1])
        raise AssertionError("unexpected url " + url)


class ShowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.tmp.name, "data")
        self.media = Path(self.tmp.name) / "tv"
        self.media.mkdir()
        for n in (1, 2, 3):
            (self.media / f"Ted.S01E0{n}.1080p.mkv").write_bytes(b"x")
        (self.media / "Ted.S02E01.mkv").write_bytes(b"x")
        (self.media / "Heat.1995.mkv").write_bytes(b"x")

    def tearDown(self):
        if self.old is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self.old
        self.tmp.cleanup()

    def lib(self):
        lib = library.Library()
        lib.add_paths([self.media], probe=False)
        return lib

    def test_episodes_fold_into_one_show_tile(self):
        lib = self.lib()
        top = lib.browse()
        self.assertEqual(sorted(i.title for i in top), ["Heat", "Ted"])
        ted = next(i for i in top if i.node == "show")
        self.assertEqual(ted.sub, "2 seasons · 4 episodes")

    def test_hierarchy_and_episode_names(self):
        lib = self.lib()
        status, text, renamed = tv.scrub(lib, "ted", fetch=FakeTvmaze())
        self.assertEqual((status, text, renamed), ("ok", "Ted (2024)", 4))     # Ted, not Ted Lasso
        seasons = lib.browse(path=("show:ted",))
        self.assertEqual([(s.title, s.sub, s.node) for s in seasons], [("Season 1", "3 episodes", "season"), ("Season 2", "1 episode", "season")])
        eps = lib.browse(path=("show:ted", "season:ted:1"))
        self.assertEqual([e.title for e in eps], ["Pilot", "Second Wind", "Three's Company"])
        self.assertEqual(eps[0].sub, "Ted · S1 · E1")

    def test_show_poster_never_comes_from_a_season(self):
        lib = self.lib()
        tv.scrub(lib, "ted", fetch=FakeTvmaze())
        show_node = next(i for i in lib.browse() if i.node == "show")
        s1, s2 = lib.browse(path=("show:ted",))
        self.assertEqual(Path(show_node.cover).read_bytes(), png("ted.png"))
        self.assertEqual(Path(s1.cover).read_bytes(), png("s1.png"))        # season art used for that season
        self.assertEqual(s2.cover, show_node.cover)                         # no season art: falls back to the show poster
        ep = lib.browse(path=("show:ted", "season:ted:1"))[0]
        self.assertEqual(lib.cover_for(ep), s1.cover)                       # episodes use their season's art

    def test_show_without_poster_stays_blank_even_if_a_season_has_art(self):
        lib = self.lib()
        tv.scrub(lib, "ted", fetch=FakeTvmaze(ted_poster=False))
        show_node = next(i for i in lib.browse() if i.node == "show")
        self.assertEqual(show_node.cover, "")                               # the season 1 poster must not leak up
        self.assertTrue(lib.browse(path=("show:ted",))[0].cover)

    def test_user_edited_titles_survive_a_lookup(self):
        lib = self.lib()
        ep = next(i for i in lib.items if i.season == 1 and i.episode == 2)
        lib.edit(ep, "My Name For It", "", "show")
        tv.scrub(lib, "ted", fetch=FakeTvmaze())
        self.assertEqual(ep.title, "My Name For It")
        self.assertEqual(next(i for i in lib.items if i.episode == 1 and i.season == 1).title, "Pilot")

    def test_flat_views_keep_episodes(self):
        lib = self.lib()
        tv.scrub(lib, "ted", fetch=FakeTvmaze())
        ep = next(i for i in lib.items if i.title == "Second Wind")
        ep.pos, ep.dur = 300, 1800
        self.assertEqual([i.title for i in lib.browse(tab="continue")], ["Second Wind"])
        self.assertEqual([i.title for i in lib.browse(tab="continue", query="second")], ["Second Wind"])
        # Search only looks at the level you are on: the main screen finds shows, not their episodes.
        self.assertEqual(lib.browse(query="pilot"), [])
        self.assertEqual([i.node for i in lib.browse(query="ted")], ["show"])
        # Inside a show it finds that show's episodes, whichever season they are in.
        self.assertEqual([i.title for i in lib.browse(query="pilot", path=("show:ted",))], ["Pilot"])
        self.assertEqual([i.title for i in lib.browse(query="second", path=("show:ted", "season:ted:1"))], ["Second Wind"])
        self.assertEqual(lib.browse(query="second", path=("show:ted", "season:ted:2")), [])

    def test_queue_continues_across_seasons(self):
        lib = self.lib()
        tv.scrub(lib, "ted", fetch=FakeTvmaze())
        last_of_s1 = lib.browse(path=("show:ted", "season:ted:1"))[-1]
        q = lib.queue_for(last_of_s1, [])
        self.assertEqual([lib.by_id(i).title for i in q], ["Pilot", "Second Wind", "Three's Company", "Return"])

    def test_best_match_is_careful(self):
        results = json.loads(FakeTvmaze()("/search/shows"))
        self.assertEqual(tv.best_match(results, "Ted", 2024)["id"], 1)
        self.assertEqual(tv.best_match(results, "Ted Lasso")["id"], 2)
        self.assertIsNone(tv.best_match(results, "Breaking Bad"))

    def test_no_match_leaves_names_alone_and_reports(self):
        lib = self.lib()
        status, text, renamed = tv.scrub(lib, "ted", query="Zzz Nothing", fetch=lambda u: b"[]")
        self.assertEqual((status, renamed), ("none", 0))
        self.assertFalse(lib.shows["ted"].matched)
        self.assertEqual(lib.browse(path=("show:ted", "season:ted:1"))[0].title, "Episode 1")

    def test_change_match_by_typing_a_name(self):
        lib = self.lib()
        status, text, _ = tv.scrub(lib, "ted", query="Ted 2024", fetch=FakeTvmaze())
        self.assertEqual((status, text), ("ok", "Ted (2024)"))

    def test_old_library_files_are_upgraded(self):
        lib = self.lib()
        path = lib.path
        raw = json.loads(path.read_text())
        for it in raw["items"]:
            for k in ("show_key", "season", "episode", "title_auto"):
                it.pop(k, None)
            if it["kind"] == "show":
                it["title"] = "Ted"
        raw.pop("shows")
        path.write_text(json.dumps(raw))
        again = library.Library().load()
        self.assertEqual([i.title for i in again.browse() if i.node == "show"], ["Ted"])
        self.assertEqual(len(again.browse(path=("show:ted", "season:ted:1"))), 3)

    def test_removing_last_episode_drops_the_show_and_its_art(self):
        lib = self.lib()
        tv.scrub(lib, "ted", fetch=FakeTvmaze())
        poster = lib.shows["ted"].poster
        for it in [i for i in lib.items if i.show_key == "ted"]:
            lib.remove(it)
        self.assertNotIn("ted", lib.shows)
        self.assertFalse(os.path.exists(poster))

    def test_report_message(self):
        rep = artwork.Report(shows=["Ted (2024)"], episodes=4, unmatched=["Obscure Show"])
        msg = rep.message()
        self.assertIn("Matched “Ted (2024)” and named 4 episodes", msg)
        self.assertIn("No TV match for “Obscure Show”", msg)


class SubtitleTests(unittest.TestCase):
    SRT = ("\ufeff1\r\n00:00:00,500 --> 00:00:02,500\r\nHello <i>world</i>\r\n\r\n"
           "2\r\n00:00:03,000 --> 00:00:03,800\r\n<font color=red>Second</font>\r\nline\r\n")

    def test_srt(self):
        c = subtitles.parse(self.SRT, "a.srt")
        self.assertEqual([(x.start, x.end) for x in c], [(0.5, 2.5), (3.0, 3.8)])
        self.assertEqual(c[0].text, "Hello <i>world</i>")
        self.assertEqual(c[1].text, "Second\nline")           # unknown tags dropped, line break kept

    def test_vtt_and_short_times(self):
        c = subtitles.parse("WEBVTT\n\nNOTE x\n\n00:01.000 --> 00:02.500 align:start\nHi\n", "a.vtt")
        self.assertEqual((c[0].start, c[0].end, c[0].text), (1.0, 2.5, "Hi"))

    def test_ass(self):
        ass = ("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
               "Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,{\\an8}Hello, {\\i1}there{\\i0}\\Nfriend\n")
        c = subtitles.parse(ass, "a.ass")
        self.assertEqual((c[0].start, c[0].end), (1.0, 2.5))
        self.assertEqual(c[0].text, "Hello, there\nfriend")      # comma inside the text survives

    def test_timeline(self):
        tl = subtitles.Timeline(subtitles.parse(self.SRT, "a.srt"))
        self.assertEqual(tl.at(0.2), "")
        self.assertEqual(tl.at(1.0), "Hello <i>world</i>")
        self.assertEqual(tl.at(2.6), "")
        self.assertEqual(tl.at(3.1, delay=0.5), "")             # delayed by half a second: not yet
        self.assertEqual(tl.at(3.6, delay=0.5), "Second\nline")

    def test_decode_legacy_encoding(self):
        self.assertEqual(subtitles.decode("caf\u00e9".encode("cp1252")), "caf\u00e9")
        self.assertEqual(subtitles.decode(b"\xef\xbb\xbfabc"), "abc")

    def test_sidecars(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "Subs").mkdir()
            for n in ("Movie.mkv", "Movie.srt", "Movie.en.srt", "Movie.fr.vtt", "Other.srt", "Movie.txt"):
                (d / n).write_text("x")
            (d / "Subs" / "Movie.es.srt").write_text("x")
            got = subtitles.sidecars(str(d / "Movie.mkv"))
            self.assertEqual(sorted(t.label for t in got), ["English", "French", "Spanish", "Subtitles"])
            self.assertTrue(all(t.source == "file" for t in got))

    def test_choose(self):
        a = subtitles.Track("e:2", "French", "embedded", lang="fre")
        b = subtitles.Track("e:3", "English", "embedded", lang="eng")
        self.assertEqual(subtitles.choose([a, b]), "e:3")        # English wins
        self.assertEqual(subtitles.choose([a, b], "e:2"), "e:2")  # a saved choice wins
        self.assertEqual(subtitles.choose([a, b], "off"), "")
        self.assertEqual(subtitles.choose([a]), "e:2")
        self.assertEqual(subtitles.choose([]), "")

    def test_library_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            lib = library.Library(Path(d) / "l.json")
            lib.items.append(library.Item(id="a", title="A", sub_files=["/x/a.srt"], sub_sel="off", sub_delay=1.5))
            lib.prefs["sub_size"] = 2
            lib.save()
            again = library.Library(Path(d) / "l.json").load()
            self.assertEqual((again.items[0].sub_files, again.items[0].sub_sel, again.items[0].sub_delay),
                             (["/x/a.srt"], "off", 1.5))
            self.assertEqual(again.prefs["sub_size"], 2)

    @unittest.skipUnless(__import__("shutil").which("ffmpeg") and __import__("shutil").which("ffprobe"), "needs ffmpeg")
    def test_embedded_track_from_mkv(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "en.srt").write_text(self.SRT, encoding="utf-8")
            (d / "es.srt").write_text("1\n00:00:00,300 --> 00:00:02,000\nHola\n", encoding="utf-8")
            v = d / "clip.mkv"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=4", "-i", str(d / "en.srt"),
                            "-i", str(d / "es.srt"), "-map", "0", "-map", "1", "-map", "2", "-c:v", "mpeg4", "-c:s", "srt",
                            "-metadata:s:s:0", "language=eng", "-metadata:s:s:1", "language=spa", str(v)], check=True)
            os.environ["XDG_CACHE_HOME"] = str(d / "cache")
            found = subtitles.tracks_for(str(v))
            self.assertEqual([t.label for t in found.tracks], ["English", "Spanish"])
            self.assertEqual(subtitles.choose(found.tracks), found.tracks[0].id)
            cues = subtitles.load(str(v), found.tracks[1])
            self.assertEqual(cues[0].text, "Hola")
            self.assertTrue(any((d / "cache" / "marquee" / "subs").iterdir()))   # cached for next time
            self.assertEqual(subtitles.load(str(v), found.tracks[1])[0].text, "Hola")


class ShowFolderTests(unittest.TestCase):
    """Picking one TV show folder must put every episode in every season folder under one show."""

    def _tree(self, d, files):
        for f in files:
            p = Path(d) / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")

    def test_season_folder_names(self):
        for name, want in (("Season 1", 1), ("Season 01 (1080p)", 1), ("S02", 2), ("Ted.S04.1080p.WEB", 4),
                           ("Series 3", 3), ("Specials", 0), ("Extras", None), ("Sopranos", None), ("Ted S01E01", None)):
            self.assertEqual(parse.season_of_folder(name), want, name)

    def test_whole_show_folder(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d, ["Ted (2024)/Season 1/Ted.S01E01.1080p.mkv", "Ted (2024)/Season 2/ted s02e01 - Title.mkv",
                           "Ted (2024)/Season 03/03 - Name.mkv", "Ted (2024)/Season 03/Episode 1.mkv",
                           "Ted (2024)/Ted.S04.1080p.WEB/Ted.S04E01.mkv", "Ted (2024)/S05/01.mkv",
                           "Ted (2024)/Season 1/Disc 1/Ted.S01E03.mkv", "Ted (2024)/Specials/Behind the scenes.mkv"])
            lib = library.Library(Path(d) / "l.json")
            new, _ = lib.add_paths([Path(d) / "Ted (2024)"], probe=False)
            self.assertEqual(len(new), 8)
            self.assertEqual({i.show_key for i in new}, {"ted-2024"})              # one show, not one per file name style
            self.assertEqual(sorted({i.season for i in new}), [0, 1, 2, 3, 4, 5])
            by = {Path(i.path).name: i for i in new}
            self.assertEqual((by["03 - Name.mkv"].season, by["03 - Name.mkv"].episode, by["03 - Name.mkv"].title), (3, 3, "Name"))
            self.assertEqual((by["Behind the scenes.mkv"].season, by["Behind the scenes.mkv"].episode), (0, 0))
            self.assertEqual(len(lib.shows), 1)

    def test_movie_extras_stay_movies(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d, ["Heat (1995)/Heat.1995.mkv", "Heat (1995)/Extras/Making of.mkv", "Heat (1995)/Specials/Cut.mkv"])
            lib = library.Library(Path(d) / "l.json")
            new, _ = lib.add_paths([Path(d) / "Heat (1995)"], probe=False)
            self.assertEqual({i.kind for i in new}, {"movie"})

    def test_picking_a_library_of_shows(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d, ["TV/Ted/Season 1/a.S01E01.mkv", "TV/Ted/Season 2/a.S02E01.mkv", "TV/Fargo/Season 1/Fargo.S01E01.mkv"])
            lib = library.Library(Path(d) / "l.json")
            lib.add_paths([Path(d) / "TV"], probe=False)
            self.assertEqual(sorted(lib.shows), ["fargo", "ted"])


class MovieMusicFolderTests(unittest.TestCase):
    def _tree(self, d, files):
        for f in files:
            p = Path(d) / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x")

    def _lib(self, d):
        return library.Library(Path(d) / "l.json")

    def test_movie_folders_name_the_movie(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d, ["Movies/Heat (1995)/heat.1080p.bluray.x264-GRP.mkv", "Movies/Heat (1995)/Extras/Making of.mkv",
                           "Movies/Heat (1995)/Heat-trailer.mkv", "Movies/Heat (1995)/poster.jpg",
                           "Movies/The Matrix 1999/tm.mkv", "Movies/Alien.1979.mkv", "Movies/Sample/s.mkv"])
            lib = self._lib(d)
            new, _ = lib.add_paths([Path(d) / "Movies"], probe=False)
            got = {(i.title, i.sub) for i in new}
            self.assertEqual(got, {("Heat", "1995"), ("The Matrix", "1999"), ("Alien", "1979")})   # no extras, trailer or sample
            heat = next(i for i in new if i.title == "Heat")
            self.assertTrue(heat.cover and Path(heat.cover).exists())                              # poster.jpg beside the film

    def test_extras_kept_when_picked_directly(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d, ["Heat (1995)/Extras/Making of.mkv"])
            new, _ = self._lib(d).add_paths([Path(d) / "Heat (1995)" / "Extras"], probe=False)
            self.assertEqual(len(new), 1)

    def test_music_folders(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d, ["Music/Pink Floyd/The Wall (1979)/Disc 1/01 - In the Flesh.flac",
                           "Music/Pink Floyd/The Wall (1979)/Disc 2/01 - Hey You.flac",
                           "Music/Pink Floyd/The Wall (1979)/cover.jpg",
                           "Music/Pink Floyd/Animals/01 Dogs.mp3", "Music/Radiohead - OK Computer/01 Airbag.mp3"])
            lib = self._lib(d)
            lib.add_paths([Path(d) / "Music"], probe=False)
            top = lib.browse()
            self.assertEqual(sorted(n.title for n in top), ["Pink Floyd", "Radiohead"])
            self.assertTrue(all(n.node == "artist" for n in top))
            albums = lib.browse(path=("artist:pink-floyd",))
            self.assertEqual(sorted(a.title for a in albums), ["Animals", "The Wall"])
            wall = next(a for a in albums if a.title == "The Wall")
            self.assertTrue(wall.cover)                                                            # cover.jpg in the album folder
            songs = lib.browse(path=("artist:pink-floyd", wall.id))
            self.assertEqual([s.title for s in songs], ["In the Flesh", "Hey You"])               # disc order
            self.assertEqual(lib.heading(("artist:pink-floyd", wall.id)), "Pink Floyd › The Wall")

    def test_music_search_is_scoped(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d, ["Music/Pink Floyd/Animals/01 Dogs.mp3", "Music/Pink Floyd/Animals/02 Sheep.mp3", "Music/Radiohead/OK Computer/01 Airbag.mp3"])
            lib = self._lib(d)
            lib.add_paths([Path(d) / "Music"], probe=False)
            self.assertEqual([n.title for n in lib.browse(query="dogs")], [])                      # songs are not searched at the top
            self.assertEqual([n.title for n in lib.browse(query="radio")], ["Radiohead"])
            self.assertEqual([s.title for s in lib.browse(query="sheep", path=("artist:pink-floyd",))], ["Sheep"])

    def test_queue_runs_through_the_album(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d, ["Music/A/One/01 x.mp3", "Music/A/One/02 y.mp3", "Music/A/Two/01 z.mp3"])
            lib = self._lib(d)
            lib.add_paths([Path(d) / "Music"], probe=False)
            y = next(i for i in lib.items if i.title == "y")
            self.assertEqual([lib.by_id(i).title for i in lib.queue_for(y, [])], ["x", "y", "z"])   # whole album, then later albums

    def test_clear_continue(self):
        with tempfile.TemporaryDirectory() as d:
            lib = self._lib(d)
            a = library.Item(id="a", title="A", pos=300, dur=1800)
            b = library.Item(id="b", title="B", pos=400, dur=1800)
            lib.items += [a, b]
            self.assertEqual(len(lib.browse(tab="continue")), 2)
            self.assertEqual(lib.clear_resume(["a"]), 1)
            self.assertEqual([i.id for i in lib.browse(tab="continue")], ["b"])
            self.assertEqual(lib.clear_resume(), 1)
            self.assertEqual(lib.browse(tab="continue"), [])


class NavigatorTests(unittest.TestCase):
    def test_back_forward_home(self):
        from marquee.core.nav import Navigator
        n = Navigator()
        self.assertFalse(n.can_back or n.can_forward)
        n.set_collection("movie")
        n.open_node("show:ted")
        n.open_node("season:ted:2")
        self.assertEqual(n.path, ("show:ted", "season:ted:2"))
        self.assertTrue(n.up())
        self.assertEqual(n.path, ("show:ted",))
        self.assertTrue(n.back())
        self.assertEqual(n.path, ("show:ted", "season:ted:2"))   # Back returns to where you were, not one level up
        self.assertTrue(n.forward())
        self.assertEqual(n.path, ("show:ted",))
        self.assertTrue(n.home())
        self.assertTrue(n.at_home)
        self.assertTrue(n.back())
        self.assertEqual(n.path, ("show:ted",))
        self.assertEqual(n.collection, "movie")

    def test_new_move_clears_forward_and_search(self):
        from marquee.core.nav import Navigator
        n = Navigator()
        n.set_tab("continue")
        n.back()
        self.assertTrue(n.can_forward)
        n.set_query("abc")
        n.set_tab("favorites")
        self.assertFalse(n.can_forward)
        self.assertEqual(n.query, "")                              # opening anything clears the search

    def test_music_nodes_and_prune(self):
        from marquee.core.nav import Navigator
        n = Navigator()
        n.open_node("album:abba:arrival")
        self.assertEqual(n.path, ("artist:abba", "album:abba:arrival"))
        n.open_node("show:gone")
        n.prune(lambda path: "show:gone" in path)
        self.assertEqual(n.path, ())
        self.assertFalse(any("show:gone" in v.path for v in n._back))


class OpenSubtitlesTests(unittest.TestCase):
    def _env(self, d):
        os.environ["XDG_DATA_HOME"] = str(d / "data")
        os.environ["XDG_CACHE_HOME"] = str(d / "cache")

    def _video(self, d):
        v = d / "Ted.S01E02.mkv"
        v.write_bytes(os.urandom(200_000))
        lib = library.Library(d / "l.json")
        lib.ensure_show("ted", "Ted", 2024)
        it = library.Item(id="a", title="Ep", kind="show", media="video", path=str(v), show_key="ted", season=1, episode=2)
        lib.items.append(it)
        return lib, it

    def fake(self, calls, results=None, srt=None):
        import json as _json

        def request(method, url, body=None, headers=None, timeout=0):
            calls.append((method, url, body, dict(headers or {})))
            if "/subtitles" in url:
                return _json.dumps({"data": results if results is not None else [
                    {"attributes": {"download_count": 5, "files": [{"file_id": 11}], "hearing_impaired": True}},
                    {"attributes": {"download_count": 50, "files": [{"file_id": 22}]}},
                    {"attributes": {"download_count": 9999, "files": [{"file_id": 33}], "hearing_impaired": True}}]}).encode()
            if url.endswith("/login"):
                return b'{"token": "tok", "base_url": "vip-api.opensubtitles.com"}'
            if url.endswith("/download"):
                return b'{"link": "https://dl.example/x.srt", "remaining": 4}'
            return (srt or "1\n00:00:01,000 --> 00:00:02,000\nHello\n").encode()
        return request

    def test_hash_of_known_data(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.bin"
            f.write_bytes(bytes(range(256)) * 1024)           # 256 KiB
            h = opensubs.moviehash(str(f))
            self.assertEqual(len(h), 16)
            self.assertEqual(h, opensubs.moviehash(str(f)))
            self.assertEqual(opensubs.moviehash(str(Path(d) / "missing")), "")

    def test_downloads_best_match_next_to_the_video(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._env(d)
            lib, it = self._video(d)
            calls = []
            creds = opensubs.Credentials(api_key="KEY", username="u", password="p", language="en")
            status, msg, path = opensubs.fetch_for_item(lib, it, creds, self.fake(calls))
            self.assertEqual((status, msg), ("ok", "Downloaded English subtitles"))
            self.assertEqual(Path(path), d / "Ted.S01E02.en.srt")
            self.assertEqual(it.sub_sel, "f:" + path)
            self.assertIn(path, it.sub_files)
            search = next(c for c in calls if "/subtitles" in c[1])
            self.assertIn("moviehash=", search[1])                      # exact release first
            self.assertEqual(search[3]["Api-Key"], "KEY")
            dl = next(c for c in calls if c[1].endswith("/download"))
            self.assertEqual(dl[2]["file_id"], 22)                       # not hearing-impaired, most downloaded of those
            self.assertEqual(dl[3]["Authorization"], "Bearer tok")
            self.assertTrue(dl[1].startswith("https://vip-api.opensubtitles.com"))

    def test_falls_back_to_show_search_and_reports_none(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._env(d)
            lib, it = self._video(d)
            calls = []
            creds = opensubs.Credentials(api_key="KEY", language="fr")
            status, msg, _ = opensubs.fetch_for_item(lib, it, creds, self.fake(calls, results=[]))
            self.assertEqual(status, "none")
            urls = [c[1] for c in calls if "/subtitles" in c[1]]
            self.assertEqual(len(urls), 3)
            self.assertIn("query=Ted+S01E02", urls[1])
            self.assertIn("season_number=1", urls[1])
            self.assertIn("episode_number=2", urls[1])

    def test_no_key_and_http_errors(self):
        import urllib.error
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._env(d)
            lib, it = self._video(d)
            self.assertEqual(opensubs.fetch_for_item(lib, it, opensubs.Credentials())[0], "nokey")

            def boom(method, url, body=None, headers=None, timeout=0):
                raise urllib.error.HTTPError(url, 401, "no", {}, None)
            status, msg, _ = opensubs.fetch_for_item(lib, it, opensubs.Credentials(api_key="bad"), boom)
            self.assertEqual(status, "error")
            self.assertIn("API key", msg)

    def test_credentials_are_private(self):
        with tempfile.TemporaryDirectory() as d:
            self._env(Path(d))
            opensubs.save_credentials(opensubs.Credentials(api_key=" abc ", username="me", password="pw", language="DE"))
            path = Path(d) / "data" / "marquee" / "opensubtitles.json"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            c = opensubs.load_credentials()
            self.assertEqual((c.api_key, c.username, c.language, c.ready), ("abc", "me", "de", True))


class PaletteTests(unittest.TestCase):
    def test_bars_always_contrast_with_the_backdrop(self):
        for hue in range(0, 360, 20):
            for sat in (0.0, 0.1, 0.4, 0.8, 1.0):
                p = palette.build((hue, sat), ((hue + 150) % 360, sat))
                self.assertGreaterEqual(p.contrast, palette.MIN_CONTRAST - 0.05, (hue, sat))

    def test_dominant_and_accent_from_pixels(self):
        red = [(200, 30, 30, 255)] * 300
        teal = [(20, 160, 170, 255)] * 90
        p = palette.from_pixels(red + teal)
        self.assertIsNotNone(p)
        r, g, b = (int(p.base[i:i + 2], 16) for i in (1, 3, 5))
        self.assertGreater(r, b)                                 # backdrop leans red, the dominant colour
        br, bg, bb = (int(p.bar[i:i + 2], 16) for i in (1, 3, 5))
        self.assertGreater(bb, br)                               # bars take the teal accent
        self.assertIsNone(palette.from_pixels([(0, 0, 0, 0)] * 10))

    def test_fallback_is_stable(self):
        self.assertEqual(palette.fallback("Song").bar, palette.fallback("Song").bar)


class SpectrumTests(unittest.TestCase):
    def _tone(self, hz):
        import math
        return [math.sin(2 * math.pi * hz * n / spectrum.RATE) * 0.3 for n in range(spectrum.WIN)]

    def test_tone_lights_the_right_band(self):
        b = spectrum.frame_bands(self._tone(1000))
        peak = max(range(spectrum.BANDS), key=lambda i: b[i])
        lo, hi = spectrum._EDGES[peak]
        self.assertTrue(lo * spectrum.RATE / spectrum.WIN - 60 <= 1000 <= hi * spectrum.RATE / spectrum.WIN + 60)
        self.assertLess(sum(1 for v in b if v > 0.5), 5)           # one tone is a narrow peak, not a wall of bars
        low = spectrum.frame_bands(self._tone(100))
        self.assertLess(max(range(spectrum.BANDS), key=lambda i: low[i]), peak)   # lower pitch, lower band
        self.assertEqual(max(spectrum.frame_bands([0.0] * spectrum.WIN)), 0.0)    # silence stays flat

    def test_pure_python_fft_matches_numpy(self):
        saved = spectrum._np
        try:
            if saved is None:
                self.skipTest("numpy not installed, the pure path is already in use")
            with_np = spectrum.frame_bands(self._tone(440))
            spectrum._np = None
            pure = spectrum.frame_bands(self._tone(440))
        finally:
            spectrum._np = saved
        self.assertTrue(all(abs(a - b) < 0.02 for a, b in zip(with_np, pure)))

    @unittest.skipUnless(__import__("shutil").which("ffmpeg"), "needs ffmpeg")
    def test_analyzer_follows_the_file(self):
        import subprocess
        import time
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "t.wav"
            # 1 s of 200 Hz, then 1 s of 3 kHz
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=200:d=1", "-f", "lavfi", "-i", "sine=f=3000:d=1",
                            "-filter_complex", "[0][1]concat=n=2:v=0:a=1", str(f)], check=True)
            a = spectrum.Analyzer(str(f)).start()
            for _ in range(100):
                if a.ready and all(x is not None for x in a.frames):
                    break
                time.sleep(0.1)
            a.stop()
            self.assertTrue(a.ready)
            first = a.bands(0.5, smooth=False)
            second = a.bands(1.5, smooth=False)
            self.assertLess(max(range(48), key=lambda i: first[i]), max(range(48), key=lambda i: second[i]))
            self.assertEqual(len(a.wave(0.5)), 96)


def _long_srt(tag: str) -> str:
    return "".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},500\nThis is spoken line number {i} of {tag}\n\n" for i in range(1, 41))


class FixListTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["XDG_DATA_HOME"] = str(Path(self.tmp.name) / "data")
        os.environ["XDG_CACHE_HOME"] = str(Path(self.tmp.name) / "cache")
        self.lib = library.Library(Path(self.tmp.name) / "lib.json")

    def _song(self, name, album="Album", artist="Band", cover=""):
        it = library.Item(id=name, title=name, kind="music", media="audio", sub=artist, path=str(Path(self.tmp.name) / (name + ".mp3")),
                          album=album, cover=cover)
        self.lib.items.append(it)
        return it

    def test_music_never_resumes(self):
        a = self._song("a")
        self.lib.record_position(a, 90.0, 200.0)
        self.assertEqual(a.pos, 0.0)
        self.assertFalse(a.resumable)
        a.pos = 90.0                                          # saved by an older version
        self.assertFalse(a.resumable)
        self.assertEqual(a.progress, 0.0)
        v = library.Item(id="v", title="v", kind="movie", media="video", path="/x.mp4", dur=600.0)
        self.lib.record_position(v, 100.0)
        self.assertTrue(v.resumable)

    def test_tracks_share_their_albums_picture(self):
        img = Path(self.tmp.name) / "c.jpg"
        img.write_bytes(b"\xff\xd8\xff")
        a, b, c = self._song("a", cover=str(img)), self._song("b"), self._song("c", album="Other")
        self.assertEqual(self.lib.share_album_covers(), 1)
        self.assertEqual(b.cover, str(img))
        self.assertEqual(c.cover, "")
        self.lib.remove(a)
        self.assertTrue(img.exists())                         # b still uses it

    @unittest.skipUnless(__import__("shutil").which("ffmpeg"), "needs ffmpeg")
    def test_every_track_gets_embedded_art_in_any_format(self):
        import subprocess
        d = Path(self.tmp.name) / "Band" / "Album"
        d.mkdir(parents=True)
        png = Path(self.tmp.name) / "c.png"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=32x32:d=1", "-frames:v", "1", str(png)], check=True)
        for n in (1, 2, 3):
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:d=1", "-i", str(png), "-map", "0", "-map", "1",
                            "-c:a", "flac", "-c:v", "png", "-disposition:v", "attached_pic", str(d / f"0{n} Song.flac")], check=True)
        new, _ = self.lib.add_paths([str(d.parent)])
        self.assertEqual(len(new), 3)
        self.assertTrue(all(i.cover and os.path.exists(i.cover) for i in new))
        self.assertEqual(len({i.cover for i in new}), 1)      # one stored picture, shared

    def test_reopens_where_you_left_off(self):
        self._song("a")
        self.assertEqual(self.lib.start_view(), ("music", "library"))   # first collection that has something
        self.lib.remember_view("movie", "continue")
        self.assertEqual(self.lib.start_view(), ("movie", "continue"))
        self.lib.remember_view(library.ALL, "library")                  # All Media is gone: never saved
        self.assertEqual(self.lib.start_view(), ("movie", "continue"))

    def test_home_is_the_top_of_the_current_collection(self):
        from marquee.core.nav import Navigator, View
        n = Navigator(View("music", "library"))
        n.open_node("artist:band")
        self.assertTrue(n.home())
        self.assertEqual((n.collection, n.path), ("music", ()))
        self.assertTrue(n.at_home)

    def test_subtitle_from_the_same_release_wins(self):
        def r(release, downloads, h=False):
            return {"attributes": {"release": release, "download_count": downloads, "moviehash_match": h, "files": [{"file_id": downloads}]}}
        results = [r("Show.S01E02.480p.DVDRip.XviD-AAA", 9000), r("Show.S01E02.1080p.WEB-DL.DDP5.1.H264-NTb", 100)]
        best = opensubs.pick(results, False, "Show.S01E02.1080p.WEB-DL.DDP5.1.H264-NTb")
        self.assertEqual(best["attributes"]["download_count"], 100)
        self.assertEqual(opensubs.pick(results + [r("x", 1, True)], False, "Show.S01E02.1080p")["attributes"]["download_count"], 1)

    def test_subtitle_for_another_episode_is_refused(self):
        def r(season, episode, downloads):
            return {"attributes": {"download_count": downloads, "files": [{"file_id": downloads}],
                                   "feature_details": {"season_number": season, "episode_number": episode}}}
        results = [r(1, 1, 9000), r(2, 1, 50)]                # the popular one is the pilot, but we want S02E01
        self.assertEqual(opensubs.pick(results, False, "", (2, 1))["attributes"]["download_count"], 50)
        self.assertIsNone(opensubs.pick([r(1, 1, 9000)], False, "", (2, 1)))
        named = {"attributes": {"download_count": 1, "release": "Show.S01E01.720p", "files": [{"file_id": 1, "file_name": "x.srt"}]}}
        self.assertFalse(opensubs.fits_episode(named, 2, 1))   # no details, but the release name says S01E01
        self.assertTrue(opensubs.fits_episode(named, 1, 1))

    def test_one_subtitle_saved_for_several_episodes_is_repaired(self):
        eps = []
        for n in (1, 2, 3):
            f = Path(self.tmp.name) / f"s01e0{n}.en.srt"
            f.write_text(_long_srt("the pilot"))                              # the pilot's file, saved three times
            it = library.Item(id=f"e{n}", title=f"E{n}", kind="show", media="video", path=f"/x/e{n}.mkv", show_key="show",
                              season=1, episode=n, sub_files=[str(f)], sub_sel="f:" + str(f), sub_tried=True)
            self.lib.items.append(it)
            eps.append((it, f))
        self.assertEqual(opensubs.repair_duplicates(self.lib), 2)
        self.assertTrue(eps[0][1].exists())
        self.assertFalse(eps[1][1].exists() or eps[2][1].exists())
        self.assertEqual(eps[1][0].sub_files, [])
        self.assertFalse(eps[1][0].sub_tried)                  # so it is looked up again

    def test_duplicate_subtitle_files_beside_videos_are_repaired(self):
        made = []
        for n in (1, 2):
            v = Path(self.tmp.name) / f"Show S01E0{n}.mkv"
            v.write_bytes(b"x" * 10)
            srt = Path(self.tmp.name) / f"Show S01E0{n}.en.srt"
            srt.write_text(_long_srt("the pilot"))
            self.lib.items.append(library.Item(id=f"v{n}", title=f"E{n}", kind="show", media="video", path=str(v), show_key="s", season=1, episode=n))
            made.append(srt)
        self.assertEqual(opensubs.repair_duplicates(self.lib), 1)
        self.assertTrue(made[0].exists())
        self.assertFalse(made[1].exists())

    def test_another_release_of_an_earlier_episode_is_refused_and_repaired(self):
        from marquee.core import subtitles
        cues = subtitles.parse(_long_srt("the pilot"), "x.srt")
        other = subtitles.parse(_long_srt("the pilot").replace("00:00:", "00:01:"), "x.srt")      # same words, different timing
        self.assertTrue(opensubs.same_dialogue(opensubs.dialogue(cues), opensubs.dialogue(other)))
        self.assertFalse(opensubs.same_dialogue(opensubs.dialogue(cues), opensubs.dialogue(subtitles.parse(_long_srt("episode two").replace("spoken line", "words"), "x.srt"))))
        eps = []
        for n, tag in ((1, "the pilot"), (2, "the pilot")):                   # episode 2 holds a copy of the pilot's dialogue
            v = Path(self.tmp.name) / f"Show S01E0{n}.mkv"
            v.write_bytes(b"x" * 10)
            srt = Path(self.tmp.name) / f"Show S01E0{n}.en.{'' if n == 1 else '2.'}srt"
            srt.write_text(_long_srt(tag).replace("00:00:", "00:0%d:" % n))
            self.lib.items.append(library.Item(id=f"w{n}", title=f"E{n}", kind="show", media="video", path=str(v), show_key="s2", season=1, episode=n))
            eps.append(srt)
        self.assertEqual(opensubs.repair_duplicates(self.lib), 1)
        self.assertTrue(eps[0].exists())
        self.assertFalse(eps[1].exists())

    def test_stale_download_marks_are_cleared_once(self):
        a = library.Item(id="z1", title="z", path="/x", sub_tried=True)
        self.lib.items.append(a)
        opensubs.reset_stale_attempts(self.lib)
        self.assertFalse(a.sub_tried)
        a.sub_tried = True                      # a real "nothing exists online" answer stays remembered
        opensubs.reset_stale_attempts(self.lib)
        self.assertTrue(a.sub_tried)

    def test_bars_scale_to_the_track(self):
        from array import array
        import math
        quiet = array("h", [int(800 * math.sin(i / 7)) for i in range(16000 * 3)])
        loud = array("h", [int(20000 * math.sin(i / 7)) for i in range(16000 * 3)])
        self.assertGreater(spectrum.track_gain(quiet), spectrum.track_gain(loud))
        self.assertLessEqual(spectrum.track_gain(quiet), 18.0)


class SubSyncTests(unittest.TestCase):
    def setUp(self):
        import random
        rnd = random.Random(7)
        self.bursts, t = [], 4.0
        while t < 1500:
            n = rnd.uniform(1.0, 3.5)
            self.bursts.append((t, t + n))
            t += n + rnd.uniform(0.8, 4.0)
        steps = int(1510 / subsync.STEP)
        self.levels = [-60.0 + rnd.uniform(-2, 2)] * steps
        for a, b in self.bursts:
            for i in range(int(a / subsync.STEP), int(b / subsync.STEP)):
                self.levels[i] = -25.0 + rnd.uniform(-4, 4)

    def cues(self, delay=0.0, scale=1.0):
        # the cues as a wrongly timed file would have them: moving them by (delay, scale) puts them right
        return [subtitles.Cue((a - delay) / scale, (b - delay) / scale, "line") for a, b in self.bursts]

    def test_already_in_step(self):
        r = subsync.fit(self.cues(), self.levels)
        self.assertIsNotNone(r)
        self.assertFalse(r.changed)

    def test_finds_the_delay(self):
        for d in (3.4, -12.7, 45.0):
            r = subsync.fit(self.cues(d), self.levels)
            self.assertIsNotNone(r, d)
            self.assertAlmostEqual(r.delay, d, delta=0.1)
            self.assertEqual(r.scale, 1.0)

    def test_finds_a_frame_rate_change(self):
        r = subsync.fit(self.cues(2.0, 25 / 23.976), self.levels)
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r.scale, 1.042709, delta=0.0006)
        self.assertAlmostEqual(r.delay, 2.0, delta=0.1)

    def test_gives_up_on_unrelated_subtitles(self):
        import random
        rnd = random.Random(2)
        junk = [subtitles.Cue(i * 6 + rnd.random(), i * 6 + rnd.random() + 1.5, "x") for i in range(50)]
        self.assertIsNone(subsync.fit(junk, self.levels))
        self.assertIsNone(subsync.fit(self.cues(), [-50.0] * 4000))     # silence: nothing to follow

    def test_timeline_applies_scale_and_delay(self):
        tl = subtitles.Timeline([subtitles.Cue(10, 12, "hello")])
        self.assertEqual(tl.at(11, 0, 1.0), "hello")
        self.assertEqual(tl.at(11, 5, 1.0), "")              # shown 5 s later: 15 to 17
        self.assertEqual(tl.at(16, 5, 1.0), "hello")
        self.assertEqual(tl.at(21, 0, 2.0), "hello")         # stretched: 20 to 24

    def test_runs_once_per_track_and_never_for_embedded(self):
        it = library.Item(id="a", title="a", path=__file__)
        self.assertTrue(subsync.wanted(it, "f:x", {}))
        self.assertFalse(subsync.wanted(it, "f:x", {"sub_autosync": False}))
        self.assertFalse(subsync.wanted(it, "t:0", {}, embedded=True))
        class Lib:
            def save(self): pass
        subsync.apply(Lib(), it, "f:x", subsync.Result(1.5, 1.0, 9.0, True))
        self.assertEqual((it.sub_delay, it.sub_synced), (1.5, "f:x"))
        self.assertFalse(subsync.wanted(it, "f:x", {}))

    @unittest.skipUnless(subsync.available(), "ffmpeg not installed")
    def test_reads_speech_from_real_audio(self):
        import math
        import random
        import wave
        rnd = random.Random(3)
        bursts, t = [], 5.0
        while t < 100:
            n = rnd.uniform(1.0, 3.0)
            bursts.append((t, t + n))
            t += n + rnd.uniform(1.0, 3.5)
        rate = 8000
        pcm = [rnd.randint(-120, 120) for _ in range(rate * 110)]
        for a, b in bursts:
            for i in range(int(a * rate), int(b * rate)):
                pcm[i] += int(9000 * math.sin(i * 0.31) * rnd.uniform(0.5, 1))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "clip.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(struct.pack("<%dh" % len(pcm), *pcm))
            cues = [subtitles.Cue(a - 6.5, b - 6.5, "x") for a, b in bursts]
            r = subsync.sync(str(path), cues)
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r.delay, 6.5, delta=0.15)


class PlayQueueTests(unittest.TestCase):
    def q(self, **kw):
        import random
        q = playqueue.PlayQueue(random.Random(4))
        q.start(list("abcde"), "c")
        q.shuffle = kw.get("shuffle", False)
        q.set_repeat(kw.get("repeat", "off"))
        return q

    def test_plays_through_then_stops(self):
        q = self.q()
        self.assertEqual([q.next(), q.next(), q.next()], ["d", "e", ""])

    def test_repeat_all_wraps(self):
        q = self.q(repeat="all")
        q.next(); q.next()
        self.assertEqual(q.next(True), "a")

    def test_repeat_one_replays_only_when_it_ends_by_itself(self):
        q = self.q(repeat="one")
        self.assertEqual(q.next(True), "c")
        self.assertEqual(q.next(False), "d")

    def test_shuffle_keeps_current_and_plays_every_track_once(self):
        q = self.q()
        q.set_shuffle(True)
        self.assertEqual(q.current, "c")
        seen = ["c"] + [q.next() for _ in range(4)]
        self.assertEqual(sorted(seen), list("abcde"))
        self.assertEqual(q.next(), "")
        q.set_shuffle(False)
        self.assertEqual(q.order, list("abcde"))

    def test_previous(self):
        q = self.q()
        self.assertEqual(q.previous(), "b")
        q.pos = 0
        self.assertEqual(q.previous(), "")                 # the caller restarts the track
        q.set_repeat("all")
        self.assertEqual(q.previous(), "e")

    def test_repeat_cycles(self):
        q = self.q()
        self.assertEqual([q.cycle_repeat() for _ in range(3)], ["all", "one", "off"])


LRC = """[ar:Someone]
[ti:A Song]
[offset:+500]
[00:12.50]First line
[00:15.00]Second <00:15.40>line
[00:09.1][01:00.00]Chorus
[00:20.00]
[00:25.00]Last line
"""


class LyricsTests(unittest.TestCase):
    def test_parses_timed_lyrics(self):
        ly = lyrics.parse(LRC)
        self.assertTrue(ly.synced)
        self.assertEqual([ln.text for ln in ly.lines][:3], ["Chorus", "First line", "Second line"])
        self.assertAlmostEqual(ly.lines[0].t, 9.6)          # 9.1 s plus the 500 ms offset
        self.assertEqual(sum(1 for ln in ly.lines if ln.text == "Chorus"), 2)
        self.assertEqual(ly.lines[-1].t, 60.5)

    def test_plain_lyrics_have_no_timing(self):
        ly = lyrics.parse("One\nTwo\nThree")
        self.assertFalse(ly.synced)
        self.assertEqual(len(ly.lines), 3)
        self.assertEqual(ly.lines[0].t, -1)

    def test_nothing_to_show(self):
        self.assertIsNone(lyrics.parse(""))
        self.assertIsNone(lyrics.parse("[ar:x]\n[ti:y]"))

    def test_sidecar_file(self):
        with tempfile.TemporaryDirectory() as d:
            song = Path(d) / "Song.mp3"
            song.write_bytes(b"x")
            (Path(d) / "Song.lrc").write_text(LRC)
            ly = lyrics.from_sidecar(str(song))
        self.assertTrue(ly.synced)
        self.assertEqual(ly.source, "file")

    def test_lrclib_prefers_synced_and_the_right_length(self):
        calls = []

        def fake(url):
            calls.append(url)
            if "/get?" in url:
                raise urllib_error(404)
            return json.dumps([
                {"duration": 300, "plainLyrics": "a\nb\nc"},
                {"duration": 201, "syncedLyrics": LRC, "plainLyrics": "x\ny"},
                {"duration": 400, "syncedLyrics": LRC},
            ]).encode()
        ly = lyrics.from_lrclib("Someone", "A Song (feat. Other)", "Album", 200, fake)
        self.assertTrue(ly.synced)
        self.assertEqual(ly.source, "lrclib")
        self.assertTrue(any("search" in c for c in calls))

    def test_lrclib_rejects_a_different_recording(self):
        def fake(url):
            if "/get?" in url:
                raise urllib_error(404)
            return json.dumps([{"duration": 400, "syncedLyrics": LRC}]).encode()
        self.assertIsNone(lyrics.from_lrclib("Someone", "A Song", "", 200, fake))

    def test_lookup_caches_and_survives_no_connection(self):
        import urllib.error
        tmp = tempfile.TemporaryDirectory()
        old = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = tmp.name
        try:
            lib = library.Library(Path(tmp.name) / "lib.json") if hasattr(library.Library, "__init__") else None
            item = library.Item(id="l1", title="A Song", path="/nonexistent/a.mp3", kind="music", media="audio", dur=200)
            hits = []

            def ok(url):
                hits.append(url)
                return json.dumps({"duration": 200, "syncedLyrics": LRC}).encode()
            self.assertTrue(lyrics.lookup(lib, item, request=ok).synced)
            self.assertTrue(lyrics.lookup(lib, item, request=ok).synced)
            self.assertEqual(len(hits), 1)                    # the second answer came from the cache

            other = library.Item(id="l2", title="Other", path="/nonexistent/b.mp3", kind="music", media="audio", dur=100)

            def down(url):
                raise urllib.error.URLError("offline")
            self.assertIsNone(lyrics.lookup(lib, other, request=down))
        finally:
            if old is None:
                os.environ.pop("XDG_CACHE_HOME", None)
            else:
                os.environ["XDG_CACHE_HOME"] = old
            tmp.cleanup()


def urllib_error(code):
    import urllib.error
    return urllib.error.HTTPError("http://x", code, "nope", {}, None)


M3U = """#EXTM3U url-tvg="http://guide.example/epg.xml.gz"
#EXTINF:-1 tvg-id="bbc1.uk" tvg-name="BBC One" tvg-logo="http://l/bbc1.png" group-title="UK, News",BBC One HD
http://s/1.ts
#EXTINF:-1 group-title="Sport",Sky Sports 1
#EXTGRP:Ignored
http://s/2.ts
#EXTINF:-1,No Group
http://s/3.ts
#EXTINF:-1,Dup
http://s/3.ts
#EXTINF:-1,Orphan
"""


def xmltv(now):
    def t(x):
        return time.strftime("%Y%m%d%H%M%S +0000", time.gmtime(x))
    return (
        '<tv><channel id="bbc1.uk"><display-name>BBC One</display-name></channel>'
        '<channel id="sky.s1"><display-name>Sky Sports 1 HD</display-name></channel>'
        '<channel id="other"><display-name>Unwanted</display-name></channel>'
        f'<programme start="{t(now-7200)}" stop="{t(now-3600)}" channel="bbc1.uk"><title>Old</title></programme>'
        f'<programme start="{t(now-1800)}" stop="{t(now+1800)}" channel="bbc1.uk"><title>News</title><desc>Headlines</desc><category>News</category></programme>'
        f'<programme start="{t(now+1800)}" stop="{t(now+5400)}" channel="bbc1.uk"><title>Film</title></programme>'
        f'<programme start="{t(now-600)}" stop="{t(now+600)}" channel="sky.s1"><title>Match</title></programme>'
        f'<programme start="{t(now)}" stop="{t(now+600)}" channel="other"><title>Nope</title></programme>'
        f'<programme start="{t(now+10*86400)}" stop="{t(now+10*86400+60)}" channel="bbc1.uk"><title>Far</title></programme>'
        '</tv>').encode()


class IptvTests(unittest.TestCase):
    def test_m3u(self):
        chans, epg = iptv.parse_m3u(M3U)
        self.assertEqual(epg, ["http://guide.example/epg.xml.gz"])
        self.assertEqual([c.name for c in chans], ["BBC One HD", "Sky Sports 1", "No Group"])
        self.assertEqual(chans[0].group, "UK, News")
        self.assertEqual(chans[0].tvg_id, "bbc1.uk")
        self.assertEqual(chans[1].group, "Sport")      # group-title beats #EXTGRP

    def test_time(self):
        self.assertEqual(iptv.parse_time("20260101000000 +0000"), iptv.parse_time("20260101130000 +1300"))
        self.assertEqual(iptv.parse_time("20260101000000"), iptv.parse_time("20260101000000 Z"))
        self.assertIsNone(iptv.parse_time("soon"))

    def test_norm(self):
        self.assertEqual(iptv.norm_name("UK: BBC One HD [Backup]"), iptv.norm_name("bbc one"))
        self.assertEqual(iptv.norm_name("Café 4K"), "cafe")

    def test_xmltv_filters_and_gzip(self):
        import gzip
        import io
        now = time.time()
        raw = xmltv(now)
        for data in (raw, gzip.compress(raw)):
            progs, names = iptv.parse_xmltv(io.BytesIO(data), {"bbc1.uk"}, {iptv.norm_name("Sky Sports 1")},
                                            now - 3600, now + 36 * 3600)
            self.assertEqual(sorted(progs), ["bbc1.uk", "sky.s1"])
            self.assertEqual([p.title for p in progs["bbc1.uk"]], ["News", "Film"])
            self.assertEqual(progs["bbc1.uk"][0].desc, "Headlines")

    def _store(self, tmp, now):
        import io
        store = iptv.Store(Path(tmp) / "d", Path(tmp) / "c")
        store.add_playlist("Test", "mem://list")

        def opener(u):
            return io.BytesIO(M3U.encode() if u == "mem://list" else xmltv(now))
        return store, opener

    def test_store_refresh_match_and_cache(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            store, opener = self._store(tmp, now)
            self.assertEqual(store.refresh(opener=opener, now=now), "")
            bbc, sky, nog = store.channels
            self.assertEqual(store.epg_of[bbc.id], "bbc1.uk")
            self.assertEqual(store.epg_of[sky.id], "sky.s1")       # matched by name
            self.assertNotIn(nog.id, store.epg_of)
            cur, nxt = store.now_next(bbc.id, now)
            self.assertEqual((cur.title, nxt.title), ("News", "Film"))
            cur, nxt = store.now_next(bbc.id, now + 4000)
            self.assertEqual((cur.title, nxt), ("Film", None))
            self.assertEqual(store.now_next(nog.id, now), (None, None))
            self.assertFalse(store.stale(now))
            again = iptv.Store(Path(tmp) / "d", Path(tmp) / "c")    # survives a restart
            self.assertEqual(len(again.channels), 3)
            self.assertEqual(again.now_next(bbc.id, now)[0].title, "News")

    def test_groups_favourites_search(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            store, opener = self._store(tmp, now)
            store.refresh(opener=opener, now=now)
            self.assertEqual([g[0] for g in store.groups()], [iptv.ALL, "Other", "Sport", "UK, News"])
            store.toggle_favourite(store.channels[1].id)
            self.assertEqual(store.groups()[0][:1], (iptv.FAV,))
            self.assertEqual([c.name for c in store.select(iptv.FAV)], ["Sky Sports 1"])
            self.assertEqual([c.name for c in store.select(iptv.ALL, "bbc")], ["BBC One HD"])
            nxt = store.neighbour(store.channels[2].id, 1)
            self.assertEqual(nxt.name, "BBC One HD")                 # wraps around

    def test_refresh_reports_errors_and_keeps_cache(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            store, opener = self._store(tmp, now)
            store.refresh(opener=opener, now=now)

            def down(u):
                raise OSError("offline")
            err = store.refresh(force=True, opener=down, now=now + 100)
            self.assertIn("offline", err)
            self.assertEqual(len(store.channels), 3)
            self.assertEqual(store.now_next(store.channels[0].id, now)[0].title, "News")


class ArtistAlbumsTests(unittest.TestCase):
    def test_albums_with_songs_in_track_order(self):
        lib = library.Library.__new__(library.Library)
        lib.items = []
        for album, track, title in (("Beta", 2, "b2"), ("Alpha", 1, "a1"), ("Beta", 1, "b1"), ("Alpha", 2, "a2")):
            lib.items.append(library.Item(id=title, title=title, kind="music", media="audio", sub="Band", album=album, track=track))
        lib.shows = {}
        got = lib.artist_albums(lib.akey("Band"))
        self.assertEqual([n.title for n, _ in got], ["Alpha", "Beta"])
        self.assertEqual([[s.id for s in ss] for _, ss in got], [["a1", "a2"], ["b1", "b2"]])


RSS = b"""<?xml version="1.0"?><feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
<yt:channelId>UCabc</yt:channelId><title>Chan</title>
<entry><yt:videoId>AAAAAAAAAAA</yt:videoId><title>First</title><author><name>Chan</name></author><published>2026-01-02T00:00:00+00:00</published>
<media:group><media:thumbnail url="http://t/1.jpg"/><media:community><media:statistics views="1234"/></media:community></media:group></entry>
<entry><yt:videoId>BBBBBBBBBBB</yt:videoId><title>Second</title><author><name>Chan</name></author><published>2026-01-01T00:00:00+00:00</published></entry>
</feed>"""


class YoutubeQualityTests(unittest.TestCase):
    LV = [{"res": 360, "size": 20_000_000}, {"res": 480, "size": 40_000_000},
          {"res": 720, "size": 90_000_000}, {"res": 1080, "size": 200_000_000}]

    def test_fixed(self):
        self.assertEqual(youtube.choose_level("720", self.LV), 720)
        self.assertEqual(youtube.choose_level("1080", self.LV[:2]), 480)
        self.assertEqual(youtube.choose_level("360", self.LV), 360)

    def test_auto_follows_speed(self):
        self.assertEqual(youtube.choose_level("auto", self.LV, speed=500_000), 360)
        self.assertEqual(youtube.choose_level("auto", self.LV, speed=2_500_000), 480)
        self.assertEqual(youtube.choose_level("auto", self.LV, speed=5_000_000), 720)
        self.assertEqual(youtube.choose_level("auto", self.LV, speed=20_000_000), 1080)
        self.assertEqual(youtube.choose_level("auto", []), 360)

    def test_dash_reuses_and_keeps_playing_file(self):
        with tempfile.TemporaryDirectory() as d:
            from unittest import mock
            folder = Path(d) / "youtube"
            folder.mkdir()
            playing = folder / "vid-1080p.mp4"
            playing.write_bytes(b"x" * 2_000_000)
            other = folder / "vid-720p.mp4"
            other.write_bytes(b"y" * 2_000_000)
            with mock.patch.object(youtube, "cache_dir", return_value=Path(d)):
                got = youtube.YtDlp(exe="yt-dlp").fetch_dash("vid", {"dur": 10}, 720)
            self.assertEqual(got["file"], str(other))
            self.assertTrue(playing.exists())

    def test_fast_start(self):
        import json as _json
        d = {"id": "abcdefghijk", "title": "T", "duration": 100, "url": "https://x.test/v", "format_id": "18", "ext": "mp4",
             "protocol": "https", "height": 360, "width": 640,
             "formats": [{"vcodec": "none", "acodec": "mp4a", "ext": "m4a", "protocol": "https", "filesize": 1_000_000},
                         {"vcodec": "avc1", "acodec": "none", "width": 1280, "height": 720, "protocol": "https", "filesize": 20_000_000}]}
        with tempfile.TemporaryDirectory() as tmp:
            from unittest import mock
            with mock.patch.object(youtube, "cache_dir", return_value=Path(tmp)):
                dl = youtube.YtDlp(exe="yt-dlp", runner=lambda a: _json.dumps(d))
                fast = dl.resolve("abcdefghijk", probe=lambda i: True, quality="720", fast=True)
                self.assertEqual(fast["upgrade"]["res"], 720)
                self.assertNotIn("file", fast)
                (Path(tmp) / "youtube").mkdir()
                (Path(tmp) / "youtube" / "abcdefghijk-720p.mp4").write_bytes(b"x" * 2_000_000)
                self.assertNotIn("upgrade", fast["upgrade"]["meta"])       # no loop: the copy carries no upgrade of its own
                again = dl.resolve("abcdefghijk", probe=lambda i: True, quality="720", fast=True)
                self.assertTrue(again["file"].endswith("-720p.mp4"))       # saved already: no waiting, no upgrade
                self.assertNotIn("upgrade", again)

    def test_feed_fallback_pieces(self):
        a = [{"id": "a1", "published": 0}, {"id": "a2", "published": 0}]
        b = [{"id": "b1", "published": 0}, {"id": "a1", "published": 0}, {"id": "b3", "published": 0}]
        self.assertEqual([v["id"] for v in youtube.interleave([a, b])], ["a1", "b1", "a2", "b3"])
        failed: list = []

        def boom(url):
            raise OSError("HTTP Error 404")
        self.assertEqual(youtube.subscription_feed([{"id": "UC1", "name": "x"}], opener=boom, failed=failed), [])
        self.assertEqual(failed[0]["id"], "UC1")

    def test_list_mode_remembered(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = youtube.Store(Path(tmp))
            self.assertFalse(st.list_mode)
            st.set_list_mode(True)
            self.assertTrue(youtube.Store(Path(tmp)).list_mode)

    def test_cache_setting_and_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            from unittest import mock
            with mock.patch.object(youtube, "cache_dir", return_value=Path(tmp)):
                st = youtube.Store(Path(tmp))
                self.assertEqual(st.keep_count, 3)
                st.set_cache("1")
                self.assertEqual(youtube.Store(Path(tmp)).keep_count, 1)
                st.set_cache("exit")
                self.assertEqual(st.keep_count, 3)
                st.set_cache("bogus")
                self.assertEqual(st.cache, "3")
                f = Path(tmp) / "youtube"
                f.mkdir()
                (f / "a-1080p.mp4").write_bytes(b"x" * 1000)
                (f / "b-720p.mp4").write_bytes(b"x" * 500)
                self.assertEqual(youtube.cache_size(), 1500)
                self.assertEqual(youtube.clear_cache(keep=str(f / "a-1080p.mp4")), (1, 500))
                self.assertTrue((f / "a-1080p.mp4").exists())

    def test_parse_url(self):
        v = "dQw4w9WgXcQ"
        for u in (f"https://www.youtube.com/watch?v={v}", f"youtube.com/watch?v={v}&list=PLabc123", f"https://youtu.be/{v}?t=5",
                  f"https://www.youtube.com/shorts/{v}", f"https://m.youtube.com/live/{v}", f"https://music.youtube.com/watch?v={v}"):
            self.assertEqual(youtube.parse_url(u), ("video", v), u)
        self.assertEqual(youtube.parse_url("https://www.youtube.com/playlist?list=PLabc123"), ("playlist", "PLabc123"))
        self.assertEqual(youtube.parse_url("https://www.youtube.com/channel/UCabc_123/videos"), ("channel", "UCabc_123"))
        self.assertEqual(youtube.parse_url("https://www.youtube.com/@Funhaus/videos"), ("channel", "https://www.youtube.com/@Funhaus"))
        for t in ("wheelhaus playlist", "https://example.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/", ""):
            self.assertEqual(youtube.parse_url(t), ("", ""), t)

    def test_playlists(self):
        p = youtube.norm_playlist({"id": "PLabc123", "title": "Best of", "thumbnails": [{"url": "https://i.ytimg.com/a.jpg"}], "playlist_count": 12})
        self.assertEqual((p["id"], p["title"], p["count"], p["thumb"]), ("PLabc123", "Best of", 12, "https://i.ytimg.com/a.jpg"))
        self.assertEqual(youtube.norm_playlist({"url": "https://www.youtube.com/playlist?list=PLxyz-9", "title": "X"})["id"], "PLxyz-9")
        self.assertIsNone(youtube.norm_playlist({"id": "UCsomechannel", "title": "not a playlist"}))
        calls = []

        def run(args):
            calls.append(args)
            return json.dumps({"title": "My list", "entries": [{"id": "abcdefghijk", "title": "One"}, {"id": "bcdefghijkl", "title": "Two"}]})
        d = youtube.YtDlp(exe="yt-dlp", runner=run).playlist("PLabc123")
        self.assertEqual([v["id"] for v in d["videos"]], ["abcdefghijk", "bcdefghijkl"])
        self.assertEqual(d["title"], "My list")
        self.assertTrue(any("list=PLabc123" in a for a in calls[0]))

    def test_resume_positions(self):
        with tempfile.TemporaryDirectory() as d:
            st = youtube.Store(Path(d))
            st.set_position("a", 5, 600)                 # just started: nothing kept
            self.assertEqual(st.resume_at("a"), 0.0)
            st.set_position("a", 125.4, 600)
            self.assertEqual(st.resume_at("a"), 125.4)
            st.set_position("a", 3, 600)                 # still jumping to the saved spot: keep it
            self.assertEqual(st.resume_at("a"), 125.4)
            self.assertEqual(youtube.Store(Path(d)).resume_at("a"), 125.4)    # survives a restart
            st.set_position("a", 590, 600)               # watched to the end: forgotten
            self.assertEqual(st.resume_at("a"), 0.0)
            st.set_position("b", 50, 600)
            st.forget("b")
            self.assertEqual(st.resume_at("b"), 0.0)

    def test_levels_from(self):
        d = {"duration": 100, "formats": [
            {"vcodec": "none", "acodec": "mp4a", "ext": "m4a", "protocol": "https", "filesize": 1_000_000},
            {"vcodec": "avc1", "acodec": "none", "width": 1920, "height": 1080, "protocol": "https", "filesize": 50_000_000},
            {"vcodec": "avc1", "acodec": "none", "width": 480, "height": 854, "protocol": "https", "filesize": 9_000_000}]}
        lv = {l["res"]: l["size"] for l in youtube.levels_from(d)}
        self.assertEqual(lv.get(1080), 51_000_000)
        self.assertEqual(lv.get(480), 10_000_000)


class YoutubeTests(unittest.TestCase):
    def test_rss_and_feed(self):
        v = youtube.parse_rss(RSS)
        self.assertEqual([x["id"] for x in v], ["AAAAAAAAAAA", "BBBBBBBBBBB"])
        self.assertEqual((v[0]["views"], v[0]["channelId"], v[0]["thumb"]), (1234, "UCabc", "http://t/1.jpg"))
        other = [{"id": "CCCCCCCCCCC", "published": v[0]["published"] + 10, "title": "", "channel": "", "channelId": "", "thumb": "", "dur": 0, "views": 0, "live": False}]
        feed = youtube.subscription_feed([{"id": "UCabc"}, {"id": "UCbad"}],
                                         opener=lambda u: RSS if "UCabc" in u else (_ for _ in ()).throw(OSError("down")))
        self.assertEqual(len(feed), 2)
        self.assertEqual(youtube.merge_feed([v, other])[0]["id"], "CCCCCCCCCCC")

    def test_takeout(self):
        rows = youtube.parse_takeout("\ufeffChannel ID,Channel URL,Channel title\n"
                                     "UC" + "a" * 22 + ",http://www.youtube.com/channel/UC" + "a" * 22 + ",One\n"
                                     "bad,http://x,Nope\n"
                                     "UC" + "b" * 22 + ",http://x,\"Two, Inc\"\n")
        self.assertEqual(rows, [("UC" + "a" * 22, "One"), ("UC" + "b" * 22, "Two, Inc")])

    def test_avatar(self):
        html = '<head><meta property="og:image" content="https://yt3.googleusercontent.com/a=s900&amp;b=1"></head>'
        self.assertEqual(youtube.fetch_avatar("UC1", opener=lambda u: html), "https://yt3.googleusercontent.com/a=s900&b=1")
        self.assertEqual(youtube.parse_avatar("<html></html>"), "")

    def test_stream_proxy_fetches_in_pieces(self):
        import re as _re
        import threading
        import urllib.request
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from marquee.core.streamproxy import StreamProxy
        data = bytes(range(50))
        seen = []

        class Up(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_GET(self):
                rng = self.headers.get("Range")
                seen.append((self.headers.get("User-Agent"), rng))
                if self.headers.get("User-Agent") != "Fake/1" or not rng:         # like YouTube: no range, no stream
                    self.send_response(403); self.send_header("Content-Length", "0"); self.end_headers(); return
                a, b = map(int, _re.match(r"bytes=(\d+)-(\d+)", rng).groups())
                b = min(b, len(data) - 1)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {a}-{b}/{len(data)}")
                self.send_header("Content-Length", str(b - a + 1))
                self.end_headers()
                self.wfile.write(data[a:b + 1])
        up = HTTPServer(("127.0.0.1", 0), Up)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        proxy = StreamProxy()
        try:
            addr = proxy.add(f"http://127.0.0.1:{up.server_address[1]}/v", {"User-Agent": "Fake/1"})
            proxy._server.chunk = 16
            with urllib.request.urlopen(addr, timeout=5) as r:                      # the player's plain GET, no Range
                self.assertEqual((r.status, r.read()), (200, data))
            self.assertGreaterEqual(len(seen), 4)                                   # 50 bytes in pieces of 16
            req = urllib.request.Request(addr, headers={"Range": "bytes=20-39"})
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertEqual((r.status, r.read()), (206, data[20:40]))
        finally:
            proxy.stop(); up.shutdown()

    def test_stream_proxy_serves_a_growing_file(self):
        import tempfile
        import threading
        import time
        import urllib.request
        from marquee.core.streamproxy import StreamProxy

        class Proc:
            def __init__(self): self.done = False
            def poll(self): return 0 if self.done else None
        d = tempfile.mkdtemp()
        path = os.path.join(d, "v.mp4")
        data = bytes(range(200))
        with open(path, "wb") as f:
            f.write(data[:50])
        proc = Proc()

        def grow():
            time.sleep(0.3)
            with open(path, "ab") as f:
                f.write(data[50:])
            proc.done = True
        threading.Thread(target=grow, daemon=True).start()
        px = StreamProxy()
        try:
            addr = px.add_file(path, 200, proc)
            req = urllib.request.Request(addr, headers={"Range": "bytes=40-149"})        # runs past what exists yet
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertEqual((r.status, r.read()), (206, data[40:150]))
            with urllib.request.urlopen(addr, timeout=5) as r:
                self.assertEqual(r.read(), data)
        finally:
            px.stop()

    def test_probe_rejects_stub_streams(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class Up(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_GET(self):
                total = 345008 if self.path == "/stub" else 45_000_000
                self.send_response(206)
                self.send_header("Content-Range", f"bytes 0-9/{total}")
                self.send_header("Content-Length", "10")
                self.end_headers()
                self.wfile.write(b"0123456789")
        up = HTTPServer(("127.0.0.1", 0), Up)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{up.server_address[1]}"
            mk = lambda path: {"id": "x", "url": base + path, "headers": {}, "dur": 841.0, "size_hint": 0}
            self.assertFalse(youtube.probe_stream(mk("/stub")))
            self.assertTrue(youtube.probe_stream(mk("/real")))
        finally:
            up.shutdown()

    def test_curl_wrapper_streams(self):
        from marquee.core import gvfetch

        class R:
            status_code = 206
            headers = {"Content-Range": "bytes 0-5/6"}

            def iter_content(self, chunk_size=0):
                yield b"abc"
                yield b"def"

            def close(self): self.closed = True
        r = gvfetch._Curl(R())
        self.assertEqual((r.status, r.read(2), r.read(), r.read(1)), (206, b"ab", b"cdef", b""))

    def test_entries(self):
        e = youtube.norm_video({"id": "abcdefghijk", "title": "T", "channel": "C", "channel_id": "UC1", "duration": 61,
                                "view_count": 5, "thumbnails": [{"url": "http://x/s.jpg"}, {"url": "http://x/b.jpg"}]})
        self.assertEqual((e["thumb"], e["dur"], e["channelId"]), ("http://x/b.jpg", 61.0, "UC1"))
        self.assertEqual(youtube.norm_video({"url": "https://www.youtube.com/watch?v=abcdefghijk"})["id"], "abcdefghijk")
        self.assertIsNone(youtube.norm_video({"id": "UCchannel", "title": "x"}))
        self.assertEqual(youtube.norm_channel({"id": "UCxyz", "title": "Band"})["name"], "Band")
        self.assertIsNone(youtube.norm_channel({"id": "notachannel"}))

    def test_ytdlp(self):
        calls = []

        def runner(cmd):
            calls.append(cmd)
            if "ytsearch3:blink" in cmd:
                return '{"entries": [{"id": "abcdefghijk", "title": "A"}, {"id": "bad"}]}'
            if any("watch?v=" in c for c in cmd):
                return '{"url": "https://s/v.mp4", "title": "Vid", "duration": 90, "channel": "Ch"}'
            return "{}"
        yt = youtube.YtDlp("yt-dlp", runner)
        yt.retry_wait = 0
        self.assertEqual([v["id"] for v in yt.search("blink", 3)], ["abcdefghijk"])
        r = yt.resolve("abcdefghijk", probe=lambda i: True)
        self.assertEqual((r["url"], r["dur"]), ("https://s/v.mp4", 90.0))
        n = []
        yt.resolve("abcdefghijk", probe=lambda i: n.append(1) or len(n) == 3)       # refused twice, third way works
        self.assertTrue(any("player_client=android_vr" in " ".join(c) for c in calls))
        with self.assertRaises(RuntimeError):
            yt.resolve("abcdefghijk", probe=lambda i: False)
        with self.assertRaises(RuntimeError):
            yt.recommended()                      # no browser chosen
        yt.browser = "firefox"
        yt.recommended()
        self.assertIn("--cookies-from-browser", calls[-1])
        self.assertIn(":ytrec", calls[-1])

    def test_store_and_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = youtube.Store(Path(tmp))
            s.subscribe("UC1", "Zed")
            s.subscribe("UC2", "Alpha")
            s.subscribe("UC1", "dup")
            self.assertEqual([x["name"] for x in s.subs], ["Alpha", "Zed"])
            s.watched({"id": "AAAAAAAAAAA", "title": "a"}, 1)
            s.watched({"id": "BBBBBBBBBBB", "title": "b"}, 2)
            s.watched({"id": "AAAAAAAAAAA", "title": "a"}, 3)
            s.set_browser("firefox")
            again = youtube.Store(Path(tmp))
            self.assertEqual([h["id"] for h in again.history], ["AAAAAAAAAAA", "BBBBBBBBBBB"])
            self.assertEqual((again.browser, len(again.subs)), ("firefox", 2))
            again.set_browser("netscape")
            self.assertEqual(again.browser, "")
        self.assertEqual(youtube.clock(75), "1:15")
        self.assertEqual(youtube.clock(3725), "1:02:05")
        self.assertEqual(youtube.ago(1000, 1000 + 3 * 86400), "3 days ago")
        self.assertEqual(youtube.short_count(1_250_000), "1.2M")
        self.assertEqual(youtube.short_count(999), "999")


class SampleTests(unittest.TestCase):
    def test_wav_header(self):
        wav = samples.synth_wav(1)
        self.assertEqual(wav[:4], b"RIFF")
        self.assertEqual(wav[8:12], b"WAVE")
        self.assertEqual(len(wav), 44 + samples.RATE * samples.SECONDS * 2)
        self.assertEqual(struct.unpack("<I", wav[4:8])[0], len(wav) - 8)


if __name__ == "__main__":
    unittest.main()
