import QtQuick
import QtMultimedia

// The music player. It lives in the main window, not on a page, so a song keeps playing while you browse the
// library; the bar at the bottom and the Now Playing page both just control and show this.
Item {
    id: eng
    visible: false
    width: 0
    height: 0

    property string itemId: ""
    property var info: ({})
    property var pal: ({ c1: "#2a2f45", c2: "#40304a", base: "#14161f", bar: "#9fb4ff", bar2: "#c2a0ff" })
    property real volume: 1
    property bool muted: false
    property string errorText: ""
    property bool lyricsOn: marquee.lyricsDefault()      // the starting choice is a setting; the Lyrics button changes it for now
    readonly property alias player: mp
    readonly property bool active: itemId !== ""
    readonly property bool playing: mp.playbackState === MediaPlayer.PlayingState
    signal trackChanged()

    function play(id) {
        var i = marquee.itemInfo(id)
        itemId = id
        info = i
        errorText = ""
        try { var p = marquee.paletteFor(id); if (p && p.base) pal = p } catch (e) { console.warn("palette:", e) }
        marquee.noteTrack(id)
        if (!i.exists) {
            mp.stop(); mp.source = ""
            errorText = "This file is no longer at " + String(i.url).replace("file://", "") + ". Remove it from the library or restore the file."
        } else {
            mp.source = i.url
            mp.play()
            try { marquee.startVisualizer(id) } catch (e) { console.warn("visualiser:", e) }
        }
        trackChanged()
    }
    function next(auto) {
        var n = marquee.nextTrack(auto)
        if (n !== "") play(n)
        else if (auto) stop()
    }
    function previous() {
        if (mp.position > 3000) { mp.position = 0; return }
        var n = marquee.previousTrack()
        if (n !== "") play(n)
        else mp.position = 0
    }
    function toggle() { playing ? mp.pause() : mp.play() }
    function stop() {
        mp.stop()
        mp.source = ""
        itemId = ""
        info = ({})
        marquee.stopVisualizer()
        trackChanged()
    }

    MediaPlayer {
        id: mp
        audioOutput: AudioOutput { volume: eng.volume; muted: eng.muted }
        onMediaStatusChanged: {
            if (mediaStatus === MediaPlayer.EndOfMedia) { marquee.finished(eng.itemId); eng.next(true) }
        }
        onErrorOccurred: (error, message) => {
            eng.errorText = "This system could not play “" + (eng.info.title || "this song") + "”. " + message +
                            " Installing the GStreamer or FFmpeg plugins for your distribution usually fixes missing formats."
        }
    }
}
