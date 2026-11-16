"""Subtitles, shared by every frontend: parsing, finding tracks, and picking the cue to show.

Sources, in the order they are listed to the user:
  * sidecar files next to the video (Movie.mkv + Movie.srt, Movie.en.srt, Subs/Movie.en.srt)
  * files the user added by hand (stored on the item)
  * text tracks inside the file (MKV, MP4, ...), pulled out with ffmpeg when it is installed

Formats: SubRip (.srt), WebVTT (.vtt) and SubStation / Advanced SubStation (.ssa, .ass, shown as plain text).
Picture-based tracks (PGS, VobSub, DVB) cannot be drawn as text and are reported, not listed.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .library import cache_dir

SUB_EXT = (".srt", ".vtt", ".ass", ".ssa")
TEXT_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text", "microdvd", "subviewer", "sami"}
IMAGE_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub", "dvb_teletext"}

LANG = {
    "en": "English", "eng": "English", "ja": "Japanese", "jpn": "Japanese", "es": "Spanish", "spa": "Spanish",
    "fr": "French", "fre": "French", "fra": "French", "de": "German", "ger": "German", "deu": "German",
    "it": "Italian", "ita": "Italian", "pt": "Portuguese", "por": "Portuguese", "ru": "Russian", "rus": "Russian",
    "ko": "Korean", "kor": "Korean", "zh": "Chinese", "chi": "Chinese", "zho": "Chinese", "nl": "Dutch",
    "dut": "Dutch", "nld": "Dutch", "sv": "Swedish", "swe": "Swedish", "no": "Norwegian", "nor": "Norwegian",
    "da": "Danish", "dan": "Danish", "fi": "Finnish", "fin": "Finnish", "pl": "Polish", "pol": "Polish",
    "tr": "Turkish", "tur": "Turkish", "ar": "Arabic", "ara": "Arabic", "he": "Hebrew", "heb": "Hebrew",
    "hi": "Hindi", "hin": "Hindi", "cs": "Czech", "cze": "Czech", "ces": "Czech", "el": "Greek", "gre": "Greek",
    "ell": "Greek", "hu": "Hungarian", "hun": "Hungarian", "th": "Thai", "tha": "Thai", "vi": "Vietnamese",
    "vie": "Vietnamese", "id": "Indonesian", "ind": "Indonesian", "uk": "Ukrainian", "ukr": "Ukrainian",
}


@dataclass
class Cue:
    start: float
    end: float
    text: str


@dataclass
class Track:
    id: str                 # "f:<absolute path>" for a file, "e:<stream index>" for a track inside the video
    label: str
    source: str             # "file" | "embedded"
    path: str = ""          # file tracks
    stream: int = -1        # embedded tracks: ffmpeg stream index
    lang: str = ""
    default: bool = False


@dataclass
class Found:
    tracks: list[Track] = field(default_factory=list)
    picture_only: bool = False       # the video has subtitle tracks, but only picture-based ones
    no_ffmpeg: bool = False          # could not look inside the file because ffmpeg is missing


# ---------------------------------------------------------------- parsing
def decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            if enc == "utf-16" and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
                continue
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


def _seconds(s: str) -> float | None:
    m = re.match(r"^\s*(?:(\d+):)?(\d+):(\d+)(?:[.,](\d+))?\s*$", s)
    if not m:
        return None
    h, mi, se, frac = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(se) + (float("0." + frac) if frac else 0.0)


_KEEP = re.compile(r"<(?!/?(?:i|b|u)>)[^>]*>", re.I)


def clean(text: str) -> str:
    """Drop ASS override blocks and unknown tags. Keeps <i>, <b>, <u>. Result is plain text with those tags."""
    text = re.sub(r"\{[^}]*\}", "", text)
    text = text.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    return _KEEP.sub("", text).strip()


def to_markup(text: str, line_break: str = "\n") -> str:
    """Escape text for Pango or Qt StyledText markup, keeping the <i>, <b> and <u> tags that survive clean()."""
    esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    esc = re.sub(r"&lt;(/?)([ibu])&gt;", r"<\1\2>", esc, flags=re.I)
    return esc.replace("\n", line_break)


def to_plain(text: str) -> str:
    return re.sub(r"</?[ibu]>", "", text, flags=re.I)


def parse(text: str, name: str = "x.srt") -> list[Cue]:
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[Cue] = []
    ext = Path(name).suffix.lower()
    if ext in (".ass", ".ssa"):
        fmt: list[str] | None = None
        for line in text.split("\n"):
            m = re.match(r"^Format:\s*(.*)$", line, re.I)
            if m and re.search(r"\bText\b", m.group(1), re.I) and re.search(r"\bStart\b", m.group(1), re.I):
                fmt = [c.strip().lower() for c in m.group(1).split(",")]
                continue
            m = re.match(r"^Dialogue:\s*(.*)$", line, re.I)
            if m and fmt:
                parts = m.group(1).split(",", len(fmt) - 1)
                if len(parts) < len(fmt):
                    continue
                s, e = _seconds(parts[fmt.index("start")]), _seconds(parts[fmt.index("end")])
                t = clean(parts[fmt.index("text")])
                if t and s is not None and e is not None:
                    cues.append(Cue(s, e, t))
    else:
        for block in re.split(r"\n{2,}", text):
            lines = block.split("\n")
            i = next((n for n, ln in enumerate(lines) if "-->" in ln), None)
            if i is None:
                continue
            m = re.match(r"^\s*([\d:.,]+)\s*-->\s*([\d:.,]+)", lines[i])
            if not m:
                continue
            s, e, t = _seconds(m.group(1)), _seconds(m.group(2)), clean("\n".join(lines[i + 1:]))
            if t and s is not None and e is not None:
                cues.append(Cue(s, e, t))
    cues.sort(key=lambda c: c.start)
    return cues


def parse_file(path: str) -> list[Cue]:
    with open(path, "rb") as f:
        return parse(decode(f.read()), path)


class Timeline:
    """Fast 'what should be on screen at time t' lookup over a cue list."""

    def __init__(self, cues: list[Cue]):
        self.cues = cues
        self.starts = [c.start for c in cues]

    def at(self, t: float, delay: float = 0.0, scale: float = 1.0) -> str:
        """What is on screen at playback time t. A cue at c is shown at c * scale + delay."""
        t = (t - delay) / (scale or 1.0)
        i = bisect.bisect_right(self.starts, t) - 1
        out = [self.cues[k].text for k in range(max(0, i - 5), i + 1) if self.cues[k].end > t]
        return "\n".join(out)


# ---------------------------------------------------------------- finding tracks
def _norm(name: str, subtitle: bool = True) -> str:
    stem = Path(name).stem
    if subtitle:                                          # a subtitle's .en / .forced tail; never the video's own name
        stem = re.sub(r"\.\d{1,2}$", "", stem)                # ".en.2.srt": the second subtitle saved for a video
        stem = re.sub(r"\.(?:[a-z]{2,3}(?:-[a-z]{2})?|forced|sdh|cc|default)$", "", stem, flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", stem.lower())


def _lang_from_name(name: str) -> str:
    m = re.search(r"[._ -]([a-z]{2,3}(?:-[a-z]{2})?)$", Path(name).stem, re.I)
    code = m.group(1).lower() if m else ""
    return code.split("-")[0] if code in LANG or code.split("-")[0] in LANG else ""


def label_for(lang: str, title: str = "", fallback: str = "Subtitles") -> str:
    name = LANG.get(lang.lower(), "") if lang and lang.lower() not in ("und", "") else ""
    base = name or (lang.upper() if lang and lang.lower() != "und" else fallback)
    return f"{base} – {title}" if title and title.lower() != base.lower() else base


def sidecars(video: str) -> list[Track]:
    """Subtitle files beside the video, or in a Subs / Subtitles folder, whose name starts with the video's."""
    vdir, want = Path(video).parent, _norm(Path(video).name, False)
    found: list[Track] = []
    for folder in (vdir, vdir / "Subs", vdir / "subs", vdir / "Subtitles", vdir / "subtitles"):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for n in names:
            if n.lower().endswith(SUB_EXT) and _norm(n) == want:
                p = str(folder / n)
                lang = _lang_from_name(n)
                found.append(Track(f"f:{p}", label_for(lang) if lang else "Subtitles", "file", path=p, lang=lang))
    return found


def file_track(path: str) -> Track:
    lang = _lang_from_name(Path(path).name)
    return Track(f"f:{path}", label_for(lang) if lang else Path(path).stem[-28:], "file", path=path, lang=lang)


def _tools() -> tuple[str | None, str | None]:
    return shutil.which("ffprobe"), shutil.which("ffmpeg")


def embedded(video: str, timeout: float = 20.0) -> Found:
    probe, ffmpeg = _tools()
    res = Found()
    if not probe or not ffmpeg:
        res.no_ffmpeg = True
        return res
    try:
        r = subprocess.run([probe, "-v", "error", "-select_streams", "s", "-show_entries",
                            "stream=index,codec_name:stream_tags=language,title:stream_disposition=default",
                            "-of", "json", video], capture_output=True, text=True, timeout=timeout)
        streams = json.loads(r.stdout or "{}").get("streams", [])
    except (OSError, ValueError, subprocess.SubprocessError):
        return res
    pictures = 0
    for s in streams:
        codec = s.get("codec_name", "")
        if codec in IMAGE_CODECS or (codec not in TEXT_CODECS):
            pictures += 1
            continue
        tags = s.get("tags", {}) or {}
        lang = (tags.get("language") or "").lower()
        res.tracks.append(Track(f"e:{s['index']}", label_for(lang, tags.get("title", "")), "embedded", stream=s["index"],
                                lang=lang, default=bool((s.get("disposition") or {}).get("default"))))
    res.picture_only = bool(pictures) and not res.tracks
    return res


def tracks_for(video: str, extra: list[str] | None = None) -> Found:
    """Everything that can be offered for this video. Runs ffprobe, so call it off the UI thread."""
    found = Found()
    seen: set[str] = set()
    for t in sidecars(video) + [file_track(p) for p in (extra or []) if os.path.exists(p)]:
        if t.id not in seen:
            seen.add(t.id)
            found.tracks.append(t)
    inner = embedded(video)
    found.tracks += inner.tracks
    found.picture_only = inner.picture_only
    found.no_ffmpeg = inner.no_ffmpeg and not found.tracks
    # two tracks with one label, such as two English files: number them
    counts: dict[str, int] = {}
    for t in found.tracks:
        counts[t.label] = counts.get(t.label, 0) + 1
    seen_n: dict[str, int] = {}
    for t in found.tracks:
        if counts[t.label] > 1:
            seen_n[t.label] = seen_n.get(t.label, 0) + 1
            t.label = f"{t.label} ({seen_n[t.label]})"
    return found


def choose(tracks: list[Track], saved: str = "") -> str:
    """Which track to turn on: the saved choice, otherwise English, otherwise the first. 'off' stays off."""
    if saved == "off":
        return ""
    if saved and any(t.id == saved for t in tracks):
        return saved
    for t in tracks:
        if t.lang in ("en", "eng"):
            return t.id
    return tracks[0].id if tracks else ""


# ---------------------------------------------------------------- loading cues
def _cache_path(video: str, stream: int) -> Path:
    st = os.stat(video)
    key = hashlib.sha1(f"{video}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:20]
    return cache_dir() / "subs" / f"{key}-{stream}.srt"


def load(video: str, track: Track, timeout: float = 180.0) -> list[Cue]:
    """Read the cues of one track. Embedded tracks are extracted once and cached. Blocking: use a worker thread."""
    if track.source == "file":
        return parse_file(track.path)
    cache = _cache_path(video, track.stream)
    if cache.exists():
        return parse_file(str(cache))
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return []
    r = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-i", video, "-map", f"0:{track.stream}", "-f", "srt", "-"],
                       capture_output=True, timeout=timeout)
    text = decode(r.stdout)
    cues = parse(text, "x.srt")
    if cues:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(text, encoding="utf-8")
        except OSError:
            pass
    return cues
