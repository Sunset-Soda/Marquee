import os
import struct
import sys
import random
import tempfile
import urllib.error
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marquee.core import hls, twitch, podcasts, artwork, desktop, iptv, youtube, ytmusic, library, lyrics, opensubs, palette, parse, playqueue, samples, shorts, spectrum, sponsorblock, subsync, subtitles, tv  # noqa: E402
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

    def test_removed_kinds_move_to_the_nearest_kind(self):
        lib = library.Library()
        lib.add_paths([self.media], probe=False)
        lib.items[0].kind = "home"
        lib.items[1].kind = "book"
        lib.save()
        loaded = library.Library().load()
        self.assertEqual({k.id for k in library.KINDS}, {"movie", "show", "music", "podcast"})
        self.assertEqual({loaded.items[0].kind, loaded.items[1].kind}, {"movie", "podcast"})
        self.assertTrue(all(i.kind in library.KIND for i in loaded.items))

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

    def test_original_and_reboot_with_the_same_name(self):
        """Two shows called Ted: the right one is the one that has the user's episodes, even if the other scores higher."""
        class TwoTeds(FakeTvmaze):
            def __call__(self, url):
                if "/search/shows" in url:
                    return json.dumps([
                        {"score": 0.99, "show": {"id": 9, "name": "Ted", "premiered": "2026-01-01", "image": None}},
                        {"score": 0.80, "show": {"id": 1, "name": "Ted", "premiered": "2024-01-11", "image": None}}]).encode()
                if url.endswith("/shows/9/episodes"):
                    return json.dumps([{"season": 1, "number": 1, "name": "New Pilot", "airdate": "2026-01-01"}]).encode()
                if url.endswith("/shows/9/seasons"):
                    return b"[]"
                if url.endswith("/shows/1/seasons"):
                    return b"[]"
                return super().__call__(url)
        lib = self.lib()
        status, text, renamed = tv.scrub(lib, "ted", fetch=TwoTeds())
        self.assertEqual((status, text, renamed), ("ok", "Ted (2024)", 4))
        self.assertEqual(lib.shows["ted"].tvmaze_id, 1)

    def test_a_wrong_stored_match_is_corrected_on_refresh(self):
        class TwoTeds(FakeTvmaze):
            def __call__(self, url):
                if "/search/shows" in url:
                    return json.dumps([
                        {"score": 0.99, "show": {"id": 9, "name": "Ted", "premiered": "2026-01-01", "image": None}},
                        {"score": 0.80, "show": {"id": 1, "name": "Ted", "premiered": "2024-01-11", "image": None}}]).encode()
                if url.endswith("/shows/9"):
                    return json.dumps({"id": 9, "name": "Ted", "premiered": "2026-01-01", "image": None}).encode()
                if url.endswith("/shows/9/episodes"):
                    return json.dumps([{"season": 1, "number": 1, "name": "New Pilot", "airdate": "2026-01-01"}]).encode()
                if url.endswith("/seasons"):
                    return b"[]"
                return super().__call__(url)
        lib = self.lib()
        wrong = lib.ensure_show("ted", "Ted", 0)
        wrong.tvmaze_id, wrong.imdb = 9, "40197357"                  # an earlier, wrong match, with its IMDb number cached
        status, text, renamed = tv.scrub(lib, "ted", force=True, fetch=TwoTeds())
        self.assertEqual((status, lib.shows["ted"].tvmaze_id, renamed), ("ok", 1, 4))
        self.assertEqual(lib.shows["ted"].imdb, "")                   # the old match's IMDb number is not kept

    def test_compact_episode_numbers_in_a_season_folder(self):
        """Scrubs/Season 1/103 - Title.avi is episode 3, not episode 103; a folder that is not numbered that way is left alone."""
        s1 = self.media / "Scrubs" / "Season 1"
        s1.mkdir(parents=True)
        for n in ("101 - My First Day", "102 - My Mentor", "103 - My Best Friends Mistake"):
            (s1 / f"{n}.avi").write_bytes(b"x")
        i = parse.parse_path(s1 / "103 - My Best Friends Mistake.avi")
        self.assertEqual((i["season"], i["episode"], i["title"]), (1, 3, "My Best Friends Mistake"))
        daily = self.media / "Daily" / "Season 1"
        daily.mkdir(parents=True)
        for n in ("01 - A", "105 - B"):
            (daily / f"{n}.mkv").write_bytes(b"x")
        self.assertEqual(parse.parse_path(daily / "105 - B.mkv")["episode"], 105)
        s2 = self.media / "Scrubs" / "Season 2"
        s2.mkdir(parents=True)
        (s2 / "201 - My Overkill.avi").write_bytes(b"x")
        self.assertEqual(parse.parse_path(s2 / "201 - My Overkill.avi")["episode"], 1)

    def test_old_libraries_get_compact_episode_numbers_fixed_once(self):
        s1 = self.media / "Scrubs" / "Season 1"
        s1.mkdir(parents=True)
        for n in ("101 - My First Day", "103 - My Best Friends Mistake"):
            (s1 / f"{n}.avi").write_bytes(b"x")
        lib = self.lib()
        eps = sorted((i.episode for i in lib.items if i.show_key == "scrubs"))
        self.assertEqual(eps, [1, 3])
        for i in lib.items:
            if i.show_key == "scrubs":                                   # as an older version had stored them
                i.episode += 100 * (i.season or 1) if i.episode < 100 else 0
                i.sub_tried = True
        lib.prefs.pop("ep_compact_v1", None)
        self.assertEqual(lib.repair_episode_numbers(), 2)
        self.assertEqual(sorted(i.episode for i in lib.items if i.show_key == "scrubs"), [1, 3])
        self.assertFalse(any(i.sub_tried for i in lib.items if i.show_key == "scrubs"))
        self.assertEqual(lib.repair_episode_numbers(), 0)                # only once

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


class WikiPosterTests(unittest.TestCase):
    def test_reads_the_poster_from_the_film_infobox(self):
        from marquee.core import artwork, net
        item = library.Item(id="m1", title="Furious 7", sub="2015", kind="movie")
        seen = []

        def get(url, *a, **k):
            seen.append(url)
            if "list=search" in url:
                return json.dumps({"query": {"search": [{"title": "Furious 7 (soundtrack)"}, {"title": "Furious 7"}]}}).encode()
            body = "{{Infobox film\n| name = Furious 7\n| image = Furious 7 poster.jpg\n| released = {{Film date|2015|04|01}}\n}}"
            if "Soundtrack" in url or "soundtrack" in url:
                body = "{{Infobox album\n| name = x\n}}"
            return json.dumps({"query": {"pages": [{"revisions": [{"slots": {"main": {"content": body}}}]}]}}).encode()
        old = net.get
        net.get = get
        try:
            url = artwork.wiki_poster(item)
        finally:
            net.get = old
        self.assertTrue(url.endswith("Special:FilePath/Furious_7_poster.jpg?width=600"), url)


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

    def _subdl_request(self, calls, zipped=True, subs=None):
        import io, zipfile
        srt = "1\n00:00:01,000 --> 00:00:02,000\nFrom Subdl\n"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Ted.S01E02.srt", srt)
        listing = {"status": True, "subtitles": subs if subs is not None else [
            {"release_name": "Ted.S01E02.1080p", "url": "/subtitle/1-2.zip", "season": 1, "episode": 2, "hi": False},
            {"release_name": "Ted.S01E03.1080p", "url": "/subtitle/9-9.zip", "season": 1, "episode": 3},
            {"release_name": "Ted.S01.Complete", "url": "/subtitle/5-5.zip", "full_season": True}]}

        def request(method, url, body=None, headers=None, timeout=0):
            calls.append(url)
            if "api.subdl.com" in url:
                return json.dumps(listing).encode()
            if "dl.subdl.com" in url:
                return buf.getvalue()
            raise urllib.error.HTTPError(url, 404, "n", {}, None)      # OpenSubtitles (and anything else): nothing
        return request

    def test_subdl_is_the_backup_when_opensubtitles_has_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._env(d)
            lib, it = self._video(d)
            calls = []
            os_fake = self.fake([], results=[])
            sd = self._subdl_request(calls)

            def request(method, url, body=None, headers=None, timeout=0):
                return sd(method, url, body, headers) if "subdl.com" in url else os_fake(method, url, body, headers)
            creds = opensubs.Credentials(api_key="KEY", subdl_key="SDKEY", language="en")
            status, msg, path = opensubs.fetch_for_item(lib, it, creds, request)
            self.assertEqual((status, msg), ("ok", "Downloaded English subtitles from Subdl"))
            self.assertIn("From Subdl", Path(path).read_text())
            self.assertTrue(any("dl.subdl.com/subtitle/1-2.zip" in c for c in calls))       # the right episode, not E03 or the pack
            self.assertFalse(any("9-9" in c or "5-5" in c for c in calls))
            q = next(c for c in calls if "api.subdl.com" in c)
            self.assertIn("api_key=SDKEY", q)
            self.assertIn("season_number=1", q)
            self.assertIn("episode_number=2", q)

    def test_subdl_takes_over_when_opensubtitles_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._env(d)
            lib, it = self._video(d)
            sd = self._subdl_request([])

            def request(method, url, body=None, headers=None, timeout=0):
                if "subdl.com" in url:
                    return sd(method, url, body, headers)
                raise urllib.error.HTTPError(url, 406, "limit", {}, None)           # today's allowance used up
            creds = opensubs.Credentials(api_key="KEY", subdl_key="SDKEY", language="en")
            self.assertEqual(opensubs.fetch_for_item(lib, it, creds, request)[0], "ok")
            self.assertEqual(opensubs.fetch_for_item(lib, it, opensubs.Credentials(api_key="KEY"), request)[0], "error")   # no backup: say so

    def test_subdl_alone_is_enough_and_wrong_episodes_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._env(d)
            lib, it = self._video(d)
            only_wrong = [{"release_name": "Ted.S01E03", "url": "/subtitle/9-9.zip", "season": 1, "episode": 3}]
            creds = opensubs.Credentials(subdl_key="SDKEY", language="en")
            self.assertTrue(creds.ready)
            self.assertEqual(opensubs.fetch_for_item(lib, it, creds, self._subdl_request([], subs=only_wrong))[0], "none")

    def test_a_stale_imdb_number_is_looked_up_again(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self._env(d)
            lib, it = self._video(d)
            info = lib.ensure_show(it.show_key, "Ted", 0)
            info.tvmaze_id, info.imdb, info.imdb_of = 1, "40197357", 0       # cached before matches were tracked
            asked = []

            def request(method, url, body=None, headers=None, timeout=0):
                asked.append(url)
                return b'{"externals": {"imdb": "tt0285403"}}'
            self.assertEqual(opensubs.imdb_id(lib, it, request), "0285403")
            self.assertEqual(opensubs.imdb_id(lib, it, request), "0285403")
            self.assertEqual(len(asked), 1)                                    # and then it is remembered

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


class YtMusicTests(unittest.TestCase):
    THUMBS = [{"url": "https://lh3.googleusercontent.com/abc=w60-h60-l90-rj", "width": 60, "height": 60},
              {"url": "https://lh3.googleusercontent.com/abc=w226-h226-l90-rj", "width": 226, "height": 226}]
    ALBUM = {"title": "Revival", "type": "Album", "thumbnails": THUMBS, "year": "2017", "duration_seconds": 600,
             "audioPlaylistId": "OLAK5uy_x", "artists": [{"name": "Eminem", "id": "UCem"}],
             "tracks": [{"videoId": "vid00000001", "title": "Walk On Water", "artists": [{"name": "Eminem", "id": "UCem"}, {"name": "Beyonce", "id": "UCb"}],
                         "album": "Revival", "thumbnails": None, "isAvailable": True, "duration": "5:03", "duration_seconds": 303, "trackNumber": 0},
                        {"videoId": "vid00000002", "title": "Believe", "artists": [{"name": "Eminem", "id": "UCem"}], "album": "Revival",
                         "isAvailable": True, "duration": "5:15"},
                        {"videoId": None, "title": "Greyed out", "artists": []},
                        {"videoId": "vid00000004", "title": "Gone", "isAvailable": False}]}

    def test_art_picks_biggest_and_resizes(self):
        self.assertEqual(ytmusic.art(self.THUMBS), "https://lh3.googleusercontent.com/abc=w544-h544-l90-rj")
        self.assertEqual(ytmusic.art(self.THUMBS, 100), "https://lh3.googleusercontent.com/abc=w100-h100-l90-rj")
        self.assertEqual(ytmusic.art([{"url": "https://i.ytimg.com/vi/x/hq.jpg", "width": 480, "height": 360}]), "https://i.ytimg.com/vi/x/hq.jpg")
        self.assertEqual(ytmusic.art(None), "")
        self.assertEqual(ytmusic.art([{"nope": 1}, "junk"]), "")

    def test_durations(self):
        self.assertEqual(ytmusic.seconds({"duration_seconds": 259}), 259.0)
        self.assertEqual(ytmusic.seconds({"duration": "4:19"}), 259.0)
        self.assertEqual(ytmusic.seconds({"duration": "1:02:03"}), 3723.0)
        self.assertEqual(ytmusic.seconds({"duration": "soon"}), 0.0)
        self.assertEqual(ytmusic.seconds({}), 0.0)

    def test_album_page(self):
        a = ytmusic.shape_album(self.ALBUM, "MPREb_1")
        self.assertEqual((a["title"], a["artist"], a["year"], a["kind"]), ("Revival", "Eminem", "2017", "Album"))
        self.assertEqual([t["title"] for t in a["tracks"]], ["Walk On Water", "Believe"])     # no id and unavailable ones are dropped
        self.assertEqual([t["n"] for t in a["tracks"]], [1, 2])                               # numbered in order shown
        t = a["tracks"][0]
        self.assertEqual((t["id"], t["vid"], t["artist"], t["artistId"], t["albumId"], t["dur"]),
                         ("ytm:vid00000001", "vid00000001", "Eminem, Beyonce", "UCem", "MPREb_1", 303.0))
        self.assertEqual(t["cover"], a["cover"])                                              # songs wear the album's cover
        self.assertEqual(a["tracks"][1]["dur"], 315.0)
        self.assertEqual(a["dur"], 600.0)

    def test_search_rows(self):
        song = ytmusic.norm_track({"videoId": "vid00000009", "title": "Wonderwall", "artists": [{"name": "Oasis", "id": "UCo"}],
                                   "album": {"name": "Morning Glory", "id": "MPREb_9"}, "duration_seconds": 259, "thumbnails": self.THUMBS})
        self.assertEqual((song["artist"], song["album"], song["albumId"], song["cover"].endswith("w544-h544-l90-rj")),
                         ("Oasis", "Morning Glory", "MPREb_9", True))
        alb = ytmusic.norm_album({"browseId": "MPREb_1", "playlistId": "OLAK5uy_k", "title": "Morning Glory", "type": "Album",
                                  "artist": "Oasis", "year": "1995", "thumbnails": self.THUMBS})
        self.assertEqual((alb["id"], alb["artist"], alb["year"], alb["playlistId"]), ("MPREb_1", "Oasis", "1995", "OLAK5uy_k"))
        self.assertIsNone(ytmusic.norm_album({"title": "no id"}))
        art = ytmusic.norm_artist({"browseId": "UCo", "artist": "Oasis", "thumbnails": self.THUMBS})
        self.assertEqual((art["id"], art["name"]), ("UCo", "Oasis"))
        pl = ytmusic.norm_playlist({"browseId": "VLPLabc", "title": "Mix", "author": "Tate", "itemCount": "1,174"})
        self.assertEqual((pl["id"], pl["author"], pl["count"]), ("PLabc", "Tate", 1174))
        self.assertEqual(ytmusic.norm_playlist({"browseId": "VLPLx", "title": "t", "itemCount": None})["count"], 0)

    def test_artist_page(self):
        raw = {"name": "Oasis", "thumbnails": self.THUMBS, "monthlyListeners": "29.1M", "radioId": "RDEM",
               "songs": {"browseId": "VLPLsongs", "results": [{"videoId": "vid00000011", "title": "Wonderwall", "artist": "Oasis", "album": "Morning Glory",
                                                              "thumbnails": self.THUMBS}]},
               "albums": {"results": [{"browseId": "MPREb_1", "title": "Morning Glory", "year": "1995", "thumbnails": self.THUMBS}]},
               "singles": {"results": []}}
        a = ytmusic.shape_artist(raw, "UCo")
        self.assertEqual((a["name"], a["listeners"], a["songsBrowseId"]), ("Oasis", "29.1M", "VLPLsongs"))
        self.assertEqual((a["songs"][0]["artist"], a["songs"][0]["album"], a["songs"][0]["n"]), ("Oasis", "Morning Glory", 1))
        self.assertEqual((a["albums"][0]["artist"], a["albums"][0]["year"]), ("Oasis", "1995"))
        self.assertEqual(ytmusic.shape_artist({}, "UCx")["songs"], [])                         # an empty page still has every key

    def test_playlist_page(self):
        raw = {"title": "Road trip", "author": {"name": "Me", "id": "x"}, "thumbnails": self.THUMBS, "trackCount": 2,
               "tracks": [{"videoId": "vid00000021", "title": "A", "artists": [{"name": "X", "id": "u"}], "duration_seconds": 100},
                          {"videoId": "vid00000022", "title": "B", "artists": [], "duration_seconds": 50}]}
        p = ytmusic.shape_playlist(raw, "PLroad")
        self.assertEqual((p["title"], p["author"], p["count"], p["dur"]), ("Road trip", "Me", 2, 150.0))
        self.assertEqual([t["n"] for t in p["tracks"]], [1, 2])

    def test_client_wraps_the_library(self):
        calls = []

        class Fake:
            def search(self, q, filter=None, limit=20):
                calls.append((q, filter))
                if filter == "albums":
                    return [{"browseId": "MPREb_1", "title": "A", "artist": "Z"}, "junk", {"title": "no id"}]
                return [{"videoId": "vid00000031", "title": "S", "artists": [{"name": "Z"}]}]

            def get_album(self, b):
                return {"title": "A", "tracks": []}

            def get_artist(self, c):
                raise ConnectionError("offline")
        c = ytmusic.YtMusic(factory=Fake)
        self.assertEqual([x["id"] for x in c.search("  z ", "songs")], ["ytm:vid00000031"])
        self.assertEqual([x["id"] for x in c.search("z", "albums")], ["MPREb_1"])               # bad rows skipped, good ones kept
        self.assertEqual(calls, [("z", "songs"), ("z", "albums")])
        self.assertEqual(c.search("   "), [])
        self.assertEqual(c.album("MPREb_1")["title"], "A")
        with self.assertRaises(RuntimeError) as cm:
            c.artist("UCx")
        self.assertIn("reach YouTube Music", str(cm.exception))

    def test_vendored_library_loads(self):
        c = ytmusic.YtMusic()
        self.assertTrue(hasattr(c._client(), "search"))

    def test_recent_plays(self):
        with tempfile.TemporaryDirectory() as d:
            st = ytmusic.Store(Path(d))
            t = lambda n: {"id": f"ytm:vid0000000{n}", "vid": f"vid0000000{n}", "title": f"T{n}", "artist": "A", "cover": "c", "extra": "dropped"}
            for n in (1, 2, 3, 1):
                st.played(t(n))
            self.assertEqual([r["title"] for r in st.recent], ["T1", "T3", "T2"])                # newest first, no repeats
            self.assertNotIn("extra", st.recent[0])
            self.assertEqual([r["title"] for r in ytmusic.Store(Path(d)).recent], ["T1", "T3", "T2"])
            st.clear_recent()
            self.assertEqual(ytmusic.Store(Path(d)).recent, [])

    def test_lyrics_for_a_track(self):
        with tempfile.TemporaryDirectory() as d:
            from unittest import mock
            asked = []

            def request(url, *a, **k):
                asked.append(url)
                return json.dumps([{"syncedLyrics": "[00:01.00] hello\n[00:03.00] world", "duration": 100}]).encode()
            with mock.patch.object(lyrics, "cache_dir", return_value=Path(d)):
                got = lyrics.lookup_track("Artist", "Title", "Album", 100, request=request)
                self.assertTrue(got and got.synced and len(got.lines) == 2)
                before = len(asked)
                again = lyrics.lookup_track("Artist", "Title", "Album", 100, request=lambda *a, **k: self.fail("asked twice"))
                self.assertTrue(again and again.synced)
                self.assertGreaterEqual(before, 1)
                self.assertEqual(len(asked), before)                                         # the second lookup came from the cache

    def test_service_lyrics_timed_and_plain(self):
        class Api:
            def get_watch_playlist(self, videoId, limit=1):
                return {"lyrics": "MPLYt_x"} if videoId == "abcdefghijk" else {"lyrics": None}

            def get_lyrics(self, browse, timestamps=False):
                return {"lyrics": [{"text": "Hello", "start_time": 9200, "end_time": 10000},
                                   {"text": "World", "start_time": 12500, "end_time": 13000}], "hasTimestamps": True}
        yt = ytmusic.YtMusic(factory=Api)
        got = yt.lyrics("abcdefghijk")
        self.assertTrue(got.synced)
        self.assertEqual([(l.t, l.text) for l in got.lines], [(9.2, "Hello"), (12.5, "World")])
        self.assertIsNone(yt.lyrics("zzzzzzzzzzz"))

    def test_service_lyrics_come_before_lrclib(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as d, mock.patch.object(lyrics, "cache_dir", return_value=Path(d)):
            own = lyrics.Lyrics([lyrics.Line(1.0, "mine")], True, "youtube music")
            got = lyrics.lookup_track("A", "T", "", 100, request=lambda *a, **k: self.fail("asked lrclib"), first=lambda: own)
            self.assertEqual(got.lines[0].text, "mine")
            plain = lyrics.Lyrics([lyrics.Line(-1.0, "plain")], False, "youtube music")
            timed = json.dumps([{"syncedLyrics": "[00:01.00] timed\n[00:03.00] lines", "duration": 100}]).encode()
            got = lyrics.lookup_track("C", "T", "", 100, request=lambda u: timed, first=lambda: plain)
            self.assertTrue(got.synced)                                                          # timed LRCLIB beats plain text
            got = lyrics.lookup_track("D", "T", "", 100, request=lambda u: b"[]", first=lambda: plain)
            self.assertEqual(got.lines[0].text, "plain")                                         # but plain beats nothing
            miss = lyrics.lookup_track("B", "T", "", 100, request=lambda u: b"[]", first=lambda: None)   # falls back to lrclib
            self.assertIsNone(miss)

    def test_lyrics_remember_the_recording_length(self):
        from unittest import mock
        timed = json.dumps([{"syncedLyrics": "[00:01.00] a\n[00:03.00] b", "duration": 212, "trackName": "T", "artistName": "A"}]).encode()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(lyrics, "cache_dir", return_value=Path(d)):
            got = lyrics.lookup_track("A", "T", "", 212, request=lambda u: timed)
            self.assertEqual(got.duration, 212.0)
            again = lyrics.lookup_track("A", "T", "", 212, request=lambda u: self.fail("asked twice"))
            self.assertEqual(again.duration, 212.0)                                  # and it survives the cache

    def test_refine_finds_the_version_that_fits_the_audio(self):
        from unittest import mock
        rows = json.dumps([
            {"syncedLyrics": "[00:25.00] a\n[00:27.00] b", "duration": 212, "trackName": "T", "artistName": "A"},
            {"syncedLyrics": "[00:12.00] a\n[00:14.00] b", "duration": 199, "trackName": "T", "artistName": "A"}]).encode()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(lyrics, "cache_dir", return_value=Path(d)):
            got = lyrics.refine("A", "T", "", 199.4, request=lambda u: b"null" if "/get" in u else rows)
            self.assertEqual((got.duration, got.lines[0].t), (199.0, 12.0))
            self.assertIsNone(lyrics.refine("A", "T", "", 150.0, request=lambda u: b"null" if "/get" in u else rows))   # nothing close

    def test_audio_stream_lookup(self):
        seen = []

        def run(cmd):
            seen.append(cmd)
            client = next((c.split("player_client=")[1] for c in cmd if "player_client=" in c), "")
            if client == "":
                return json.dumps({"url": "https://s/bad", "title": "Song", "duration": 200, "ext": "m4a", "abr": 128})
            return json.dumps({"formats": [{"url": "https://s/video", "vcodec": "avc1", "acodec": "none"},
                                           {"url": "https://s/audio", "vcodec": "none", "acodec": "mp4a", "abr": 128}],
                               "title": "Song", "duration": 200, "artist": "Band"})
        dl = youtube.YtDlp(exe="yt-dlp", runner=run)
        dl.retry_wait = 0
        got = dl.resolve_audio("abcdefghijk", probe=lambda i: i["url"] != "https://s/bad")
        self.assertEqual((got["url"], got["dur"], got["channel"], got["audio"]), ("https://s/audio", 200.0, "Band", True))
        self.assertTrue(all("music.youtube.com/watch?v=abcdefghijk" in c[-1] for c in seen))
        self.assertTrue(all("bestaudio" in c[c.index("-f") + 1] for c in seen))

        # the client that worked is tried first next time
        seen.clear()
        self.assertEqual(dl._audio_good, "android_vr")
        dl.resolve_audio("abcdefghijk", probe=lambda i: i["url"] != "https://s/bad")
        self.assertIn("player_client=android_vr", " ".join(seen[0]))

    def test_peek_next(self):
        q = playqueue.PlayQueue()
        q.start(["a", "b", "c"], "b")
        self.assertEqual(q.peek(), "c")
        self.assertEqual(q.current, "b")                 # peeking doesn't move
        q.next()
        self.assertEqual(q.peek(), "")
        q.set_repeat("one")
        self.assertEqual(q.peek(), "")


class SponsorBlockTests(unittest.TestCase):
    def test_segments_asks_by_hash_prefix_and_keeps_our_video(self):
        import hashlib
        asked = []

        def request(url):
            asked.append(url)
            return json.dumps([
                {"videoID": "other", "segments": [{"segment": [1, 2], "category": "sponsor", "actionType": "skip"}]},
                {"videoID": "abcdefghijk", "segments": [
                    {"segment": [30.5, 60], "category": "sponsor", "actionType": "skip"},
                    {"segment": [0, 5], "category": "intro", "actionType": "skip"},          # not a category we asked for
                    {"segment": [90, 95], "category": "selfpromo", "actionType": "mute"},   # not a skip
                    {"segment": [70, 70], "category": "sponsor", "actionType": "skip"}]}])  # empty
        got = sponsorblock.segments("abcdefghijk", request=request)
        self.assertEqual(got, [(30.5, 60.0, "sponsor")])
        self.assertIn(hashlib.sha256(b"abcdefghijk").hexdigest()[:4], asked[0])
        self.assertNotIn("abcdefghijk", asked[0])                 # the id itself is never sent

    def test_no_segments_when_unknown_or_unreachable(self):
        import urllib.error

        def notfound(url):
            raise urllib.error.HTTPError(url, 404, "n", {}, None)

        def down(url):
            raise OSError("offline")
        self.assertEqual(sponsorblock.segments("abcdefghijk", request=notfound), [])
        self.assertEqual(sponsorblock.segments("abcdefghijk", request=down), [])
        self.assertEqual(sponsorblock.segments("", request=down), [])

    def test_only_chosen_categories_are_asked_for(self):
        asked = []
        sponsorblock.segments("abcdefghijk", ("outro", "bogus"), request=lambda u: asked.append(u) or b"[]")
        self.assertIn("outro", asked[0])
        self.assertNotIn("bogus", asked[0])
        self.assertEqual(sponsorblock.segments("abcdefghijk", (), request=lambda u: self.fail("asked with nothing chosen")), [])

    def test_merge_joins_overlaps(self):
        got = sponsorblock.merge([(50, 60, "sponsor"), (10, 20, "sponsor"), (20.2, 30, "selfpromo"), (55, 70, "sponsor")])
        self.assertEqual(got, [(10, 30, "sponsor"), (50, 70, "sponsor")])


class ShortDiscoveryTests(unittest.TestCase):
    class Dlp:
        def search(self, q, n):
            return [{"id": f"{abs(hash(q)) % 10**6:06d}{i:05d}"[:11], "title": q, "channel": "Other", "channelId": "UCother", "dur": 20.0, "live": False}
                    for i in range(n)]

    def test_topics_come_from_history(self):
        hist = [{"title": "How to make the BEST sourdough bread at home!"}]
        t = shorts.topics_from(hist, random.Random(1), 1)
        self.assertEqual(t, ["make sourdough bread home"])
        self.assertEqual(shorts.topics_from([], random.Random(1)), [])

    def test_for_you_mixes_subscriptions_and_new_finds(self):
        subs = [{"id": "UCsub", "name": "Sub"}]
        d = self.Dlp()
        d._json = lambda *a: {"channel": "Sub", "entries": [{"id": f"sub{i:08d}"[:11], "title": "s", "duration": 20} for i in range(6)]}
        rows = shorts.for_you(d, subs, [{"title": "Cats playing piano"}], random.Random(3))
        chans = {r["channel"] for r in rows}
        self.assertIn("Sub", chans)
        self.assertIn("Other", chans)                              # not only channels you follow
        self.assertEqual(len({r["id"] for r in rows}), len(rows))  # no repeats

    def test_for_you_follows_the_accounts_recommendations(self):
        d = self.Dlp()
        d._json = lambda *a: {"channel": "Rec Chan", "entries": [{"id": f"rec{i:08d}"[:11], "title": "r", "duration": 20} for i in range(4)]}
        recs = [{"id": "short000001", "title": "Quick tip", "channel": "X", "channelId": "UCrec", "dur": 25.0, "live": False},
                {"id": "long0000001", "title": "Deep dive into woodworking joints", "channel": "Y", "channelId": "UCrec2", "dur": 900.0, "live": False}]
        rows = shorts.for_you(d, [], [], random.Random(3), recs=recs)
        ids = [r["id"] for r in rows]
        self.assertEqual(ids[0], "short000001")                    # a Short in the home feed comes first, as it is
        self.assertNotIn("long0000001", ids)                       # a long video is not a Short
        self.assertTrue(any(r["channel"] == "Rec Chan" for r in rows))   # the channels the feed shows

    def test_for_you_without_subscriptions(self):
        rows = shorts.for_you(self.Dlp(), [], [], random.Random(3))
        self.assertTrue(rows)
        self.assertTrue(all(r["channel"] == "Other" for r in rows))


class ShortFetchTests(unittest.TestCase):
    def setUp(self):
        self.d = youtube.YtDlp.__new__(youtube.YtDlp)
        self.calls = []
        self.d.fetch_dash = lambda vid, meta, res: (self.calls.append(("dash", res, meta["dur"])) or {"id": vid, "file": "/x", "res": res})
        self.d.resolve = lambda vid, **kw: (self.calls.append(("full", kw.get("quality"))) or {"id": vid})

    def test_chosen_size_is_used(self):
        self.assertEqual(self.d.short_level("720", 30), 720)
        self.assertEqual(self.d.short_level("1080", 30), 1080)

    def test_auto_drops_on_a_slow_connection(self):
        self.assertEqual(self.d.short_level("auto", 30, 5_000_000), 1080)
        self.assertEqual(self.d.short_level("auto", 60, 400_000), 480)
        self.assertEqual(self.d.short_level("auto", 0, 0), 1080)         # length unknown, speed not measured yet

    def test_goes_straight_to_the_download(self):
        out = self.d.resolve_short("abc", {"dur": 30, "title": "t"}, quality="1080")
        self.assertEqual(out["res"], 1080)
        self.assertEqual(self.calls, [("dash", 1080, 30.0)])             # one yt-dlp call, no separate lookup first

    def test_small_size_streams(self):
        self.d.resolve_short("abc", {}, quality="360")
        self.assertEqual(self.calls, [("full", "360")])

    def test_falls_back_to_the_full_lookup(self):
        def boom(vid, meta, res):
            raise RuntimeError("no")
        self.d.fetch_dash = boom
        self.d.resolve_short("abc", {}, quality="1080")
        self.assertEqual(self.calls, [("full", "1080")])


class ShortsTests(unittest.TestCase):
    class Dlp:
        def __init__(self):
            self.calls = []

        def _json(self, *args):
            self.calls.append(args)
            url = args[-1]
            if "/shorts" in url:
                cid = url.split("/channel/")[1].split("/")[0]
                return {"channel": "Chan " + cid, "entries": [{"id": f"{cid[:8]}{i:03d}"[:11].ljust(11, "x"), "title": f"short {i}", "duration": 30} for i in range(3)]}
            return {}

        def search(self, query, n=24):
            self.calls.append(("search", query, n))
            return [{"id": "aaaaaaaaaaa", "title": "short", "dur": 30.0, "live": False},
                    {"id": "bbbbbbbbbbb", "title": "long video", "dur": 900.0, "live": False},
                    {"id": "ccccccccccc", "title": "no length", "dur": 0.0, "live": False},
                    {"id": "ddddddddddd", "title": "live", "dur": 0.0, "live": True}]

    def test_channel_shorts_reads_the_shorts_tab(self):
        d = self.Dlp()
        got = shorts.channel_shorts(d, "UCabc12345", 5)
        self.assertEqual(len(got), 3)
        self.assertTrue(all(v["short"] and v["channelId"] == "UCabc12345" and v["channel"] == "Chan UCabc12345" for v in got))
        self.assertTrue(d.calls[0][-1].endswith("/channel/UCabc12345/shorts"))
        self.assertIn("5", d.calls[0])

    def test_search_keeps_only_short_enough_results(self):
        d = self.Dlp()
        got = shorts.search_shorts(d, "cats", 10)
        self.assertEqual([v["id"] for v in got], ["aaaaaaaaaaa", "ccccccccccc"])        # not the 15-minute one, not the live one
        self.assertEqual(d.calls[0][1], "cats #shorts")

    def test_subscription_shorts_weaves_channels_and_survives_a_failing_one(self):
        d = self.Dlp()
        subs = [{"id": f"UC{n}xxxxxxxx", "name": n} for n in "ABC"]
        real = d._json

        def flaky(*args):
            if "UCBxxxxxxxx" in args[-1]:
                raise RuntimeError("blocked")
            return real(*args)
        d._json = flaky
        got = shorts.subscription_shorts(d, subs, per=3, channels=3, rng=random.Random(1))
        self.assertEqual(len(got), 6)                                                    # two working channels, three each
        self.assertEqual({v["channelId"] for v in got}, {"UCAxxxxxxxx", "UCCxxxxxxxx"})


if __name__ == "__main__":
    unittest.main()


FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
  <title>The Test Show</title>
  <itunes:author>Some Host</itunes:author>
  <description>&lt;p&gt;A show &amp;amp; more&lt;/p&gt;</description>
  <itunes:image href="https://img.example/cover.jpg"/>
  <item><title>Old one</title><guid>g-1</guid><pubDate>Mon, 01 Jan 2024 10:00:00 +0000</pubDate>
    <enclosure url="https://cdn.example/1.mp3" type="audio/mpeg" length="100"/><itunes:duration>45:30</itunes:duration></item>
  <item><title>New one</title><guid>g-2</guid><pubDate>Mon, 01 Jul 2024 10:00:00 +0000</pubDate>
    <enclosure url="https://cdn.example/2.mp3" type="audio/mpeg" length="100"/><itunes:duration>1:02:03</itunes:duration>
    <itunes:episode>2</itunes:episode><itunes:season>1</itunes:season>
    <content:encoded><![CDATA[<p>Notes<br/>line two</p>]]></content:encoded></item>
  <item><title>A video</title><guid>g-3</guid><enclosure url="https://cdn.example/3.mp4" type="video/mp4" length="1"/></item>
  <item><title>No file</title><guid>g-4</guid></item>
</channel></rss>"""


class PodcastTests(unittest.TestCase):
    def test_parse_feed(self):
        f = podcasts.parse_feed(FEED, "https://x.example/feed.xml")
        self.assertEqual((f["title"], f["author"], f["image"]), ("The Test Show", "Some Host", "https://img.example/cover.jpg"))
        self.assertEqual(f["desc"], "A show & more")
        self.assertEqual([e["title"] for e in f["episodes"]], ["New one", "Old one"])     # newest first; video and file-less items left out
        new = f["episodes"][0]
        self.assertEqual((new["dur"], new["episode"], new["season"]), (3723.0, 2, 1))
        self.assertEqual(new["desc"], "Notes\nline two")
        self.assertEqual(f["episodes"][1]["dur"], 2730.0)
        self.assertEqual(len({e["key"] for e in f["episodes"]}), 2)

    def test_not_a_feed(self):
        for bad in (b"<html><body>hi</body></html>", b"not xml at all", b"<feed xmlns='http://www.w3.org/2005/Atom'></feed>",
                    b'<!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel/></rss>'):
            with self.assertRaises(podcasts.FeedError):
                podcasts.parse_feed(bad, "u")

    def test_durations(self):
        self.assertEqual([podcasts.parse_duration(x) for x in ("90", "1:30", "1:01:30", "", "abc")], [90.0, 90.0, 3690.0, 0.0, 0.0])

    def test_what_was_typed(self):
        self.assertEqual(podcasts.classify("history of rome"), ("search", "history of rome"))
        self.assertEqual(podcasts.classify("https://x.example/feed.xml"), ("feed", "https://x.example/feed.xml"))
        self.assertEqual(podcasts.classify("feed://x.example/rss"), ("feed", "https://x.example/rss"))
        self.assertEqual(podcasts.classify("x.example/rss")[0], "feed")
        self.assertEqual(podcasts.classify("https://podcasts.apple.com/us/podcast/x/id123")[0], "apple")
        self.assertEqual(podcasts.classify("")[0], "search")

    def test_search_keeps_results_with_a_feed(self):
        body = json.dumps({"results": [{"collectionName": "A", "artistName": "Me", "feedUrl": "https://a/feed", "artworkUrl600": "https://a/i.jpg"},
                                       {"collectionName": "No feed"}]}).encode()
        old = podcasts.net.get
        podcasts.net.get = lambda url, timeout=10.0: body
        try:
            self.assertEqual(podcasts.search("x"), [{"title": "A", "author": "Me", "feed": "https://a/feed", "image": "https://a/i.jpg", "genre": ""}])
        finally:
            podcasts.net.get = old

    def test_apple_link(self):
        old = podcasts.net.get
        podcasts.net.get = lambda url, timeout=10.0: json.dumps({"results": [{"feedUrl": "https://feed/x"}]}).encode()
        try:
            self.assertEqual(podcasts.feed_from_apple("https://podcasts.apple.com/us/podcast/name/id1234"), "https://feed/x")
            with self.assertRaises(podcasts.FeedError):
                podcasts.feed_from_apple("https://podcasts.apple.com/us/podcast/name")
        finally:
            podcasts.net.get = old

    def test_only_web_addresses_are_fetched(self):
        for bad in ("file:///etc/passwd", "ftp://x/y", "javascript:1"):
            with self.assertRaises(podcasts.FeedError):
                podcasts.fetch(bad)

    def test_store_keeps_subscriptions_and_places(self):
        with tempfile.TemporaryDirectory() as d:
            mk = lambda: podcasts.Store(Path(d) / "p.json", Path(d) / "cache")
            st = mk()
            feed = podcasts.parse_feed(FEED, "https://x.example/feed.xml")
            st.subscribe(feed)
            st.subscribe(feed)                                          # again: still one
            a, b = feed["episodes"][0]["key"], feed["episodes"][1]["key"]
            st.set_position(a, 1000, 3723)
            st.set_position(a, 2, 3723)                                 # a restart must not wipe the saved place
            st.set_position(b, 2700, 2730)                              # nearly done: counts as heard
            again = mk()
            self.assertEqual(len(again.subs), 1)
            self.assertEqual(again.position(a), 1000.0)
            self.assertTrue(again.is_played(b))
            self.assertEqual(again.position(b), 0.0)
            self.assertEqual(again.unplayed(again.cached_feed(feed["url"])), 1)
            again.mark_played(b, False)
            self.assertEqual(again.unplayed(again.cached_feed(feed["url"])), 2)
            again.unsubscribe(feed["url"])
            self.assertEqual(mk().subs, [])
            self.assertIsNone(mk().cached_feed(feed["url"]))


MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1200000,RESOLUTION=854x480,CODECS="avc1.4d401e,mp4a.40.2"
low/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1920x1080
https://cdn.example/hd/index.m3u8?token=abc
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720
/mid/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=900000
audio-ish.m3u8
"""


class HlsTests(unittest.TestCase):
    def test_master_playlist_biggest_first(self):
        vs = hls.parse_master(MASTER, "https://tv.example/live/master.m3u8")
        self.assertEqual([v["height"] for v in vs], [1080, 720, 480, 0])
        self.assertEqual(vs[0]["url"], "https://cdn.example/hd/index.m3u8?token=abc")
        self.assertEqual(vs[1]["url"], "https://tv.example/mid/index.m3u8")                   # relative to the host
        self.assertEqual(vs[2]["url"], "https://tv.example/live/low/index.m3u8")              # relative to the folder
        self.assertEqual(vs[0]["bandwidth"], 6_000_000)
        self.assertIn("1920x1080 @ 6.0 Mbps", hls.describe(vs))

    def test_a_plain_media_playlist_has_no_variants(self):
        self.assertEqual(hls.parse_master("#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nseg1.ts\n", "https://x/y.m3u8"), [])

    def test_only_web_addresses(self):
        self.assertEqual(hls.variants("file:///etc/passwd"), [])
        self.assertEqual(hls.variants("rtmp://x/y"), [])


class TwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = twitch.Store(Path(self.tmp.name) / "twitch.json")
        self.store.client_id = "cid"
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def client(self, script):
        def fake(method, url, headers=None, form=None, timeout=15.0):
            self.calls.append((method, url, form))
            for key, reply in script:
                if key in url and (reply.get("when") is None or reply["when"](form, headers)):
                    return reply["status"], reply["body"]
            return 404, {"message": "no such thing"}
        return twitch.Twitch(self.store, fake)

    def test_login_flow(self):
        tw = self.client([
            ("/device", {"status": 200, "body": {"device_code": "dc", "user_code": "ABCDEFGH", "verification_uri": "https://www.twitch.tv/activate", "interval": 5, "expires_in": 600}}),
        ])
        login = tw.start_login()
        self.assertEqual((login["user_code"], login["url"]), ("ABCDEFGH", "https://www.twitch.tv/activate"))
        self.assertEqual(self.calls[0][2]["scopes"], "user:read:follows")
        waiting = self.client([("/token", {"status": 400, "body": {"message": "authorization_pending"}})])
        self.assertFalse(waiting.poll_login(login))
        done = self.client([("/token", {"status": 200, "body": {"access_token": "AT", "refresh_token": "RT", "expires_in": 14000}}),
                            ("/users", {"status": 200, "body": {"data": [{"id": "7", "login": "me", "display_name": "Me", "profile_image_url": "i"}]}})])
        self.assertTrue(done.poll_login(login))
        again = twitch.Store(self.store.path)
        self.assertTrue(again.signed_in)
        self.assertEqual((again.user["login"], again.refresh), ("me", "RT"))
        self.assertEqual(oct(self.store.path.stat().st_mode & 0o777), "0o600")           # a login token: private to you

    def test_login_needs_a_client_id_and_reports_refusal(self):
        self.store.client_id = ""
        with self.assertRaises(twitch.TwitchError):
            self.client([]).start_login()
        self.store.client_id = "cid"
        bad = self.client([("/device", {"status": 400, "body": {"message": "invalid client"}})])
        with self.assertRaisesRegex(twitch.TwitchError, "invalid client"):
            bad.start_login()
        denied = self.client([("/token", {"status": 400, "body": {"message": "access_denied"}})])
        with self.assertRaises(twitch.TwitchError):
            denied.poll_login({"device_code": "x"})

    def signed(self):
        self.store.access, self.store.refresh, self.store.expires = "AT", "RT", time.time() + 3000
        self.store.user = {"id": "7", "login": "me", "name": "Me", "image": ""}

    def test_following_pages_and_pictures(self):
        self.signed()
        pages = iter([{"data": [{"broadcaster_id": "1", "broadcaster_login": "zed", "broadcaster_name": "Zed"}], "pagination": {"cursor": "c2"}},
                      {"data": [{"broadcaster_id": "2", "broadcaster_login": "amy", "broadcaster_name": "Amy"}], "pagination": {}}])

        def fake(method, url, headers=None, form=None, timeout=15.0):
            self.assertEqual(headers["Client-Id"], "cid")
            if "channels/followed" in url:
                return 200, next(pages)
            if "/users" in url:
                return 200, {"data": [{"id": "1", "profile_image_url": "z.png"}, {"id": "2", "profile_image_url": "a.png"}]}
            return 404, {}
        out = twitch.Twitch(self.store, fake).following()
        self.assertEqual([(c["name"], c["image"]) for c in out], [("Amy", "a.png"), ("Zed", "z.png")])

    def test_live_most_viewers_first(self):
        self.signed()
        body = {"data": [{"user_login": "a", "user_name": "A", "title": "t", "game_name": "g", "viewer_count": 5, "type": "live", "thumbnail_url": "x-{width}x{height}.jpg"},
                         {"user_login": "b", "user_name": "B", "viewer_count": 50, "type": "live", "thumbnail_url": ""}], "pagination": {}}
        out = twitch.Twitch(self.store, lambda *a, **k: (200, body)).live()
        self.assertEqual([s["login"] for s in out], ["b", "a"])
        self.assertEqual(out[1]["thumb"], "x-440x248.jpg")

    def test_an_expired_token_is_refreshed_once(self):
        self.signed()
        seen = []

        def fake(method, url, headers=None, form=None, timeout=15.0):
            seen.append(url.split("?")[0].rsplit("/", 1)[-1])
            if url.endswith("/token"):
                return 200, {"access_token": "NEW", "refresh_token": "RT2", "expires_in": 3600}
            return (401, {"message": "Invalid OAuth token"}) if headers["Authorization"] == "Bearer AT" else (200, {"data": [], "pagination": {}})
        self.assertEqual(twitch.Twitch(self.store, fake).live(), [])
        self.assertEqual((self.store.access, self.store.refresh), ("NEW", "RT2"))
        self.assertEqual(seen, ["followed", "token", "followed"])

    def test_channel_names(self):
        for text, want in (("Shroud", "shroud"), ("https://www.twitch.tv/Shroud?sr=a", "shroud"), ("twitch.tv/some_one/videos", "some_one"),
                           ("a b", ""), ("ab", ""), ("https://example.com/x", "")):
            self.assertEqual(twitch.channel_login(text), want)

    def test_format_choice(self):
        fm = [{"url": "u1", "height": 1080, "fps": 60, "vcodec": "avc1", "acodec": "mp4a"}, {"url": "u2", "height": 720, "fps": 60, "vcodec": "avc1", "acodec": "mp4a"},
              {"url": "u3", "height": 480, "vcodec": "avc1", "acodec": "mp4a"}, {"url": "ua", "vcodec": "none", "acodec": "mp4a"}]
        self.assertEqual(twitch.pick_format(fm)["url"], "u1")
        self.assertEqual(twitch.pick_format(fm, "720")["url"], "u2")
        self.assertEqual(twitch.pick_format(fm, "360")["url"], "u3")          # nothing that small: the smallest there is
        self.assertIsNone(twitch.pick_format([fm[3]]))

    def stream(self, height=1080):
        return {"title": "T", "formats": [{"url": f"https://v/{height}.m3u8", "height": height, "vcodec": "avc1", "acodec": "mp4a"}]}

    def test_resolve_normally(self):
        got = twitch.resolve("yt-dlp", "chan", self.store, run=lambda args, t: self.stream())
        self.assertEqual((got["height"], got["adfree"], got["note"]), (1080, False, ""))

    def test_ad_free_uses_the_proxy(self):
        self.store.adfree, self.store.proxy = True, "http://proxy.example:3128"
        seen = []
        got = twitch.resolve("yt-dlp", "chan", self.store, run=lambda args, t: (seen.append(args), self.stream())[1])
        self.assertIn("--proxy", seen[0])
        self.assertTrue(got["adfree"])
        self.assertEqual(len(seen), 1)

    def test_ad_free_falls_back_to_the_normal_stream(self):
        self.store.adfree, self.store.proxy = True, "http://proxy.example:3128"
        seen = []

        def run(args, t):
            seen.append(args)
            if "--proxy" in args:
                raise twitch.TwitchError("Twitch took too long to answer.")
            return self.stream(720)
        got = twitch.resolve("yt-dlp", "chan", self.store, run=run)
        self.assertEqual((got["height"], got["adfree"]), (720, False))
        self.assertIn("with ads", got["note"])
        self.assertEqual(len(seen), 2)

    def test_playlist_summary_shows_tags_not_addresses(self):
        text = ("#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:100\n"
                '#EXT-X-DATERANGE:ID="stitched-ad-1",CLASS="twitch-stitched-ad",DURATION=30.0,X-TV-TWITCH-AD-URL="https://ads.example/a?token=SECRET"\n'
                "#EXT-X-PROGRAM-DATE-TIME:2026-11-19T08:00:00.000Z\n#EXTINF:2.000,\nhttps://cdn.example/seg1.mp4?dna=SECRET\n"
                "#EXT-X-DISCONTINUITY\n#EXT-X-PROGRAM-DATE-TIME:2026-11-19T08:00:30.000Z\n#EXTINF:2.000,\nhttps://cdn.example/seg2.mp4\n")
        out = twitch.playlist_summary("u", fetch=lambda u: text)
        self.assertIn("2 segments, 1 discontinuities", out[0])
        self.assertIn("first time 2026-11-19T08:00:00.000Z", out[0])
        joined = "\n".join(out)
        self.assertIn("twitch-stitched-ad", joined)
        self.assertNotIn("SECRET", joined)
        self.assertNotIn("cdn.example", joined)
        self.assertIn("unreadable", twitch.playlist_summary("u", fetch=lambda u: 1 / 0)[0])

    def test_ad_free_without_a_proxy_says_so(self):
        self.store.adfree = True
        got = twitch.resolve("yt-dlp", "chan", self.store, run=lambda a, t: self.stream())
        self.assertIn("no proxy address", got["note"])

    def test_offline_is_reported_not_retried(self):
        self.store.adfree, self.store.proxy = True, "http://p:1"
        calls = []

        def run(args, t):
            calls.append(args)
            raise twitch.TwitchError("That channel is offline right now.")
        with self.assertRaisesRegex(twitch.TwitchError, "offline"):
            twitch.resolve("yt-dlp", "chan", self.store, run=run)
        self.assertEqual(len(calls), 1)


class TwitchSplitTests(unittest.TestCase):
    TXT = """#EXTM3U
#EXT-X-TARGETDURATION:5
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-DATERANGE:ID="a",CLASS="twitch-stitched-ad",START-DATE="2026-11-18T19:15:16.044Z",DURATION=10.0
#EXT-X-MAP:URI="http://x/adinit"
#EXT-X-PROGRAM-DATE-TIME:2026-11-18T19:15:16.044Z
#EXTINF:2.000,
http://x/a1
#EXT-X-DISCONTINUITY
#EXT-X-MAP:URI="http://x/liveinit"
#EXT-X-PROGRAM-DATE-TIME:2026-11-18T19:15:26.044Z
#EXTINF:2.000,
http://x/l1
"""

    def test_split(self):
        from marquee.core import twitch as tw
        cut = tw.ad_end(self.TXT)
        self.assertIsNotNone(cut)
        before, after = tw.split_at(self.TXT, "http://x/", cut)
        self.assertIn("a1", before)
        self.assertIn("#EXT-X-ENDLIST", before)
        self.assertNotIn("l1", before)
        self.assertIn("l1", after)
        self.assertIn("liveinit", after)
        self.assertNotIn("a1", after)
        self.assertNotIn("ENDLIST", after)

    def test_no_break(self):
        from marquee.core import twitch as tw
        self.assertIsNone(tw.ad_end("#EXTM3U\n#EXTINF:2,\nhttp://x/s\n"))


    def test_break_tail_stays_with_the_break(self):
        from marquee.core import twitch as tw
        txt = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-DATERANGE:ID="a",CLASS="twitch-stitched-ad",START-DATE="2026-11-18T19:30:22.044Z",DURATION=30.235
#EXT-X-PROGRAM-DATE-TIME:2026-11-18T19:30:50.044Z
#EXTINF:2.000,
http://x/a14
#EXT-X-PROGRAM-DATE-TIME:2026-11-18T19:30:52.044Z
#EXTINF:0.235,
http://x/tail
#EXT-X-DISCONTINUITY
#EXT-X-PROGRAM-DATE-TIME:2026-11-18T19:30:52.279Z
#EXTINF:2.000,
http://x/live1
"""
        before, after = tw.split_at(txt, "http://x/", tw.ad_end(txt))
        self.assertIn("tail", before)
        self.assertNotIn("tail", after)
        self.assertIn("live1", after)


    def test_cut_at_zero_keeps_everything(self):
        from marquee.core import twitch as tw
        txt = "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:7\n#EXT-X-PROGRAM-DATE-TIME:2026-11-19T00:00:00.000Z\n#EXTINF:2.000,\nhttp://x/a\n#EXTINF:2.000,\nhttp://x/b\n"
        before, after = tw.split_at(txt, "http://x/", 0.0)
        self.assertNotIn("http://x/a", before)
        self.assertIn("http://x/a", after)
        self.assertIn("http://x/b", after)
        self.assertIn("MEDIA-SEQUENCE:7", after)


class TwitchAdStateTests(unittest.TestCase):
    @staticmethod
    def pl(segs, ads=()):
        out = ["#EXTM3U", "#EXT-X-TARGETDURATION:2", "#EXT-X-MEDIA-SEQUENCE:0"]
        for s, d in ads:
            out.append(f'#EXT-X-DATERANGE:ID="a",CLASS="twitch-stitched-ad",START-DATE="{s}",DURATION={d}')
        for t, u in segs:
            out += ["#EXT-X-PROGRAM-DATE-TIME:" + t, "#EXTINF:2.0,", u]
        return "\n".join(out) + "\n"

    def test_break_listed_mid_stream(self):
        from marquee.core import twitch as tw
        ch = [("2026-01-01T00:00:%02d.000Z" % (i * 2), "c%d.ts" % i) for i in range(5)]
        ad = [("2026-01-01T00:00:%02d.000Z" % (10 + i * 2), "a%d.ts" % i) for i in range(3)]
        after = [("2026-01-01T00:00:%02d.000Z" % (16 + i * 2), "d%d.ts" % i) for i in range(2)]
        win = [("2026-01-01T00:00:10.000Z", 6)]
        st = tw.AdState()
        self.assertFalse(st.open(self.pl(ch), "http://x/"))
        text = self.pl(ch + ad, win)
        main, n, _ = st.main(text, "http://x/")
        self.assertEqual(n, 5)
        self.assertIn("#EXT-X-ENDLIST", main)                 # the channel stops where the break begins
        self.assertNotIn("a0.ts", main)
        self.assertTrue(st.pending())
        text = self.pl(ch + ad + after, win)
        adtxt, n, _ = st.ad_part(text, "http://x/")
        self.assertEqual(n, 3)
        self.assertIn("a2.ts", adtxt)
        self.assertNotIn("d0.ts", adtxt)
        st.advance()
        main, n, _ = st.main(text, "http://x/")
        self.assertEqual(n, 2)
        self.assertIn("d0.ts", main)
        self.assertNotIn("#EXT-X-ENDLIST", main)
        self.assertFalse(st.pending())


class LiveProxyTests(unittest.TestCase):
    def test_live_address_is_swapped_for_a_fresh_one(self):
        import threading, time, urllib.request
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from marquee.core.streamproxy import StreamProxy

        class Up(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = (b"#EXTM3U\n#EXTINF:2.0,\nseg1.ts\n") if "old" in self.path else (b"#EXTM3U\n#EXTINF:2.0,\nfresh.ts\n")
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        up = ThreadingHTTPServer(("127.0.0.1", 0), Up)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{up.server_address[1]}"
        px = StreamProxy()
        try:
            local = px.add_live(base + "/old.m3u8", {}, refresh=lambda: (base + "/new.m3u8", {}), every=0.05)
            self.assertIn("seg1.ts", urllib.request.urlopen(local).read().decode())
            time.sleep(0.2)
            urllib.request.urlopen(local).read()               # this poll starts the refresh
            time.sleep(0.5)
            self.assertIn("fresh.ts", urllib.request.urlopen(local).read().decode())
        finally:
            px.stop()
            up.shutdown()

    def test_long_live_playlist_is_cut_to_the_live_edge(self):
        from marquee.core.streamproxy import _Handler
        head = ["#EXTM3U", "#EXT-X-TARGETDURATION:2", "#EXT-X-MEDIA-SEQUENCE:100"]
        lines = head + [x for i in range(100, 150) for x in (f"#EXTINF:2.0,", f"s{i}.ts")]
        out = _Handler._live_edge(lines, 12)
        self.assertIn("#EXT-X-MEDIA-SEQUENCE:136", out)
        self.assertEqual([l for l in out if l.endswith(".ts")][0], "s136.ts")
        self.assertEqual(len([l for l in out if l.endswith(".ts")]), 12)
        self.assertEqual(_Handler._live_edge(lines + ["#EXT-X-ENDLIST"], 12), lines + ["#EXT-X-ENDLIST"])

    def test_playlist_and_pieces_come_through_with_headers(self):
        import threading
        import urllib.request
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from marquee.core.streamproxy import StreamProxy

        class Up(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.headers.get("User-Agent") != "TestUA/1":          # like googlevideo: refuse a client with the wrong headers
                    self.send_response(403)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path.startswith("/live.m3u8"):
                    body = b"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MAP:URI=\"init.mp4\"\n#EXTINF:2.0,\nseg1.ts\n#EXTINF:2.0,\nseg2.ts\n"
                else:
                    body = b"PIECE" + self.path.encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        up = ThreadingHTTPServer(("127.0.0.1", 0), Up)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        px = StreamProxy()
        try:
            local = px.add_live(f"http://127.0.0.1:{up.server_address[1]}/live.m3u8", {"User-Agent": "TestUA/1"})
            text = urllib.request.urlopen(local).read().decode()
            lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
            self.assertEqual(len(lines), 2)
            self.assertTrue(all(ln.startswith("http://127.0.0.1:") and "?u=" in ln for ln in lines))
            self.assertTrue(all(ln.split("?")[0].endswith(".ts") for ln in lines))      # ffmpeg only accepts known extensions
            self.assertIn('URI="http://127.0.0.1:', text)
            self.assertEqual(urllib.request.urlopen(lines[0]).read(), b"PIECE/seg1.ts")
        finally:
            px.stop()
            up.shutdown()


    def test_piece_without_a_length_is_sent_with_one(self):
        import threading
        import urllib.request
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from marquee.core.streamproxy import StreamProxy

        class Up(BaseHTTPRequestHandler):                    # protocol HTTP/1.0: no length, ends by closing, like a piece still being produced
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"x" * 5000)
        up = ThreadingHTTPServer(("127.0.0.1", 0), Up)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        px = StreamProxy()
        try:
            local = px.add_live(f"http://127.0.0.1:{up.server_address[1]}/a/seg.ts", {})
            r = urllib.request.urlopen(local, timeout=5)
            self.assertEqual(r.headers.get("Content-Length"), "5000")        # ffmpeg treats a piece with no length as cut short
            self.assertEqual(len(r.read()), 5000)
        finally:
            px.stop()
            up.shutdown()


class YoutubeInfoTests(unittest.TestCase):
    def test_description_links_and_times(self):
        from marquee.core import youtube as y
        h = y.describe_html("0:00 Intro\n1:02:03 Part two <b>\nsee https://example.com/a?t=1:30. ok")
        self.assertIn('<a href="seek:0">0:00</a>', h)
        self.assertIn('<a href="seek:3723">1:02:03</a>', h)
        self.assertIn('<a href="https://example.com/a?t=1:30">https://example.com/a?t=1:30</a>.', h)   # the time inside the address stays put
        self.assertIn("&lt;b&gt;", h)                                                                    # markup in the text is escaped
        self.assertIn("<br>", h)

    def test_chapters_and_upload_date(self):
        from marquee.core import youtube as y
        self.assertEqual(y.chapters_from({"chapters": [{"title": "Intro", "start_time": 0}, {"title": "", "start_time": 61.5}]}),
                         [{"title": "Intro", "start": 0.0}, {"title": "Chapter", "start": 61.5}])
        self.assertEqual(y.chapters_from({}), [])
        self.assertEqual(y.upload_text({"upload_date": "20261112"}), "12 Nov 2026")
        self.assertEqual(y.upload_text({}), "")

    def test_comments_keep_top_level_pinned_first(self):
        from marquee.core import youtube as y
        d = {"comments": [{"id": "a", "text": "first 1:00", "author": "@Ann", "like_count": 3, "parent": "root"},
                          {"id": "b", "text": "reply", "author": "Bob", "parent": "a"},
                          {"id": "c", "text": "pinned", "author": "Cy", "parent": "root", "is_pinned": True, "author_is_uploader": True}]}
        rows = y.comments_from(d)
        self.assertEqual([r["id"] for r in rows], ["c", "a"])
        self.assertEqual(rows[1]["author"], "Ann")
        self.assertIn("seek:60", rows[1]["html"])
        self.assertTrue(rows[0]["uploader"])


class YoutubeCaptionTests(unittest.TestCase):
    def test_tracks_offered(self):
        from marquee.core import youtube as y
        got = y.captions_from({"subtitles": {"en": [{"name": "English"}], "live_chat": [{"name": "Live chat"}]},
                               "automatic_captions": {"en-orig": [{"name": "English (Original)"}], "fr": [{"name": "French"}]}})
        self.assertEqual([(t["id"], t["auto"], t["label"]) for t in got],
                         [("s:en", False, "English"), ("a:en-orig", True, "English (auto-generated)")])
        self.assertEqual(y.captions_from({}), [])
        only = y.captions_from({"language": "de", "automatic_captions": {"de": [{"name": "German"}], "en": [{"name": "English"}]}})
        self.assertEqual([t["id"] for t in only], ["a:de"])

    def test_rolling_auto_captions_show_each_line_once(self):
        from marquee.core import subtitles as sb, youtube as y
        vtt = ("WEBVTT\n\n00:00:01.000 --> 00:00:03.000 align:start position:0%\nhello<00:00:01.500><c> and</c>\n\n"
               "00:00:03.000 --> 00:00:03.010\nhello and\n\n00:00:03.010 --> 00:00:05.000\nhello and\nto<00:00:03.500><c> you</c>\n\n"
               "00:00:05.000 --> 00:00:05.010\nto you\n")
        got = y.rolling_to_lines(sb.parse(vtt, "x.vtt"))
        self.assertEqual([c.text for c in got], ["hello and", "to you"])

    def test_caption_language_is_remembered(self):
        import tempfile
        from pathlib import Path
        from marquee.core import youtube as y
        with tempfile.TemporaryDirectory() as d:
            st = y.Store(Path(d))
            st.set_caption_lang("fr")
            self.assertEqual(y.Store(Path(d)).caption_lang, "fr")


class CaptionTextTests(unittest.TestCase):
    def test_codes_become_characters(self):
        from marquee.core import youtube as y
        self.assertEqual(y.unescape_caption("&gt;&gt; Tom &amp; Jerry&#39;s&nbsp;show"), ">> Tom & Jerry's show")
        self.assertEqual(y.unescape_caption("a\u200bb\u00a0c"), "ab c")
        self.assertEqual(y.unescape_caption("[Music] ♪"), "[Music] ♪")
