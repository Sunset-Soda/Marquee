import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import QtMultimedia
import org.kde.kirigami as Kirigami

// Twitch: sign in once and see the channels you follow, live ones first. Click one to watch it.
// Twitch puts its ads into the stream itself; the ad-free switch (under Settings) asks for the stream another way
// and falls back to the normal one when that doesn't work.
Kirigami.Page {
    id: pg
    title: "Twitch"
    padding: 0

    property var music                              // the window's music player; it stops when a channel starts
    property var pod                                // the window's podcast player; it pauses too
    property string phase: "browse"                 // browse | loading | playing
    property bool showSettings: false
    property bool muted: false
    readonly property bool setup: !twitch.signedIn

    function watch(text) {
        nudges = 0
        stillFor = 0
        lastPos = -1
        posMoved = false
        ticks = 0
        if (music) music.stop()
        if (pod) pod.pause()
        player.stop()
        errorNote = ""
        phase = "loading"
        twitch.play(text)
    }
    function leave() {
        reconnects = 0
        player.stop()
        player.source = ""
        twitch.stop()
        phase = "browse"
        twitch.refresh()
    }
    function compact(n) { return n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "K" : String(n) }

    Component.onCompleted: { if (twitch.signedIn) twitch.refresh(); showSettings = !twitch.signedIn && twitch.clientId === "" }
    Component.onDestruction: { player.stop(); if (twitch) twitch.stopRecording() }

    // what the media keys and the desktop's media widget see (Main.qml)
    readonly property bool mediaPlaying: phase === "playing"
    readonly property bool mediaIsPlaying: player.playbackState === MediaPlayer.PlayingState
    readonly property string mediaTitle: twitch.channel
    readonly property string mediaSub: (twitch.stream && twitch.stream.title) || "Twitch"
    function mediaToggle() { if (player.playbackState === MediaPlayer.PlayingState) player.pause(); else player.play() }
    function mediaPause() { player.pause() }
    function jumpLive() { if (dvr.alive) openMain(0); else watch(twitch.channel) }

    // ---- pause / rewind / Live (the channel is kept on disk while you watch) ----
    property var dvr: ({ alive: false })
    property real partStart: 0                      // stream time at which the playlist now playing began (the channel's floor, or the break's start)
    property bool scrubbing: false
    readonly property real behind: dvr.alive ? Math.max(0, dvr.live - (partStart + player.position / 1000)) : 0
    function syncPart() {
        var st = twitch.dvrStatus()
        dvr = st
        partStart = st.alive ? (inAd ? st.adStart : st.floor) : 0
    }
    // play the channel from `back` seconds behind live (0 = live); a break that falls there plays first
    function openMain(back) {
        stillFor = 0; lastPos = -1; posMoved = false; ticks = 0; nudges = 0
        player.stop()
        inAd = twitch.seekBack(back)
        opens++
        syncPart()
        console.log("marquee: twitch: opening at " + Math.round(back) + "s behind live; piece starts at stream time " + Math.round(partStart) + (inAd ? " (break)" : ""))
        player.source = (inAd ? twitch.stream.adUrl : twitch.stream.url) + "?" + opens
        player.play()
    }
    Timer {
        interval: 1000; repeat: true; running: pg.phase === "playing"
        onTriggered: if (!pg.scrubbing) pg.dvr = twitch.dvrStatus()
    }
    function fmtLen(s) {
        s = Math.max(0, Math.round(s))
        return Math.floor(s / 60) + ":" + (s % 60 < 10 ? "0" : "") + (s % 60)
    }
    function fmtBehind(s) {
        s = Math.round(s)
        return "-" + Math.floor(s / 60) + ":" + (s % 60 < 10 ? "0" : "") + (s % 60)
    }

    Timer { interval: 60000; repeat: true; running: pg.visible && pg.phase === "browse" && twitch.signedIn; onTriggered: twitch.refresh() }

    Connections {
        target: twitch
        function onStreamReady(login) {
            if (pg.phase !== "loading") return
            pg.inAd = !!twitch.stream.startsInAd
            pg.syncPart()
            player.source = pg.inAd ? twitch.stream.adUrl : twitch.stream.url
            player.play()
            pg.phase = "playing"
        }
        function onStreamFailed(login, message) { if (pg.phase === "loading") pg.phase = "browse" }
    }

    MediaPlayer {
        id: player
        audioOutput: AudioOutput { muted: pg.muted; volume: volSlider.value }
        videoOutput: video
        onErrorOccurred: (error, message) => {
            console.log("marquee: twitch: player error: " + message)
            if (pg.phase === "playing") { pg.phase = "browse"; twitch.stop() }
            pg.errorNote = "This channel could not be played. " + message
        }
        onMediaStatusChanged: {
            console.log("marquee: twitch: player status " + mediaStatus + ", video " + hasVideo + ", audio " + hasAudio)
            if (mediaStatus === MediaPlayer.EndOfMedia && pg.phase === "playing" && (pg.inAd || twitch.adPending())) {
                // the channel has reached a commercial break (play it in full), or the break is over (back to the channel)
                pg.stillFor = 0; pg.lastPos = -1; pg.posMoved = false; pg.ticks = 0; pg.nudges = 0
                player.stop()
                if (pg.inAd) {
                    console.log("marquee: twitch: the commercial break is over; back to the channel")
                    pg.inAd = false
                    twitch.advance()
                    pg.syncPart()
                    pg.opens++
                    player.source = twitch.stream.url + "?" + pg.opens
                } else {
                    console.log("marquee: twitch: a commercial break is next; playing it")
                    pg.inAd = true
                    pg.syncPart()
                    pg.opens++
                    player.source = twitch.stream.adUrl + "?" + pg.opens
                }
                player.play()
                return
            }
            if ((mediaStatus === MediaPlayer.EndOfMedia || mediaStatus === MediaPlayer.InvalidMedia) && pg.phase === "playing")
                pg.reconnect("the stream ended")
        }
        onPlaybackStateChanged: {
            console.log("marquee: twitch: playback state " + playbackState)
            // a paused live stream falls behind what Twitch still keeps: resuming after a while goes back to live
            if (playbackState === MediaPlayer.PausedState) pg.pausedAt = Date.now()
            else if (playbackState === MediaPlayer.PlayingState && pg.pausedAt > 0) {
                var longPause = Date.now() - pg.pausedAt > 15000
                pg.pausedAt = 0
                if (longPause && pg.phase === "playing" && !pg.dvr.alive) { console.log("marquee: twitch: resumed after a pause; going back to live"); Qt.callLater(function () { pg.watch(twitch.channel) }) }
            }
        }
    }
    // while a channel plays, say every few seconds whether the picture is really moving (for finding a blank screen)
    Timer {
        interval: 3000; repeat: true; running: pg.phase === "playing"
        onTriggered: {
            var pos = player.position
            if (player.playbackState === MediaPlayer.PlayingState && pg.posMoved) {
                if (pos === pg.lastPos) pg.stillFor++
                else { pg.stillFor = 0; if (pos > 60000) pg.reconnects = 0 }
            } else pg.stillFor = 0
            if (pg.lastPos >= 0 && pos !== pg.lastPos && pos > 0) pg.posMoved = true
            pg.ticks++
            // the clock never started (picture and sound both stuck at the very start): treat it like a stall
            if (!pg.posMoved && pg.ticks >= 6 && player.playbackState === MediaPlayer.PlayingState) pg.reconnect("the stream never started (position 0 after " + pg.ticks * 3 + "s)")
            pg.lastPos = pos
            if (pg.stillFor >= 2) {
                pg.stillFor = 0
                if (pg.nudges < 3) {
                    // The clock often sticks at the join between the commercial-break picture and the channel: jump a little past it.
                    pg.nudges++
                    var to = pos + pg.nudges * 2000
                    console.log("marquee: twitch: playback stalled at " + Math.round(pos / 1000) + "s; skipping ahead to " + Math.round(to / 1000) + "s (" + pg.nudges + ")")
                    player.position = to
                    if (player.playbackState !== MediaPlayer.PlayingState) player.play()
                } else pg.reconnect("playback stalled at " + Math.round(pos / 1000) + "s")
            }
        }
    }
    Timer {
        interval: 4000; repeat: true; running: pg.phase === "playing"
        onTriggered: console.log("marquee: twitch: position " + Math.round(player.position / 1000) + "s, video " + player.hasVideo +
                                 ", picture " + Math.round(video.contentRect.width) + "x" + Math.round(video.contentRect.height) +
                                 ", tracks " + player.videoTracks.length)
    }
    property string errorNote: ""
    // A stream can stall when Twitch switches from its commercial-break picture to the channel (picture and sound stop).
    // When playback is meant to be running but the position has not moved for a few seconds, ask for a fresh stream.
    property real lastPos: -1
    property int stillFor: 0
    property int ticks: 0                           // checks since this stream opened
    property bool posMoved: false                   // the player's clock has been seen running (some live streams never report one)
    property int reconnects: 0
    property bool inAd: false                       // the commercial break at the start is playing (the channel follows it)
    property int opens: 0                           // how many times a playlist has been (re)opened, to keep each request distinct
    property double pausedAt: 0
    property int nudges: 0                          // skips past a stall tried on this stream
    property real playingSince: 0
    function reconnect(why) {
        if (reconnects >= 5) { errorNote = "The stream keeps stalling. Press Back and try again in a moment."; leave(); return }
        reconnects++
        inAd = false
        nudges = 0
        stillFor = 0
        lastPos = -1
        posMoved = false
        ticks = 0
        twitch.probe(why)
        if (reconnects <= 3 && twitch.stream.url && dvr.alive) {
            console.log("marquee: twitch: " + why + "; reopening where it was (" + reconnects + ")")
            openMain(behind < 6 ? 0 : behind)
        } else if (reconnects <= 3 && twitch.stream.url) {
            // Same stream address again: by now the commercial break has left its playlist, so it carries on with the channel.
            // (Asking Twitch for a new stream instead would start with a new commercial break.)
            console.log("marquee: twitch: " + why + "; reopening the stream (" + reconnects + ")")
            player.stop()
            player.source = twitch.stream.url
            player.play()
        } else {
            console.log("marquee: twitch: " + why + "; getting a fresh stream (" + reconnects + ")")
            var keep = reconnects
            watch(twitch.channel)
            reconnects = keep
        }
    }

    // a round or square picture that falls back to a plain tile
    component Pic: Item {
        id: pic
        property string source: ""
        property bool round: false
        Rectangle { anchors.fill: parent; radius: pic.round ? width / 2 : 6; color: Kirigami.Theme.alternateBackgroundColor }
        Image {
            anchors.fill: parent
            source: pic.source
            asynchronous: true
            fillMode: Image.PreserveAspectCrop
            sourceSize.width: 440
            sourceSize.height: 440
        }
    }

    // ---------------- watching ----------------
    Item {
        id: stage
        anchors.fill: parent
        visible: pg.phase !== "browse"
        Rectangle { anchors.fill: parent; color: "black" }
        VideoOutput { id: video; anchors.fill: parent; fillMode: VideoOutput.PreserveAspectFit }
        MouseArea { anchors.fill: parent; onClicked: player.playbackState === MediaPlayer.PlayingState ? player.pause() : player.play() }

        QQC2.BusyIndicator { anchors.centerIn: parent; running: pg.phase === "loading"; visible: running }
        QQC2.Label {
            anchors { horizontalCenter: parent.horizontalCenter; top: parent.verticalCenter; topMargin: Kirigami.Units.gridUnit * 2 }
            visible: pg.phase === "loading"
            color: "white"; opacity: 0.8
            text: "Opening " + twitch.channel + "…" + (twitch.adFree ? "  (ad-free mode may take a few seconds longer)" : "")
        }

        Rectangle {
            anchors { left: parent.left; right: parent.right; top: parent.top }
            height: topRow.implicitHeight + Kirigami.Units.largeSpacing * 2
            gradient: Gradient {
                GradientStop { position: 0; color: "#cc000000" }
                GradientStop { position: 1; color: "transparent" }
            }
            RowLayout {
                id: topRow
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: Kirigami.Units.largeSpacing }
                spacing: Kirigami.Units.smallSpacing
                QQC2.Button { text: "Back"; icon.name: "go-previous"; onClicked: pg.leave() }
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    spacing: 0
                    QQC2.Label { text: twitch.channel; color: "white"; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                    QQC2.Label { text: twitch.stream.title || ""; color: "white"; opacity: 0.8; elide: Text.ElideRight; Layout.fillWidth: true; font: Kirigami.Theme.smallFont }
                }
                QQC2.Label { text: twitch.stream.height ? twitch.stream.height + "p" : ""; color: "white"; opacity: 0.8 }
            }
        }
        // the play bar: pause, drag back through what has been kept, Live, volume
        QQC2.ToolBar {
            id: rewind
            z: 20
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            visible: pg.phase === "playing"
            contentItem: ColumnLayout {
                spacing: Kirigami.Units.smallSpacing
                RowLayout {
                    spacing: Kirigami.Units.largeSpacing
                    visible: pg.dvr.alive && pg.dvr.live - pg.dvr.oldest > 6
                    QQC2.Label { text: pg.scrubbing ? pg.fmtBehind(rewSlider.to - rewSlider.value) : (pg.behind > 10 ? pg.fmtBehind(pg.behind) : "Live") }
                    QQC2.Slider {
                        id: rewSlider
                        Layout.fillWidth: true
                        from: 0
                        to: Math.max(1, pg.dvr.live - pg.dvr.oldest)
                        // The handle follows the picture, except while you hold it. A click or drag that really moved it rewinds to there;
                        // letting go without having moved it does nothing.
                        function follow() { if (!pressed && !pg.scrubbing) value = Math.max(0, to - pg.behind) }
                        Component.onCompleted: follow()
                        onToChanged: follow()
                        Connections { target: pg; function onBehindChanged() { rewSlider.follow() } }
                        onPressedChanged: {
                            if (pressed) {
                                pg.scrubbing = true
                            } else if (pg.scrubbing) {
                                var back = to - value
                                var now = pg.behind
                                pg.scrubbing = false
                                if (Math.abs(back - now) > 3) {
                                    console.log("marquee: twitch: slider released at " + Math.round(back) + "s behind (was " + Math.round(now) + "s)")
                                    pg.openMain(back < 8 ? 0 : back)
                                } else follow()
                            }
                        }
                        QQC2.ToolTip.text: pg.fmtBehind(rewSlider.to - rewSlider.value)
                        QQC2.ToolTip.visible: pressed
                    }
                    QQC2.Label { text: pg.fmtLen(pg.dvr.live - pg.dvr.oldest) + " recorded" }
                }
                RowLayout {
                    spacing: Kirigami.Units.smallSpacing
                    QQC2.ToolButton {
                        icon.name: player.playbackState === MediaPlayer.PlayingState ? "media-playback-pause" : "media-playback-start"
                        display: QQC2.AbstractButton.IconOnly
                        text: player.playbackState === MediaPlayer.PlayingState ? "Pause" : "Play"
                        onClicked: player.playbackState === MediaPlayer.PlayingState ? player.pause() : player.play()
                    }
                    Item { Layout.fillWidth: true }
                    QQC2.ToolButton {
                        text: "● Live"
                        highlighted: player.playbackState !== MediaPlayer.PlayingState || pg.behind > 10
                        onClicked: { if (pg.dvr.alive) pg.openMain(0); else pg.watch(twitch.channel) }
                        QQC2.ToolTip.text: "Jump back to the live broadcast"; QQC2.ToolTip.visible: hovered
                    }
                    QQC2.ComboBox {
                        id: qBox
                        visible: pg.phase === "playing"
                        model: ["Best"].concat((twitch.stream.heights || []).map(function (h) { return h + "p" }))
                        currentIndex: {
                            var hs = twitch.stream.heights || []
                            var i = hs.indexOf(parseInt(twitch.quality))
                            return twitch.quality === "best" || i < 0 ? 0 : i + 1
                        }
                        onActivated: (i) => {
                            twitch.setQuality(i === 0 ? "best" : String((twitch.stream.heights || [])[i - 1]))
                            pg.watch(twitch.channel)
                        }
                    }
                    QQC2.ToolButton {
                        icon.name: pg.muted || volSlider.value === 0 ? "player-volume-muted" : "player-volume"
                        display: QQC2.AbstractButton.IconOnly
                        text: pg.muted ? "Unmute" : "Mute"
                        onClicked: pg.muted = !pg.muted
                    }
                    QQC2.Slider {
                        id: volSlider
                        from: 0; to: 1; value: 1
                        implicitWidth: Kirigami.Units.gridUnit * 6
                    }
                }
            }
        }
        QQC2.Label {
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom; bottomMargin: rewind.height + Kirigami.Units.smallSpacing; leftMargin: Kirigami.Units.largeSpacing; rightMargin: Kirigami.Units.largeSpacing }
            visible: pg.phase === "playing" && !!(twitch.stream.note || twitch.stream.adfree)
            color: "white"
            opacity: 0.85
            wrapMode: Text.Wrap
            text: twitch.stream.note ? twitch.stream.note : "Ad-free route in use."
        }
    }

    // ---------------- browsing ----------------
    ColumnLayout {
        anchors.fill: parent
        visible: pg.phase === "browse"
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.smallSpacing
            spacing: Kirigami.Units.smallSpacing
            Pic { visible: twitch.signedIn; round: true; source: twitch.userImage; Layout.preferredWidth: Kirigami.Units.gridUnit * 2; Layout.preferredHeight: Kirigami.Units.gridUnit * 2 }
            QQC2.Label { visible: twitch.signedIn; text: twitch.userName; font.bold: true }
            QQC2.TextField {
                id: chanField
                Layout.fillWidth: true
                placeholderText: "Watch any channel: type its name or paste a twitch.tv link"
                onAccepted: { pg.watch(text); text = "" }
            }
            QQC2.Button { text: "Watch"; icon.name: "media-playback-start"; enabled: chanField.text !== ""; onClicked: { pg.watch(chanField.text); chanField.text = "" } }
            QQC2.ToolButton {
                visible: twitch.signedIn
                icon.name: "view-refresh"
                enabled: !twitch.busy
                onClicked: twitch.refresh()
                QQC2.ToolTip.text: "Refresh who is live"; QQC2.ToolTip.visible: hovered
            }
            QQC2.ToolButton {
                visible: twitch.signedIn
                icon.name: twitch.listView ? "view-list-icons" : "view-list-details"
                onClicked: twitch.setListView(!twitch.listView)
                QQC2.ToolTip.text: twitch.listView ? "Show as tiles" : "Show as a list"; QQC2.ToolTip.visible: hovered
            }
            QQC2.ToolButton {
                icon.name: "configure"
                checkable: true
                checked: pg.showSettings
                onToggled: pg.showSettings = checked
                QQC2.ToolTip.text: "Twitch settings"; QQC2.ToolTip.visible: hovered
            }
        }

        QQC2.Label {
            Layout.fillWidth: true
            Layout.leftMargin: Kirigami.Units.largeSpacing
            Layout.rightMargin: Kirigami.Units.largeSpacing
            visible: (twitch.error !== "" || pg.errorNote !== "")
            wrapMode: Text.Wrap
            color: Kirigami.Theme.negativeTextColor
            text: twitch.error !== "" ? twitch.error : pg.errorNote
        }

        // ---- settings and sign-in ----
        ColumnLayout {
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.largeSpacing
            visible: pg.showSettings || pg.setup
            spacing: Kirigami.Units.smallSpacing

            QQC2.Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                visible: pg.setup
                text: "To sign in, Twitch needs a free developer application of your own (this is how every app that uses Twitch’s login works):\n" +
                      "1. Go to dev.twitch.tv/console and sign in.\n" +
                      "2. Press “Register Your Application”. Any name; OAuth Redirect URL: http://localhost; Category: Other; Client Type: Public.\n" +
                      "3. Copy its Client ID into the box below, then press Sign in."
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing
                QQC2.Label { text: "Client ID" }
                QQC2.TextField { id: cidField; Layout.fillWidth: true; text: twitch.clientId; placeholderText: "Paste your Twitch Client ID"; onEditingFinished: twitch.setClientId(text) }
                QQC2.Button {
                    visible: pg.setup && !twitch.loggingIn
                    text: "Sign in with Twitch"; icon.name: "im-user"; highlighted: true
                    enabled: cidField.text.trim() !== ""
                    onClicked: { twitch.setClientId(cidField.text); twitch.signIn() }
                }
                QQC2.Button { visible: twitch.signedIn; text: "Sign out"; icon.name: "system-log-out"; onClicked: twitch.signOut() }
            }
            // the code to type at twitch.com/activate
            Rectangle {
                Layout.fillWidth: true
                visible: twitch.loggingIn
                implicitHeight: codeCol.implicitHeight + Kirigami.Units.largeSpacing * 2
                radius: 8
                color: Kirigami.Theme.alternateBackgroundColor
                ColumnLayout {
                    id: codeCol
                    anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: Kirigami.Units.largeSpacing }
                    spacing: Kirigami.Units.smallSpacing
                    QQC2.Label { text: "Type this code at " + twitch.loginUrl + " and press Authorize:"; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    QQC2.Label { text: twitch.loginCode; font.bold: true; font.pointSize: Kirigami.Theme.defaultFont.pointSize * 2.2; font.letterSpacing: 4; Layout.alignment: Qt.AlignHCenter }
                    RowLayout {
                        Layout.alignment: Qt.AlignHCenter
                        QQC2.Button { text: "Open " + twitch.loginUrl; icon.name: "internet-web-browser"; onClicked: Qt.openUrlExternally(twitch.loginUrl) }
                        QQC2.Button { text: "Cancel"; onClicked: twitch.cancelSignIn() }
                    }
                    RowLayout {
                        Layout.alignment: Qt.AlignHCenter
                        QQC2.BusyIndicator { running: true; implicitWidth: Kirigami.Units.iconSizes.smallMedium; implicitHeight: implicitWidth }
                        QQC2.Label { text: "Waiting for you to authorise…"; opacity: 0.7 }
                    }
                }
            }

            Kirigami.Separator { Layout.fillWidth: true; visible: !pg.setup }
            QQC2.Switch {
                visible: !pg.setup
                text: "Ad-free mode (off: normal Twitch, with ads)"
                checked: twitch.adFree
                onToggled: twitch.setAdFree(checked)
            }
            QQC2.Label {
                visible: !pg.setup
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                opacity: 0.7
                font: Kirigami.Theme.smallFont
                text: "Twitch puts ads into the video itself. When this is on, Marquee asks for the stream through the proxy below. " +
                      "That depends on someone else’s server, is against Twitch’s terms, and can stop working at any time; when it does, " +
                      "Marquee plays the normal stream instead and says so. Marquee doesn’t supply a proxy: put the address of one in a place where Twitch doesn’t show ads."
            }
            RowLayout {
                visible: !pg.setup
                Layout.fillWidth: true
                QQC2.Label { text: "Proxy" }
                QQC2.TextField { Layout.fillWidth: true; text: twitch.proxy; placeholderText: "http://address:port"; enabled: twitch.adFree; onEditingFinished: twitch.setProxy(text) }
            }
            QQC2.CheckBox {
                visible: !pg.setup
                text: "Use my browser’s Twitch login (for Turbo or a channel subscription)"
                checked: twitch.browserLogin
                enabled: twitch.hasBrowser
                onToggled: twitch.setBrowserLogin(checked)
            }
            QQC2.Label {
                visible: !pg.setup && !twitch.hasBrowser
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                opacity: 0.7
                font: Kirigami.Theme.smallFont
                text: "Choose your browser in the YouTube settings first; this uses the same one."
            }
            RowLayout {
                visible: !pg.setup
                QQC2.Label { text: "Picture size" }
                QQC2.ComboBox {
                    model: ["Best", "1080p", "720p", "480p", "360p"]
                    readonly property var values: ["best", "1080", "720", "480", "360"]
                    currentIndex: Math.max(0, values.indexOf(twitch.quality))
                    onActivated: (i) => twitch.setQuality(values[i])
                }
            }
        }

        // ---- the channels ----
        Flickable {
            id: scroller
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: twitch.signedIn
            clip: true
            contentWidth: width
            contentHeight: body.implicitHeight + Kirigami.Units.largeSpacing * 2
            QQC2.ScrollBar.vertical: QQC2.ScrollBar {}

            // what the search box leaves in
            readonly property string needle: filterField.text.trim().toLowerCase()
            ColumnLayout {
                id: body
                readonly property var liveShown: twitch.live.filter(function (c) { return scroller.needle === "" || c.name.toLowerCase().indexOf(scroller.needle) >= 0 || c.login.indexOf(scroller.needle) >= 0 || (c.game || "").toLowerCase().indexOf(scroller.needle) >= 0 })
                readonly property var offlineShown: twitch.offline.filter(function (c) { return scroller.needle === "" || c.name.toLowerCase().indexOf(scroller.needle) >= 0 || c.login.indexOf(scroller.needle) >= 0 })
                x: Kirigami.Units.smallSpacing
                y: Kirigami.Units.smallSpacing
                width: scroller.width - Kirigami.Units.smallSpacing * 2
                spacing: Kirigami.Units.largeSpacing

                QQC2.TextField {
                    id: filterField
                    Layout.fillWidth: true
                    placeholderText: "Search the channels you follow (" + (twitch.live.length + twitch.offline.length) + ")"
                    visible: twitch.live.length + twitch.offline.length > 8
                }
                RowLayout {
                    spacing: Kirigami.Units.smallSpacing
                    QQC2.Label { text: "Live now"; font.bold: true; font.pointSize: Kirigami.Theme.defaultFont.pointSize * 1.2 }
                    QQC2.Label { text: body.liveShown.length > 0 ? "(" + body.liveShown.length + ")" : ""; opacity: 0.6 }
                    QQC2.BusyIndicator { running: twitch.busy; visible: running; implicitWidth: Kirigami.Units.iconSizes.smallMedium; implicitHeight: implicitWidth }
                }
                QQC2.Label {
                    visible: body.liveShown.length === 0 && !twitch.busy
                    opacity: 0.7
                    text: filterField.text !== "" ? "No live channel matches." : "None of the channels you follow is live right now."
                }
                // tiles
                Flow {
                    Layout.fillWidth: true
                    visible: !twitch.listView
                    spacing: Kirigami.Units.largeSpacing
                    Repeater {
                        model: body.liveShown
                        delegate: Item {
                            id: card
                            required property var modelData
                            readonly property real cardW: Math.max(Kirigami.Units.gridUnit * 12, Math.min(Kirigami.Units.gridUnit * 16, (body.width - Kirigami.Units.largeSpacing * 3) / 3))
                            width: cardW
                            height: cardW * 9 / 16 + Kirigami.Units.gridUnit * 3.6
                            ColumnLayout {
                                anchors.fill: parent
                                spacing: 3
                                Pic {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: card.cardW * 9 / 16
                                    source: modelData.thumb
                                    Rectangle {
                                        anchors { left: parent.left; bottom: parent.bottom; margins: 6 }
                                        height: Kirigami.Units.gridUnit * 1.2
                                        width: vlabel.implicitWidth + 12
                                        radius: 4
                                        color: "#cc000000"
                                        QQC2.Label { id: vlabel; anchors.centerIn: parent; text: "● " + pg.compact(modelData.viewers); color: "white"; font: Kirigami.Theme.smallFont }
                                    }
                                }
                                QQC2.Label { text: modelData.name; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                                QQC2.Label { text: modelData.title; elide: Text.ElideRight; Layout.fillWidth: true; maximumLineCount: 1; opacity: 0.85 }
                                QQC2.Label { text: modelData.game; elide: Text.ElideRight; Layout.fillWidth: true; maximumLineCount: 1; opacity: 0.6; font: Kirigami.Theme.smallFont }
                            }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: pg.watch(modelData.login) }
                        }
                    }
                }
                // list
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: twitch.listView
                    spacing: 2
                    Repeater {
                        model: body.liveShown
                        delegate: QQC2.ItemDelegate {
                            required property var modelData
                            Layout.fillWidth: true
                            onClicked: pg.watch(modelData.login)
                            contentItem: RowLayout {
                                spacing: Kirigami.Units.largeSpacing
                                Pic { source: modelData.thumb; Layout.preferredWidth: Kirigami.Units.gridUnit * 7; Layout.preferredHeight: Kirigami.Units.gridUnit * 7 * 9 / 16 }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 0
                                    QQC2.Label { text: modelData.name; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                                    QQC2.Label { text: modelData.title; elide: Text.ElideRight; Layout.fillWidth: true; maximumLineCount: 1; opacity: 0.85 }
                                    QQC2.Label { text: modelData.game; elide: Text.ElideRight; Layout.fillWidth: true; maximumLineCount: 1; opacity: 0.6; font: Kirigami.Theme.smallFont }
                                }
                                QQC2.Label { text: "● " + pg.compact(modelData.viewers) + " watching"; color: Kirigami.Theme.negativeTextColor }
                            }
                        }
                    }
                }

                // everyone else you follow
                RowLayout {
                    visible: body.offlineShown.length > 0
                    spacing: Kirigami.Units.smallSpacing
                    QQC2.Label { text: "Offline"; font.bold: true; font.pointSize: Kirigami.Theme.defaultFont.pointSize * 1.2 }
                    QQC2.Label { text: "(" + body.offlineShown.length + ")"; opacity: 0.6 }
                }
                Flow {
                    Layout.fillWidth: true
                    visible: !twitch.listView
                    spacing: Kirigami.Units.largeSpacing
                    Repeater {
                        model: body.offlineShown
                        delegate: Item {
                            required property var modelData
                            width: Kirigami.Units.gridUnit * 5
                            height: Kirigami.Units.gridUnit * 5.2
                            ColumnLayout {
                                anchors.fill: parent
                                spacing: 3
                                Pic { round: true; source: modelData.image; Layout.alignment: Qt.AlignHCenter; Layout.preferredWidth: Kirigami.Units.gridUnit * 3; Layout.preferredHeight: Kirigami.Units.gridUnit * 3; opacity: 0.75 }
                                QQC2.Label { text: modelData.name; elide: Text.ElideRight; Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; opacity: 0.8; font: Kirigami.Theme.smallFont }
                            }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: pg.watch(modelData.login) }
                        }
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: twitch.listView
                    spacing: 0
                    Repeater {
                        model: body.offlineShown
                        delegate: QQC2.ItemDelegate {
                            required property var modelData
                            Layout.fillWidth: true
                            onClicked: pg.watch(modelData.login)
                            contentItem: RowLayout {
                                spacing: Kirigami.Units.largeSpacing
                                Pic { round: true; source: modelData.image; Layout.preferredWidth: Kirigami.Units.gridUnit * 2; Layout.preferredHeight: Kirigami.Units.gridUnit * 2; opacity: 0.8 }
                                QQC2.Label { text: modelData.name; Layout.fillWidth: true; elide: Text.ElideRight; opacity: 0.85 }
                                QQC2.Label { text: "Offline"; opacity: 0.5; font: Kirigami.Theme.smallFont }
                            }
                        }
                    }
                }
            }
        }

        Item { visible: !twitch.signedIn; Layout.fillHeight: true }
    }
}
