import QtQuick
import QtMultimedia

// The window's one live-TV player. The preview on the Live TV page and the full-size player page both show this
// same stream, passing the picture between them, so going full size and back never restarts or pauses the channel.
Item {
    id: eng
    readonly property alias player: mp
    property string channelId: ""
    property string title: ""
    property string errorText: ""
    property alias muted: ao.muted
    property alias volume: ao.volume

    // Show the picture in `out` (a VideoOutput). Only ever set once: the Live TV page keeps a single output.
    function attach(out) { if (out && mp.videoOutput !== out) mp.videoOutput = out }
    function release(out) { if (mp.videoOutput === out) mp.videoOutput = null }
    // Tune to a channel. The channel that is already playing is left alone.
    function play(id) {
        if (id === channelId && mp.source.toString() !== "" && errorText === "") {
            if (mp.playbackState !== MediaPlayer.PlayingState) mp.play()
            return
        }
        var info = marquee.itemInfo(id)
        errorText = ""
        channelId = id
        title = info.title || ""
        mp.source = info.url || ""
        mp.play()
    }
    function stop() {
        mp.stop()
        mp.source = ""
        channelId = ""
        errorText = ""
    }
    MediaPlayer {
        id: mp
        audioOutput: AudioOutput { id: ao }
        onErrorOccurred: (error, message) => {
            eng.errorText = "Couldn’t open “" + eng.title + "”. " + message + " The channel may be offline, or the playlist link may have expired."
        }
        onMediaStatusChanged: if (mediaStatus === MediaPlayer.EndOfMedia)
            eng.errorText = "The stream for “" + eng.title + "” ended. The channel may be off air, or your connection dropped."
    }
}
