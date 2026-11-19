import QtQuick
import QtMultimedia

// The window's podcast player. Like the music player it lives here, not on a page, so an episode keeps playing while
// you look at something else. The Podcasts page shows and controls it.
Item {
    id: eng
    visible: false
    width: 0
    height: 0

    property var info: ({})
    property string key: ""
    property string errorText: ""
    property real rate: podcasts ? podcasts.speed : 1
    property real resumeAt: 0                       // seconds to jump to once the file is open
    readonly property alias player: mp
    readonly property bool active: key !== ""
    readonly property bool playing: mp.playbackState === MediaPlayer.PlayingState
    property alias volume: ao.volume
    property alias muted: ao.muted

    function play(epKey) {
        var i = podcasts.episodeInfo(epKey)
        if (!i.url) return
        if (key === epKey && mp.source.toString() !== "") { if (!playing) mp.play(); return }
        save()
        key = epKey
        info = i
        errorText = ""
        resumeAt = i.pos
        mp.stop()
        mp.source = i.url
        mp.play()
    }
    function toggle() { playing ? mp.pause() : mp.play() }
    function pause() { if (playing) mp.pause() }
    function skip(seconds) { mp.position = Math.max(0, Math.min(mp.duration > 0 ? mp.duration : 1e12, mp.position + seconds * 1000)) }
    function seek(fraction) { if (mp.duration > 0) mp.position = fraction * mp.duration }
    function save() { if (key !== "" && mp.duration > 0) podcasts.savePosition(key, mp.position / 1000, mp.duration / 1000) }
    function stop() { save(); mp.stop(); mp.source = ""; key = ""; info = ({}) }

    // where you stopped, written every few seconds while it plays
    Timer { interval: 5000; repeat: true; running: eng.playing; onTriggered: eng.save() }

    MediaPlayer {
        id: mp
        audioOutput: AudioOutput { id: ao }
        playbackRate: eng.rate
        onMediaStatusChanged: {
            if ((mediaStatus === MediaPlayer.LoadedMedia || mediaStatus === MediaPlayer.BufferedMedia) && eng.resumeAt > 5) {
                var at = eng.resumeAt
                eng.resumeAt = 0
                position = at * 1000
            } else if (mediaStatus === MediaPlayer.EndOfMedia && eng.key !== "") {
                podcasts.markPlayed(eng.key, true)
            }
        }
        onErrorOccurred: (error, message) => {
            eng.errorText = "This episode could not be played. " + message
        }
    }
}
