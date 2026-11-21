import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import QtMultimedia
import QtQuick.Dialogs
import QtQml.Models
import org.kde.kirigami as Kirigami

Kirigami.Page {
    id: pp

    property string itemId
    property var info: ({})
    // Videos play here. Songs play in the window's music player (see MusicEngine.qml), so they carry on after this
    // page closes; `player` is whichever applies, so the controls below work for both.
    property var music            // the window's music player, passed in when the page is opened
    property var live             // the window's live-TV player (see LiveEngine.qml), passed in with `music`
    readonly property var player: isLive ? live.player : (isVideo ? videoPlayer : music.player)
    readonly property bool mutedNow: isLive ? live.muted : (isVideo ? audioOut.muted : music.muted)
    readonly property string errText: isLive ? live.errorText : (isVideo ? errorText : music.errorText)
    property var lyricRows: []
    property bool lyricSynced: false
    property string lyricStatus: ""
    property string refinedFor: ""
    property real lyricOffset: 0                 // seconds; positive shows lyrics earlier
    readonly property bool wideLayout: width >= Kirigami.Units.gridUnit * 36
    readonly property bool lyricsShown: !isVideo && music.lyricsOn
    property real resumeAt: 0
    property string retriedId: ""                  // a YouTube video already re-opened once after failing (so a real failure still shows)
    property string errorText: ""
    readonly property bool isVideo: info.media === "video"
    // Live TV: a stream with no end, no seeking and no resume point. `nowNext` feeds the on-screen guide strip.
    readonly property bool isLive: !!info.live
    property var nowNext: ({})
    property bool osd: false
    // subtitles
    property var subTracks: []
    property string subSel: ""
    property string subLast: ""
    property string subText: ""
    property string subNote: ""
    property real subDelay: 0
    property int subSize: 1
    // Now Playing (music): colours from the cover, and the visualiser
    property var pal: ({ c1: "#2a2f45", c2: "#40304a", base: "#14161f", bar: "#9fb4ff", bar2: "#c2a0ff" })
    property string vizStyle: "bars"
    property var bandsData: []
    property var waveData: []
    readonly property var vizStyles: [["bars", "Bars"], ["mirror", "Mirror"], ["wave", "Wave"], ["dots", "Dots"], ["scope", "Scope"], ["off", "Off"]]

    // Fullscreen shows the picture only. Move the mouse to bring the controls back; they hide again after a moment.
    property bool fs: false
    property bool controlsUp: true
    globalToolBarStyle: fs ? Kirigami.ApplicationHeaderStyle.None : Kirigami.ApplicationHeaderStyle.Auto
    function poke() { controlsUp = true; hideTimer.restart(); if (isLive) { osd = true; osdTimer.restart() } }
    // Qt also reports the pointer "moving" when things change under a still mouse (such as the controls hiding),
    // which would bring them straight back. Only a real movement of a few pixels counts.
    property real lastX: -100
    property real lastY: -100
    function moved(p) {
        if (Math.abs(p.x - lastX) + Math.abs(p.y - lastY) > 4) { lastX = p.x; lastY = p.y; poke() }
    }
    Timer {
        id: hideTimer
        interval: 2500
        // Hides when the mouse has been still for a moment, even if it is resting on the controls. Any movement brings
        // them back. They stay while the video is paused.
        onTriggered: {
            if (pp.fs) console.log("marquee: idle in fullscreen; playing:", player.playbackState === MediaPlayer.PlayingState)
            if (pp.fs && player.playbackState === MediaPlayer.PlayingState) pp.controlsUp = false
            else if (pp.fs) restart()
        }
    }
    onFsChanged: { console.log("marquee: fullscreen is now", fs); poke() }
    onControlsUpChanged: console.log("marquee: controls shown:", controlsUp)
    Connections {
        target: pp.Window.window
        ignoreUnknownSignals: true
        function onVisibilityChanged() { pp.fs = pp.Window.window.visibility === Window.FullScreen; console.log("marquee: window visibility", pp.Window.window.visibility, "fullscreen:", pp.fs) }
    }

    title: info.title || ""
    padding: 0
    Kirigami.Theme.colorSet: isVideo ? Kirigami.Theme.Complementary : Kirigami.Theme.View
    Kirigami.Theme.inherit: false

    function fmt(ms) {
        var s = Math.max(0, Math.floor(ms / 1000)), h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60)
        s = s % 60
        return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(s).padStart(2, "0")
    }
    function load(id) {
        var next = marquee.itemInfo(id)
        if (next.media !== "video") {                  // a song: the music player does the playing
            if (music.itemId !== id) music.play(id)
            followMusic()
            return
        }
        if (next.live) {                               // a channel: the shared live player does the playing
            if (videoPlayer.source != "") { marquee.savePosition(itemId, videoPlayer.position / 1000, videoPlayer.duration / 1000); videoPlayer.stop(); videoPlayer.source = "" }
            itemId = id
            info = next
            errorText = ""
            subTracks = []; subSel = ""; subText = ""; subNote = ""
            nowNext = marquee.liveNowNext(id)
            live.attach(liveVideo)
            live.play(id)                              // already on this channel (from the preview): carries straight on
            poke()
            return
        }
        if (videoPlayer.source != "") marquee.savePosition(itemId, videoPlayer.position / 1000, videoPlayer.duration / 1000)
        itemId = id
        info = next
        errorText = ""
        subTracks = []; subSel = ""; subText = ""; subNote = ""
        subDelay = info.subDelay || 0
        if (info.media === "video" && !info.live) marquee.openSubtitles(id)
        nowNext = info.live ? marquee.liveNowNext(id) : ({})
        if (info.live) poke()
        if (!info.exists) { errorText = "This file is no longer at " + info.url.replace("file://", "") + ". Remove it from the library or restore the file."; videoPlayer.stop(); return }
        resumeAt = info.pos * 1000
        videoPlayer.source = info.url
        videoPlayer.play()
        sponsorParts = []; skipped = ({}); sponsorOn = marquee.sponsorBlockOn()
        if (!!info.yt && !info.live) marquee.loadSponsorSegments(id)
        startVisuals()
    }
    // Show whatever the music player is on now, and start the colours, bars and lyrics for it.
    function followMusic() {
        itemId = music.itemId
        info = music.info
        errorText = ""
        subTracks = []; subSel = ""; subText = ""; subNote = ""
        lyricRows = []; lyricSynced = false; lyricStatus = ""
        lyricOffset = marquee.lyricOffset(itemId)
        startVisuals()
        if (music.lyricsOn) askLyrics()
    }
    function askLyrics() {
        if (itemId === "") return
        lyricStatus = "Looking for lyrics…"
        marquee.loadLyrics(itemId)
    }
    // diagnostics for lyric clicks: where we asked to go, and where the player really was a moment later
    Timer { id: seekCheck; property real target: 0; interval: 1500
            onTriggered: console.log("lyrics seek: asked for " + target.toFixed(1) + "s, player is at " + (pp.player.position / 1000).toFixed(1) + "s, offset " + pp.lyricOffset) }
    // Timings made for a recording of a different length: start from the difference, unless you've set it by hand.
    function autoLyricOffset() {
        if (isVideo || !lyricSynced || !(player.duration > 0) || marquee.hasLyricOffset(itemId)) return
        var o = marquee.lyricAutoOffset(itemId, player.duration / 1000)
        if (o !== 0 && refinedFor !== itemId) {         // first look for lyrics made for a recording this long
            refinedFor = itemId
            marquee.refineLyrics(itemId, player.duration / 1000)
        }
        if (o !== 0) console.log("lyrics: timings are for a recording " + o + "s different in length, shifting by that")
        lyricOffset = o
    }
    Connections { target: pp.player; function onDurationChanged() { pp.autoLyricOffset() } }
    function nudgeLyrics(d) { lyricOffset = Math.round((lyricOffset + d) * 10) / 10; marquee.setLyricOffset(itemId, lyricOffset) }
    function toggleLyrics() { music.lyricsOn = !music.lyricsOn }
    Connections {
        target: pp.music
        function onLyricsOnChanged() { if (pp.music.lyricsOn && pp.lyricRows.length === 0 && !pp.isVideo) pp.askLyrics() }
        function onTrackChanged() {
            if (pp.isVideo) return
            if (pp.music.active) pp.followMusic()
            else pp.leave()
        }
    }
    Connections {
        target: marquee
        function onVizStyleChanged() { pp.vizStyle = marquee.vizStyle(); viz.requestPaint(); fullViz.requestPaint(); backViz.requestPaint() }
        function onLyricsReady(id, rows, synced, source) {
            if (id !== pp.itemId) return
            pp.lyricRows = rows
            pp.lyricSynced = synced
            pp.lyricStatus = rows.length === 0 ? "No lyrics found for this song." : ""
            pp.autoLyricOffset()
        }
    }
    function toggleMute() { if (isLive) live.muted = !live.muted; else if (isVideo) audioOut.muted = !audioOut.muted; else music.muted = !music.muted }
    function back() {
        if (!isVideo) { music.previous(); return }
        if (isLive) { step(-1); return }
        if (player.position > 3000 || !step(-1)) player.position = 0
    }
    // After playback has started, and never allowed to get in its way.
    function startVisuals() {
        try {
            if (info.media === "video") { marquee.stopVisualizer(); return }
            pal = music.pal
        } catch (e) { console.warn("visualiser:", e) }
    }
    function step(delta) {
        if (!isVideo) { if (delta > 0) music.next(false); else music.previous(); return true }
        var n = marquee.neighbor(itemId, delta)
        if (n.indexOf("yt:") === 0) { youtube.play(n.substring(3)); return true }     // next in a YouTube playlist
        if (n !== "") { load(n); return true }
        return false
    }
    function toggle() { player.playbackState === MediaPlayer.PlayingState ? player.pause() : player.play() }
    function leave() {
        if (isLive) applicationWindow().pipId = itemId      // keeps playing in the small box on the Live TV page
        if (applicationWindow().visibility === Window.FullScreen) applicationWindow().showNormal()
        else applicationWindow().pageStack.pop()
    }

    function pickSubtitle(id) {
        if (id !== "") subLast = id
        subSel = id
        subText = ""
        marquee.selectSubtitle(itemId, id)
    }
    function toggleSubtitles() {
        if (subSel !== "") pickSubtitle("")
        else if (subLast !== "") pickSubtitle(subLast)
        else if (subTracks.length > 0) pickSubtitle(subTracks[0].id)
    }
    function nudgeSubtitles(d) {
        subDelay = Math.round((subDelay + d) * 10) / 10
        marquee.setSubtitleDelay(itemId, subDelay)
    }
    function setSize(n) { subSize = n; marquee.setSubtitleSize(n) }

    Component.onCompleted: { sponsorOn = marquee.sponsorBlockOn(); fs = pp.Window.window ? pp.Window.window.visibility === Window.FullScreen : false; startCollection = marquee.collection; startTab = marquee.tab; subSize = marquee.subtitleSize(); vizStyle = marquee.vizStyle(); load(itemId) }

    function setViz(style) { vizStyle = style; marquee.setVizStyle(style); viz.requestPaint(); fullViz.requestPaint(); backViz.requestPaint() }
    // Draws the visualiser. `full` is the wide one behind the cover: it stands on a baseline 72% of the way down, is
    // drawn softer so the cover stays the focus, and is mirrored faintly below. The small one fills its box.
    function paintViz(cv, full) {
        var ctx = cv.getContext("2d"), w = cv.width, H = cv.height
        ctx.clearRect(0, 0, w, H)
        if (w < 2 || H < 2) return
        var v = bandsData, style = vizStyle
        var base = full ? H * 0.72 : H
        var at = function (i, count) { return v && v.length ? v[Math.min(v.length - 1, Math.floor(i * v.length / count))] : 0 }
        var draw = function (h, alpha) {
            var n = full ? Math.max(24, Math.round(w / 40)) : 48
            var g = ctx.createLinearGradient(0, h, 0, 0)
            g.addColorStop(0, pp.pal.bar2); g.addColorStop(1, pp.pal.bar)
            ctx.fillStyle = g; ctx.strokeStyle = pp.pal.bar
            ctx.globalAlpha = alpha
            if (style === "bars" || style === "mirror") {
                var gap = full ? 15 : 4, bw = (w - gap * (n - 1)) / n
                for (var i = 0; i < n; i++) {
                    var bh = Math.max(4, at(i, n) * h)
                    ctx.beginPath()
                    ctx.roundedRect(i * (bw + gap), style === "bars" ? h - bh : (h - bh) / 2, bw, bh, Math.min(bw / 2, 4), Math.min(bw / 2, 4))
                    ctx.fill()
                }
            } else if (style === "wave") {
                var m = full ? n : 40, pts = []
                for (var j = 0; j < m; j++) pts.push([j * w / (m - 1), h - Math.max(3, at(j, m) * h * 0.95)])
                var trace = function () {
                    ctx.beginPath(); ctx.moveTo(0, h); ctx.lineTo(pts[0][0], pts[0][1])
                    for (var k = 1; k < m - 1; k++) ctx.quadraticCurveTo(pts[k][0], pts[k][1], (pts[k][0] + pts[k + 1][0]) / 2, (pts[k][1] + pts[k + 1][1]) / 2)
                    ctx.lineTo(w, pts[m - 1][1])
                }
                trace(); ctx.lineTo(w, h); ctx.closePath(); ctx.globalAlpha = alpha * 0.4; ctx.fill(); ctx.globalAlpha = alpha
                trace(); ctx.lineWidth = 3; ctx.lineJoin = "round"; ctx.stroke()
            } else if (style === "dots") {
                var cols = full ? n : 32, rows = 9, cw = w / cols, ch = h / rows, r = Math.min(cw, ch) * 0.32
                for (var c = 0; c < cols; c++) {
                    var lit = Math.max(1, Math.min(rows, Math.round(at(c, cols) * rows)))
                    for (var q = 0; q < rows; q++) {
                        ctx.globalAlpha = alpha * (q < lit ? 1 : 0.2)
                        ctx.beginPath(); ctx.arc(c * cw + cw / 2, h - (q * ch + ch / 2), r, 0, 6.2832); ctx.fill()
                    }
                }
            } else if (style === "scope") {
                var wv = waveData && waveData.length ? waveData : [0], cnt = wv.length
                ctx.beginPath()
                for (var s2 = 0; s2 < cnt; s2++) {
                    var x = s2 * w / Math.max(1, cnt - 1), y = h / 2 + wv[s2] * h * 0.45
                    if (s2) ctx.lineTo(x, y); else ctx.moveTo(x, y)
                }
                ctx.lineWidth = 3; ctx.lineJoin = "round"; ctx.lineCap = "round"; ctx.stroke()
            }
            ctx.globalAlpha = 1
        }
        if (!full) { draw(base, 1); return }
        draw(base, 0.5)
        ctx.save()                                              // the reflection: the same picture upside down, below the baseline
        ctx.beginPath(); ctx.rect(0, base, w, H - base); ctx.clip()
        ctx.translate(0, base * 2); ctx.scale(1, -1)
        draw(base, 0.14)
        ctx.restore()
    }

    Timer {
        interval: 33
        repeat: true
        running: pp.visible && !pp.isVideo && pp.vizStyle !== "off"
        onTriggered: {
            pp.bandsData = marquee.bandsAt(player.position / 1000, player.playbackState === MediaPlayer.PlayingState)
            if (pp.vizStyle === "scope") pp.waveData = marquee.waveAt(player.position / 1000)
            viz.requestPaint()
            fullViz.requestPaint()
            backViz.requestPaint()
        }
    }

    property string subBadge: ""
    // SponsorBlock: parts of a YouTube video to jump over. `skipped` remembers which ones were already skipped, so
    // seeking back into one (to watch it on purpose) isn't undone.
    property var sponsorParts: []
    property var skipped: ({})
    property bool sponsorOn: true
    Timer { id: subBadgeTimer; interval: 4500; onTriggered: pp.subBadge = "" }
    Connections {
        target: marquee
        function onSubToast(text) { pp.subBadge = text; subBadgeTimer.restart() }
        function onSponsorReady(id, parts) { if (id === pp.itemId) { pp.sponsorParts = parts; pp.skipped = ({}) } }
        function onSubtitleTimingChanged(id, delay) { if (id === pp.itemId) pp.subDelay = delay }
        function onSubtitleTracksReady(id, tracks, sel, note) {
            if (id !== pp.itemId) return
            pp.subTracks = tracks
            pp.subSel = sel
            pp.subNote = note
            if (sel !== "") pp.subLast = sel
        }
    }
    Timer {
        interval: 250
        repeat: true
        running: pp.isVideo && pp.sponsorOn && pp.sponsorParts.length > 0
        onTriggered: {
            var t = videoPlayer.position / 1000
            for (var i = 0; i < pp.sponsorParts.length; i++) {
                var p = pp.sponsorParts[i]
                if (t >= p[0] && t < p[1] - 0.3 && !pp.skipped[i]) {
                    pp.skipped[i] = true
                    videoPlayer.position = p[1] * 1000
                    pp.subBadge = "Skipped: " + p[2]; subBadgeTimer.restart()
                    break
                }
            }
        }
    }
    Timer {
        interval: 50
        repeat: true
        running: pp.isVideo && pp.subSel !== ""
        onTriggered: pp.subText = marquee.subtitleAt(player.position / 1000)
    }
    FileDialog {
        id: subFile
        title: "Add Subtitle File"
        nameFilters: ["Subtitles (*.srt *.vtt *.ass *.ssa)", "All files (*)"]
        onAccepted: marquee.addSubtitleFile(pp.itemId, selectedFile)
    }
    // Called by the window when you pick something else in the sidebar, and on the way out.
    // Songs are not stopped by this: they carry on in the bar at the bottom.
    // (A channel is only stopped here when you go off to another collection; Esc and Guide leave it playing.)
    function stopPlayback() {
        if (isLive) { live.release(liveVideo); live.stop(); return }
        if (!isVideo) return
        if (videoPlayer.source != "") marquee.savePosition(itemId, videoPlayer.position / 1000, videoPlayer.duration / 1000)
        videoPlayer.stop()
        videoPlayer.source = ""
        marquee.stopVisualizer()
    }
    Component.onDestruction: {
        if (isLive) live.release(liveVideo)
        else stopPlayback()
        try { if (pp.Window.window && pp.Window.window.visibility === Window.FullScreen) pp.Window.window.showNormal() } catch (e) {}
    }
    // Choosing another collection or tab while this is open ends playback and returns to the library.
    property string startCollection: ""          // captured once when the page opens, not bound
    property string startTab: ""
    Connections {
        target: marquee
        function onStateChanged() {
            if ((marquee.collection !== pp.startCollection || marquee.tab !== pp.startTab) && applicationWindow().pageStack.lastItem === pp) {
                pp.stopPlayback()
                applicationWindow().leavePlayer()
            }
        }
    }

    MediaPlayer {
        id: videoPlayer
        audioOutput: AudioOutput { id: audioOut; volume: volumeSlider.value }
        videoOutput: video
        playbackRate: [0.75, 1, 1.25, 1.5, 2][speed.currentIndex]
        onMediaStatusChanged: {
            if (mediaStatus === MediaPlayer.BufferedMedia) pp.retriedId = ""
            if (mediaStatus === MediaPlayer.LoadedMedia && !!pp.info.yt) {
                try {                                   // what is really on screen, for the log
                    var r = metaData.value(MediaMetaData.Resolution)
                    console.log("marquee: youtube: " + pp.info.id + " is playing at " + r.width + "x" + r.height + (pp.info.upgradeTo > 0 ? " (" + pp.info.upgradeTo + "p still downloading)" : ""))
                } catch (e) {}
            }
            if (mediaStatus === MediaPlayer.LoadedMedia && pp.resumeAt > 0) { position = pp.resumeAt; pp.resumeAt = 0 }
            if (mediaStatus === MediaPlayer.EndOfMedia) {
                if (pp.isLive) { pp.errorText = "The stream for “" + pp.info.title + "” ended. The channel may be off air, or your connection dropped."; return }
                marquee.finished(pp.itemId); if (!pp.step(1)) pp.leave()
            }
        }
        onErrorOccurred: (error, message) => {
            // a YouTube video left paused for a long time can lose its source: open it again from where it was, once, before giving up
            if (!!pp.info.yt && !pp.isLive && pp.retriedId !== pp.info.id) {
                pp.retriedId = pp.info.id
                console.log("marquee: youtube: playback failed (" + message + "); reopening " + pp.info.id + " at " + Math.round(videoPlayer.position / 1000) + "s")
                youtube.reopen(pp.info.id.substring(3), videoPlayer.position / 1000, youtube.quality)
                return
            }
            pp.errorText = pp.isLive
                ? "Couldn’t open “" + pp.info.title + "”. " + message + " The channel may be offline, or the playlist link may have expired."
                : "This system could not play “" + pp.info.title + "”. " + message +
                  " Installing the GStreamer or FFmpeg plugins for your distribution usually fixes missing formats."
        }
    }
    Binding { target: pp.live; property: "volume"; value: volumeSlider.value; when: pp.isLive && !!pp.live }
    Timer { id: osdTimer; interval: 6000; onTriggered: pp.osd = false }
    Timer {                                  // the guide moves on while you watch
        interval: 20000
        repeat: true
        running: pp.isLive
        onTriggered: pp.nowNext = marquee.liveNowNext(pp.itemId)
    }
    Timer {
        interval: 5000
        repeat: true
        running: pp.isVideo && !pp.isLive && videoPlayer.playbackState === MediaPlayer.PlayingState
        onTriggered: marquee.savePosition(pp.itemId, videoPlayer.position / 1000, videoPlayer.duration / 1000)
    }

    Shortcut { sequences: ["Space"]; onActivated: pp.toggle() }
    Shortcut { sequences: ["K"]; onActivated: pp.toggle() }
    Shortcut { sequences: ["Up", "PgUp"]; enabled: pp.isLive; onActivated: pp.step(1) }
    Shortcut { sequences: ["Down", "PgDown"]; enabled: pp.isLive; onActivated: pp.step(-1) }
    Shortcut { sequences: ["Right"]; enabled: !pp.isLive; onActivated: player.position = Math.min(player.duration, player.position + 10000) }
    Shortcut { sequences: ["Left"]; enabled: !pp.isLive; onActivated: player.position = Math.max(0, player.position - 10000) }
    Shortcut { sequences: ["C"]; onActivated: pp.toggleSubtitles() }
    Shortcut { sequences: ["M"]; onActivated: pp.toggleMute() }
    Shortcut { sequences: ["L"]; enabled: !pp.isVideo; onActivated: pp.toggleLyrics() }
    Shortcut { sequences: ["["]; enabled: pp.lyricsShown && pp.lyricSynced; onActivated: pp.nudgeLyrics(-0.1) }   // fine adjustment: 0.1s
    Shortcut { sequences: ["]"]; enabled: pp.lyricsShown && pp.lyricSynced; onActivated: pp.nudgeLyrics(0.1) }
    Shortcut { sequences: ["N"]; onActivated: pp.step(1) }
    Shortcut { sequences: ["P"]; onActivated: pp.step(-1) }
    Shortcut { sequences: ["F", "F11"]; onActivated: applicationWindow().visibility === Window.FullScreen ? applicationWindow().showNormal() : applicationWindow().showFullScreen() }
    Shortcut { sequences: ["Escape"]; onActivated: pp.leave() }

    Rectangle {
        anchors { left: parent.left; right: parent.right; top: parent.top; bottom: parent.bottom }
        anchors.bottomMargin: pp.isVideo && !pp.fs ? controls.height : 0
        color: pp.isVideo ? "black" : pp.pal.base
        HoverHandler {
            cursorShape: pp.fs && !pp.controlsUp ? Qt.BlankCursor : Qt.ArrowCursor
            onPointChanged: pp.moved(point.scenePosition)
        }
        TapHandler { onTapped: pp.poke() }
        Behavior on color { ColorAnimation { duration: 600 } }

        // Music backdrop (dynamic colour, as on Apple Music and Spotify): glows in the cover's own colours drift
        // slowly over a deeper base. The colours crossfade when the track changes.
        Canvas {
            id: backdrop
            // Painted at 1/8 size and scaled up: the glows are soft, and a full-window canvas redrawn ten times a
            // second would starve the bars of CPU.
            readonly property int shrink: 8
            width: Math.ceil(parent.width / shrink)
            height: Math.ceil(parent.height / shrink)
            scale: shrink
            transformOrigin: Item.TopLeft
            smooth: true
            visible: !pp.isVideo
            renderTarget: Canvas.Image
            property real phase: 0
            property color g1: pp.pal.c1
            property color g2: pp.pal.c2
            property color g3: pp.pal.c3 || pp.pal.c2
            property color g4: pp.pal.c4 || pp.pal.c1
            property color gb: pp.pal.base
            Behavior on g1 { ColorAnimation { duration: 800 } }
            Behavior on g2 { ColorAnimation { duration: 800 } }
            Behavior on g3 { ColorAnimation { duration: 800 } }
            Behavior on g4 { ColorAnimation { duration: 800 } }
            Behavior on gb { ColorAnimation { duration: 800 } }
            onG1Changed: requestPaint()
            onG2Changed: requestPaint()
            onG3Changed: requestPaint()
            onG4Changed: requestPaint()
            onGbChanged: requestPaint()
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            Timer {
                interval: 100
                repeat: true
                running: backdrop.visible && pp.visible
                onTriggered: { backdrop.phase += 0.007; backdrop.requestPaint() }
            }
            onPaint: {
                var ctx = getContext("2d"), w = width, h = height, t = phase
                if (w < 2 || h < 2) return
                ctx.globalCompositeOperation = "source-over"
                ctx.globalAlpha = 1
                ctx.fillStyle = gb
                ctx.fillRect(0, 0, w, h)
                var big = Math.max(w, h)
                var glow = function (col, cx, cy, r) {
                    var g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r)
                    g.addColorStop(0, Qt.rgba(col.r, col.g, col.b, 0.95))
                    g.addColorStop(1, Qt.rgba(col.r, col.g, col.b, 0))
                    ctx.fillStyle = g
                    ctx.fillRect(0, 0, w, h)
                }
                glow(g1, w * (0.25 + 0.12 * Math.sin(t)),        h * (0.28 + 0.12 * Math.cos(t * 1.3)), big * 0.75)
                glow(g2, w * (0.78 + 0.12 * Math.cos(t * 0.9)),  h * (0.72 + 0.12 * Math.sin(t * 1.1)), big * 0.7)
                glow(g3, w * (0.72 + 0.10 * Math.sin(t * 1.2)),  h * (0.18 + 0.10 * Math.cos(t * 0.8)), big * 0.55)
                glow(g4, w * (0.20 + 0.10 * Math.cos(t * 1.1)),  h * (0.80 + 0.10 * Math.sin(t * 0.7)), big * 0.55)
            }
        }
        // A light shade behind the bars and title only, so they stay readable on bright covers.
        Rectangle {
            anchors.fill: parent
            visible: !pp.isVideo
            gradient: Gradient {
                GradientStop { position: 0.0; color: "#00000000" }
                GradientStop { position: 0.55; color: "#00000000" }
                GradientStop { position: 1.0; color: "#40000000" }
            }
        }

        VideoOutput {                                    // a channel's picture, fed by the shared player (see LiveEngine.qml)
            id: liveVideo
            anchors.fill: parent
            visible: pp.isLive
            fillMode: VideoOutput.PreserveAspectFit
            TapHandler { onTapped: pp.toggle(); onDoubleTapped: applicationWindow().visibility === Window.FullScreen ? applicationWindow().showNormal() : applicationWindow().showFullScreen() }
        }
        VideoOutput {
            id: video
            anchors.fill: parent
            visible: pp.isVideo && !pp.isLive
            fillMode: VideoOutput.PreserveAspectFit
            TapHandler { onTapped: pp.toggle(); onDoubleTapped: applicationWindow().visibility === Window.FullScreen ? applicationWindow().showNormal() : applicationWindow().showFullScreen() }
        }

        // Subtitles sit on the picture, so they use fixed light-on-dark colours rather than the desktop theme.
        Rectangle {
            visible: pp.isVideo && pp.subText !== ""
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: parent.height * 0.06
            width: Math.min(parent.width * 0.88, subLabel.implicitWidth + Kirigami.Units.gridUnit)
            height: subLabel.contentHeight + Kirigami.Units.smallSpacing * 2
            color: "#b8000000"
            radius: Kirigami.Units.smallSpacing
            Text {
                id: subLabel
                anchors.centerIn: parent
                width: parent.width - Kirigami.Units.gridUnit
                text: pp.subText
                textFormat: Text.StyledText
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
                color: "white"
                font.pixelSize: Math.max(15, video.height * [0.035, 0.045, 0.06][pp.subSize])
            }
        }

        // Live TV: channel, what's on and what's next. Shows when you tune in or move the mouse, then fades away.
        Rectangle {
            id: osdBox
            visible: pp.isLive && opacity > 0
            opacity: pp.osd || player.playbackState !== MediaPlayer.PlayingState ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 250 } }
            anchors { left: parent.left; top: parent.top; margins: Kirigami.Units.largeSpacing * 2 }
            width: Math.min(parent.width - Kirigami.Units.largeSpacing * 4, Kirigami.Units.gridUnit * 26)
            height: osdCol.implicitHeight + Kirigami.Units.largeSpacing * 2
            radius: Kirigami.Units.largeSpacing
            color: "#c8000000"
            readonly property var cur: pp.nowNext.now || ({})
            readonly property var nxt: pp.nowNext.next || ({})
            function span(p) { return p.title ? marquee.clockText(p.start) + "–" + marquee.clockText(p.end) : "" }
            ColumnLayout {
                id: osdCol
                anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: Kirigami.Units.largeSpacing }
                spacing: 2
                RowLayout {
                    spacing: Kirigami.Units.smallSpacing
                    Rectangle { width: 8; height: 8; radius: 4; color: "#ff4d4d" }
                    Text { text: "LIVE"; color: "#ff8080"; font.pixelSize: 11; font.weight: Font.Bold; font.letterSpacing: 1 }
                    Text { Layout.fillWidth: true; text: pp.info.title || ""; color: "white"; font.pixelSize: 17; font.weight: Font.Bold; elide: Text.ElideRight }
                }
                Text {
                    Layout.fillWidth: true
                    visible: !!osdBox.cur.title
                    text: osdBox.span(osdBox.cur) + "  " + (osdBox.cur.title || "")
                    color: "white"; font.pixelSize: 14; elide: Text.ElideRight
                }
                Rectangle {
                    Layout.fillWidth: true; height: 3; radius: 1.5; color: "#40ffffff"
                    visible: !!osdBox.cur.title
                    Rectangle {
                        height: parent.height; radius: 1.5; color: "#ff8080"
                        width: parent.width * Math.max(0, Math.min(1, (marquee.nowSeconds() - (osdBox.cur.start || 0)) / Math.max(1, (osdBox.cur.end || 1) - (osdBox.cur.start || 0))))
                    }
                }
                Text {
                    Layout.fillWidth: true
                    visible: !!osdBox.nxt.title
                    text: "Next  " + osdBox.span(osdBox.nxt) + "  " + (osdBox.nxt.title || "")
                    color: "#b0ffffff"; font.pixelSize: 12; elide: Text.ElideRight
                }
                Text {
                    Layout.fillWidth: true
                    visible: !osdBox.cur.title && !osdBox.nxt.title
                    text: pp.info.sub || ""
                    color: "#b0ffffff"; font.pixelSize: 12; elide: Text.ElideRight
                }
            }
        }
        QQC2.BusyIndicator {
            anchors.centerIn: parent
            running: pp.isLive && pp.errText === "" && (player.mediaStatus === MediaPlayer.LoadingMedia || player.mediaStatus === MediaPlayer.StalledMedia || player.mediaStatus === MediaPlayer.BufferingMedia)
            visible: running
        }

        // Left: cover, title and bars. With lyrics on and room to spare it takes the left half and the lyrics the right;
        // on a narrow window the lyrics replace it.
        Item {
            id: leftPane
            anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
            width: pp.lyricsShown && pp.wideLayout ? parent.width * 0.46 : parent.width
            visible: !pp.isVideo && !(pp.lyricsShown && !pp.wideLayout)
            Behavior on width { NumberAnimation { duration: 250; easing.type: Easing.OutCubic } }
            // Without lyrics: the visualiser runs the whole width of the window behind the cover, standing on the cover's
            // bottom edge with a faint reflection below it.
            Canvas {
                id: fullViz
                z: 0
                anchors.horizontalCenter: parent.horizontalCenter
                width: parent.width
                height: Math.max(1, artCover.height * 1.3)
                y: artColumn.y + artCover.y + artCover.height - height * 0.72
                visible: !pp.lyricsShown && pp.vizStyle !== "off"
                renderTarget: Canvas.FramebufferObject
                onPaint: pp.paintViz(fullViz, true)
            }
            ColumnLayout {
                id: artColumn
                z: 1
                anchors.centerIn: parent
                spacing: Kirigami.Units.largeSpacing
                Cover {
                    id: artCover
                    Layout.alignment: Qt.AlignHCenter
                    boxSize: Math.min(Kirigami.Units.gridUnit * 18, leftPane.width * 0.7, pp.height * 0.42)
                    ratio: 1
                    source: pp.info.cover || ""
                    title: pp.info.title || ""
                    iconName: pp.info.icon || "audio-x-generic"
                }
                // Text sits on the art-coloured backdrop, which is always dark, so it is fixed light rather than themed.
                Kirigami.Heading { Layout.alignment: Qt.AlignHCenter; Layout.maximumWidth: leftPane.width * 0.8; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap; text: pp.info.title || ""; level: pp.lyricsShown ? 2 : 1; font.weight: Font.Bold; color: "white" }
                QQC2.Label { Layout.alignment: Qt.AlignHCenter; Layout.maximumWidth: leftPane.width * 0.8; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight; text: pp.info.sub || ""; color: "#d6ffffff" }
                Canvas {
                    id: viz
                    Layout.alignment: Qt.AlignHCenter
                    Layout.topMargin: Kirigami.Units.largeSpacing
                    Layout.preferredWidth: Math.min(leftPane.width * 0.8, Kirigami.Units.gridUnit * 36)
                    Layout.preferredHeight: Math.max(Kirigami.Units.gridUnit * 5, Math.min(pp.height * 0.2, Kirigami.Units.gridUnit * 9))
                    visible: pp.lyricsShown && pp.vizStyle !== "off"
                    renderTarget: Canvas.FramebufferObject
                    onPaint: pp.paintViz(viz, false)
                }
            }
        }
        // A narrow window shows only the lyrics, so the visualiser moves behind them: soft bars standing on the bottom edge.
        Canvas {
            id: backViz
            z: 0
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            height: parent.height * 0.3
            opacity: 0.18                       // faint, so the lyrics stay easy to read over it
            visible: pp.lyricsShown && !pp.wideLayout && pp.vizStyle !== "off"
            renderTarget: Canvas.FramebufferObject
            onPaint: pp.paintViz(backViz, true)
        }
        LyricsView {
            anchors {
                right: parent.right; top: parent.top; bottom: parent.bottom
                topMargin: Kirigami.Units.largeSpacing; bottomMargin: Kirigami.Units.largeSpacing
            }
            width: pp.wideLayout ? parent.width * 0.54 : parent.width
            visible: pp.lyricsShown
            rows: pp.lyricRows
            synced: pp.lyricSynced
            status: pp.lyricStatus
            pos: pp.player.position / 1000 + pp.lyricOffset
            onSeekTo: seconds => { pp.player.position = (seconds - pp.lyricOffset) * 1000; seekCheck.target = seconds - pp.lyricOffset; seekCheck.restart() }
        }

        Kirigami.InlineMessage {
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: Kirigami.Units.largeSpacing }
            visible: pp.errText.length > 0
            type: Kirigami.MessageType.Error
            text: pp.errText
            actions: [
                Kirigami.Action { text: "Skip to Next"; icon.name: "media-skip-forward"; onTriggered: if (!pp.step(1)) pp.leave() }
            ]
        }
    }

    // Songs use the same bar as the library, in this page's footer. Videos have their own controls (below).
    footer: MiniPlayer {
        music: pp.music
        visible: !pp.isVideo && pp.music && pp.music.active
        nowPlayingOpen: true
        onOpenRequested: applicationWindow().leavePlayer()
    }

    // The controls are not the page's footer: a footer always keeps its place, while this one floats over the
    // picture in fullscreen and hides, so nothing but the video is left on screen.
    QQC2.ToolBar {
        id: controls
        z: 20
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        visible: pp.isVideo && (!pp.fs || pp.controlsUp)
        HoverHandler { id: footerHover; onPointChanged: pp.moved(point.scenePosition) }
        contentItem: ColumnLayout {
            spacing: Kirigami.Units.smallSpacing
            RowLayout {
                spacing: Kirigami.Units.largeSpacing
                visible: !pp.isLive
                QQC2.Label { text: pp.fmt(player.position) }
                QQC2.Slider {
                    id: seek
                    Layout.fillWidth: true
                    from: 0
                    to: Math.max(1, player.duration)
                    enabled: player.seekable
                    onMoved: player.position = value
                    Binding { target: seek; property: "value"; value: player.position; when: !seek.pressed }
                    QQC2.ToolTip.text: pp.fmt(value)
                    QQC2.ToolTip.visible: pressed
                }
                QQC2.Label { text: pp.fmt(player.duration) }
            }
            RowLayout {
                spacing: Kirigami.Units.smallSpacing
                QQC2.ToolButton { icon.name: "media-skip-backward"; display: QQC2.AbstractButton.IconOnly; text: pp.isLive ? "Previous Channel" : "Previous"; onClicked: pp.back(); QQC2.ToolTip.text: text; QQC2.ToolTip.visible: hovered }
                QQC2.ToolButton {
                    icon.name: player.playbackState === MediaPlayer.PlayingState ? "media-playback-pause" : "media-playback-start"
                    display: QQC2.AbstractButton.IconOnly
                    text: player.playbackState === MediaPlayer.PlayingState ? "Pause" : "Play"
                    onClicked: pp.toggle()
                }
                QQC2.ToolButton { icon.name: "media-skip-forward"; display: QQC2.AbstractButton.IconOnly; text: pp.isLive ? "Next Channel" : "Next"; onClicked: pp.step(1); QQC2.ToolTip.text: text; QQC2.ToolTip.visible: hovered }
                RowLayout {
                    visible: pp.isLive
                    spacing: Kirigami.Units.smallSpacing
                    Rectangle { width: 9; height: 9; radius: 4.5; color: "#ff4d4d" }
                    QQC2.Label { text: "LIVE"; font.bold: true; color: "#ff8080" }
                    QQC2.ToolButton {
                        icon.name: "view-list-details"
                        text: "Guide"
                        onClicked: pp.leave()
                        QQC2.ToolTip.text: "Back to the channel guide"; QQC2.ToolTip.visible: hovered
                    }
                }
                QQC2.ToolButton {
                    visible: !pp.isVideo
                    icon.name: "media-playlist-shuffle"
                    display: QQC2.AbstractButton.IconOnly; text: "Shuffle"; checkable: true; checked: marquee.shuffle
                    onClicked: marquee.toggleShuffle()
                    QQC2.ToolTip.text: "Shuffle " + (marquee.shuffle ? "on" : "off"); QQC2.ToolTip.visible: hovered
                }
                QQC2.ToolButton {
                    visible: !pp.isVideo
                    icon.name: marquee.repeatMode === "one" ? "media-playlist-repeat-song" : "media-playlist-repeat"
                    display: QQC2.AbstractButton.IconOnly; text: "Repeat"; checkable: true; checked: marquee.repeatMode !== "off"
                    onClicked: marquee.cycleRepeat()
                    QQC2.ToolTip.text: marquee.repeatMode === "one" ? "Repeat this song" : (marquee.repeatMode === "all" ? "Repeat all" : "Repeat off")
                    QQC2.ToolTip.visible: hovered
                }
                QQC2.ToolButton {
                    visible: !pp.isVideo
                    icon.name: "view-media-lyrics"
                    text: "Lyrics"; checkable: true; checked: pp.lyricsShown
                    onClicked: pp.toggleLyrics()
                    QQC2.ToolTip.text: "Lyrics (L)"; QQC2.ToolTip.visible: hovered
                }
                RowLayout {
                    visible: pp.lyricsShown && pp.lyricSynced
                    spacing: 0
                    QQC2.ToolButton {
                        icon.name: "go-previous"; display: QQC2.AbstractButton.IconOnly; text: "Lyrics later"
                        onClicked: pp.nudgeLyrics(-0.5)
                        QQC2.ToolTip.text: "Lyrics come too early: show them later (the [ key does it in smaller steps)"; QQC2.ToolTip.visible: hovered
                    }
                    QQC2.Label {
                        text: pp.lyricOffset === 0 ? "Sync" : (pp.lyricOffset > 0 ? "+" : "") + pp.lyricOffset.toFixed(1) + "s"
                        opacity: 0.8
                        MouseArea { anchors.fill: parent; onDoubleClicked: { pp.lyricOffset = 0; marquee.setLyricOffset(pp.itemId, 0) } }
                    }
                    QQC2.ToolButton {
                        icon.name: "go-next"; display: QQC2.AbstractButton.IconOnly; text: "Lyrics earlier"
                        onClicked: pp.nudgeLyrics(0.5)
                        QQC2.ToolTip.text: "Lyrics come too late: show them earlier (the ] key does it in smaller steps; double-click the number to reset)"; QQC2.ToolTip.visible: hovered
                    }
                }
                Item { Layout.fillWidth: true }
                QQC2.ToolButton {
                    icon.name: pp.mutedNow || volumeSlider.value === 0 ? "player-volume-muted" : "player-volume"
                    display: QQC2.AbstractButton.IconOnly
                    text: "Mute"
                    onClicked: pp.toggleMute()
                }
                QQC2.Slider {
                    id: volumeSlider
                    from: 0; to: 1
                    value: pp.isVideo ? 1 : pp.music.volume
                    onMoved: { if (!pp.isVideo) pp.music.volume = value }
                    implicitWidth: Kirigami.Units.gridUnit * 6
                }
                QQC2.ComboBox {
                    id: qualityBox
                    visible: pp.isVideo && !!info.yt && !pp.isLive
                    readonly property var keys: ["auto"].concat((info.levels || []).slice().sort(function (a, b) { return b - a }).map(String))
                    model: keys.map(function (k) { return k === "auto" ? "Auto · " + (info.res || 360) + "p" : k + "p" })
                    readonly property bool waiting: info.upgradeTo > 0 && (info.res || 360) < info.upgradeTo
                    currentIndex: Math.max(0, keys.indexOf(info.quality || "auto"))
                    displayText: waiting ? (info.res || 360) + "p, getting " + info.upgradeTo + "p…" : ((info.quality || "auto") === "auto" ? "Auto · " + (info.res || 360) + "p" : currentText)
                    QQC2.ToolTip.text: "Video quality"
                    QQC2.ToolTip.visible: hovered
                    onActivated: { var at = player.position / 1000; player.pause(); youtube.reopen(info.id.substring(3), at, keys[currentIndex]) }
                }
                QQC2.ToolButton {
                    id: ccButton
                    visible: pp.isVideo && !pp.isLive && !info.yt
                    text: "CC"
                    font.bold: true
                    checked: pp.subSel !== ""
                    QQC2.ToolTip.text: "Subtitles (C)"
                    QQC2.ToolTip.visible: hovered
                    onClicked: ccMenu.popup(ccButton, 0, -ccMenu.height)

                    QQC2.Menu {
                        id: ccMenu
                        QQC2.MenuItem { text: "Off"; checkable: true; checked: pp.subSel === ""; onTriggered: pp.pickSubtitle("") }
                        Instantiator {
                            model: pp.subTracks
                            delegate: QQC2.MenuItem {
                                required property var modelData
                                text: modelData.label + (modelData.source === "embedded" ? "  (in file)" : "")
                                checkable: true
                                checked: pp.subSel === modelData.id
                                onTriggered: pp.pickSubtitle(modelData.id)
                            }
                            onObjectAdded: (index, object) => ccMenu.insertItem(index + 1, object)
                            onObjectRemoved: (index, object) => ccMenu.removeItem(object)
                        }
                        QQC2.MenuItem {
                            enabled: false
                            visible: pp.subTracks.length === 0
                            height: visible ? implicitHeight : 0
                            text: pp.subNote !== "" ? pp.subNote : "No subtitles found"
                        }
                        QQC2.MenuSeparator {}
                        QQC2.MenuItem { text: "Add Subtitle File…"; icon.name: "list-add"; onTriggered: subFile.open() }
                        QQC2.MenuItem { text: "Download Subtitles"; icon.name: "download"; onTriggered: marquee.downloadSubtitles(pp.itemId) }
                        QQC2.MenuItem { text: "Subtitle Settings…"; icon.name: "configure"; onTriggered: applicationWindow().openSubtitleSettings() }
                        QQC2.MenuSeparator {}
                        QQC2.MenuItem { text: "Sync Subtitles to the Speech"; icon.name: "view-refresh"; enabled: pp.subSel !== ""; onTriggered: marquee.syncSubtitles(pp.itemId) }
                        QQC2.MenuItem { text: "Sync Downloaded Subtitles Automatically"; checkable: true; checked: marquee.autoSyncSubtitles(); onTriggered: marquee.setAutoSyncSubtitles(checked) }
                        QQC2.MenuItem { text: "Show Earlier (−0.5 s)"; onTriggered: pp.nudgeSubtitles(-0.5) }
                        QQC2.MenuItem { text: "Show Later (+0.5 s)"; onTriggered: pp.nudgeSubtitles(0.5) }
                        QQC2.MenuItem { text: "Show Earlier a Little (−0.1 s)"; onTriggered: pp.nudgeSubtitles(-0.1) }
                        QQC2.MenuItem { text: "Show Later a Little (+0.1 s)"; onTriggered: pp.nudgeSubtitles(0.1) }
                        QQC2.MenuItem { enabled: false; text: "Timing: " + (pp.subDelay > 0 ? "+" : "") + pp.subDelay.toFixed(1) + " s" }
                        QQC2.Menu {
                            title: "Size"
                            QQC2.MenuItem { text: "Small"; checkable: true; checked: pp.subSize === 0; onTriggered: pp.setSize(0) }
                            QQC2.MenuItem { text: "Medium"; checkable: true; checked: pp.subSize === 1; onTriggered: pp.setSize(1) }
                            QQC2.MenuItem { text: "Large"; checkable: true; checked: pp.subSize === 2; onTriggered: pp.setSize(2) }
                        }
                    }
                }
                QQC2.ComboBox {
                    visible: !pp.isVideo
                    model: pp.vizStyles.map(function (x) { return x[1] })
                    currentIndex: Math.max(0, pp.vizStyles.findIndex(function (x) { return x[0] === pp.vizStyle }))
                    onActivated: pp.setViz(pp.vizStyles[currentIndex][0])
                    QQC2.ToolTip.text: "Visualiser style"
                    QQC2.ToolTip.visible: hovered
                }
                QQC2.ComboBox { id: speed; visible: pp.isVideo && !pp.isLive; model: ["0.75×", "1×", "1.25×", "1.5×", "2×"]; currentIndex: 1 }
                QQC2.ToolButton {
                    icon.name: applicationWindow().visibility === Window.FullScreen ? "view-restore" : "view-fullscreen"
                    display: QQC2.AbstractButton.IconOnly
                    text: "Fullscreen"
                    onClicked: applicationWindow().visibility === Window.FullScreen ? applicationWindow().showNormal() : applicationWindow().showFullScreen()
                }
            }
        }
    }
    Rectangle {
        anchors.fill: parent
        visible: !!info.yt && youtube.resolving !== ""
        color: "#cc000000"
        z: 50
        MouseArea { anchors.fill: parent }
        ColumnLayout {
            anchors.centerIn: parent
            QQC2.BusyIndicator { Layout.alignment: Qt.AlignHCenter; running: parent.visible }
            Text { Layout.alignment: Qt.AlignHCenter; text: "Loading video…"; color: "white"; font.pixelSize: 16 }
            Text { Layout.alignment: Qt.AlignHCenter; visible: youtube.note !== ""; text: "Downloading " + youtube.note; color: "#ccffffff"; font.pixelSize: 13 }
        }
    }
    // YouTube started at 360p so it opened quickly; the bigger picture downloads behind it and replaces it when ready
    Connections {
        target: youtube
        function onUpgradeReady(vid) {
            if (!!pp.info.yt && pp.info.id === "yt:" + vid && pp.info.upgradeTo > 0 && !pp.isLive)
                youtube.applyUpgrade(vid, pp.player.position / 1000)
        }
    }
    Rectangle {
        id: upBadge
        visible: !!pp.info.yt && pp.info.upgradeTo > 0 && youtube.upgrading === pp.info.id.substring(3)
        anchors { top: parent.top; right: parent.right; margins: Kirigami.Units.largeSpacing }
        z: 40
        radius: 4
        color: "#99000000"
        width: upLabel.implicitWidth + 16
        height: upLabel.implicitHeight + 8
        Text { id: upLabel; anchors.centerIn: parent; color: "white"; font.pixelSize: 12
               text: "Getting " + pp.info.upgradeTo + "p" + (youtube.note !== "" ? " · " + youtube.note : "…") }
    }
    Rectangle {
        visible: pp.subBadge !== ""
        anchors { top: parent.top; right: parent.right; margins: Kirigami.Units.largeSpacing }
        anchors.topMargin: Kirigami.Units.largeSpacing + (upBadge.visible ? upBadge.height + 6 : 0)
        z: 40
        radius: 4
        color: "#99000000"
        width: Math.min(subBadgeLabel.implicitWidth + 16, pp.width * 0.6)
        height: subBadgeLabel.implicitHeight + 8
        Text { id: subBadgeLabel; anchors.centerIn: parent; width: parent.width - 16; color: "white"; font.pixelSize: 12
               elide: Text.ElideRight; horizontalAlignment: Text.AlignHCenter; text: pp.subBadge }
    }
}
