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
        triedUpgrade = false
        offered = []
        mp.source = info.url || ""
        mp.play()
        marquee.probeLive(id)                               // what sizes does this channel offer? (printed in the terminal)
    }
    function stop() {
        mp.stop()
        mp.source = ""
        channelId = ""
        errorText = ""
        offered = []
        checkTimer.stop()
    }
    // When the stream holds several picture sizes and the player started a smaller one, show the biggest.
    property bool triedUpgrade: false
    property var offered: []
    Connections {
        target: marquee
        function onLiveVariants(id, list) {
            if (id !== "live:" + eng.channelId && id !== eng.channelId) return
            eng.offered = list
            if (!eng.triedUpgrade) checkTimer.restart()
        }
    }
    Timer {
        id: checkTimer
        interval: 3000                                      // give the player time to open the stream and list its pictures
        onTriggered: {
            if (eng.triedUpgrade) return
            eng.triedUpgrade = true
            // The stream stays as it is (the sound is a separate part of the list, so swapping the address would lose it);
            // only the picture is chosen: the biggest one the player found inside it.
            var tracks = mp.videoTracks
            var bestAt = -1, bestH = 0, curH = 0
            for (var i = 0; i < tracks.length; i++) {
                var r = tracks[i].value(MediaMetaData.Resolution)
                var h = r ? r.height : 0
                if (i === mp.activeVideoTrack) curH = h
                if (h > bestH) { bestH = h; bestAt = i }
            }
            console.log("marquee: live: " + eng.title + ": picture " + (curH ? curH + "p" : "unknown") + " of " + tracks.length + " found in the stream, biggest " + (bestH ? bestH + "p" : "unknown"))
            if (bestAt >= 0 && bestH > curH && bestAt !== mp.activeVideoTrack) {
                console.log("marquee: live: " + eng.title + ": showing the " + bestH + "p picture")
                mp.activeVideoTrack = bestAt
            }
        }
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
