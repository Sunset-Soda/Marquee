import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import QtMultimedia
import org.kde.kirigami as Kirigami

// YouTube Shorts: one tall video at a time. Up and Down (or the mouse wheel, or the arrows) move between them.
// The next one is looked up while this one plays, so moving on is quick.
Kirigami.Page {
    id: sp
    title: "Shorts"
    padding: 0

    property var music                              // the window's music player; it stops when Shorts starts
    property var pod                                // the window's podcast player; it pauses too
    property int index: -1
    property string phase: "idle"                   // idle, loading, playing, error
    property string errorText: ""
    property string titleText: ""
    property string channelText: ""
    property bool muted: false
    property bool autoNext: marquee.shortsAutoScroll()      // a remembered choice: move on by itself when one ends
    property bool paused: false
    readonly property var items: shorts.items
    readonly property var cur: index >= 0 && index < items.length ? items[index] : null

    function show(i) {
        if (i < 0 || i >= items.length) return
        index = i
        phase = "loading"; errorText = ""; paused = false
        player.stop()
        titleText = items[i].title || ""
        channelText = items[i].channel || ""
        if (i >= items.length - 3 && shorts.label.indexOf("For you") === 0) shorts.more()     // keep it going: add new ones near the end
        youtube.dropShortQueue()                       // what was lined up for the old place is no longer wanted
        youtube.playShort(items[i].id, items[i])       // lookAhead() runs once this one is playing, so it gets the bandwidth first
    }
    // get the next few ready while this one plays (two are fetched at a time)
    function lookAhead() {
        for (var k = 1; k <= 3; k++)
            if (index + k < items.length) youtube.prefetchShort(items[index + k].id, items[index + k])
    }
    function next() { if (index + 1 < items.length) show(index + 1) }
    function previous() { if (index > 0) show(index - 1) }
    function begin(id) {
        var info = marquee.itemInfo("yt:" + id)
        if (!info || !info.url) { phase = "error"; errorText = "That Short has no stream."; return }
        if (info.title) titleText = info.title
        if (info.sub) channelText = info.sub
        player.source = info.url
        player.play()
        phase = "playing"
        lookAhead()
    }
    function togglePause() {
        if (phase !== "playing") return
        if (player.playbackState === MediaPlayer.PlayingState) { player.pause(); paused = true }
        else { player.play(); paused = false }
    }

    Component.onCompleted: {
        if (music) music.stop()
        if (pod) pod.pause()
        if (items.length > 0) show(Math.max(0, index))
        else shorts.forYou()
    }
    Component.onDestruction: player.stop()

    Connections {
        target: shorts
        function onLoaded() { if (sp.items.length > 0) sp.show(0) }
    }
    Connections {
        target: youtube
        function onShortReady(id) { if (sp.cur && id === sp.cur.id && sp.phase === "loading") sp.begin(id) }
        function onShortFailed(id, message) {
            if (sp.cur && id === sp.cur.id) { sp.phase = "error"; sp.errorText = message }
        }
    }

    MediaPlayer {
        id: player
        audioOutput: AudioOutput { muted: sp.muted }
        videoOutput: video
        onMediaStatusChanged: {
            if (mediaStatus !== MediaPlayer.EndOfMedia) return
            if (sp.autoNext && sp.index + 1 < sp.items.length) sp.next()
            else { position = 0; play() }                  // a Short loops until you move on
        }
        onErrorOccurred: (error, message) => { sp.phase = "error"; sp.errorText = message || "This Short could not be played." }
    }

    Shortcut { sequences: ["Down", "PgDown", "J"]; enabled: sp.visible && !searchField.activeFocus; onActivated: sp.next() }
    Shortcut { sequences: ["Up", "PgUp", "K"]; enabled: sp.visible && !searchField.activeFocus; onActivated: sp.previous() }
    Shortcut { sequences: ["Space"]; enabled: sp.visible && !searchField.activeFocus; onActivated: sp.togglePause() }
    Shortcut { sequences: ["M"]; enabled: sp.visible && !searchField.activeFocus; onActivated: sp.muted = !sp.muted }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.smallSpacing
            spacing: Kirigami.Units.smallSpacing
            QQC2.TextField {
                id: searchField
                Layout.fillWidth: true
                placeholderText: "Search Shorts, or paste a Shorts or channel link"
                onAccepted: { shorts.search(text); focus = false }
            }
            QQC2.Button { text: "Search"; icon.name: "edit-find"; onClicked: { shorts.search(searchField.text); searchField.focus = false } }
            QQC2.Button {
                text: "Auto-scroll"; icon.name: "go-down"; checkable: true; checked: sp.autoNext; highlighted: checked
                onToggled: { sp.autoNext = checked; marquee.setShortsAutoScroll(checked) }
                QQC2.ToolTip.text: sp.autoNext ? "On: the next Short starts as soon as one ends" : "Off: each Short loops until you move on"
                QQC2.ToolTip.visible: hovered
            }
            QQC2.Button { text: "For you"; icon.name: "view-refresh"; onClicked: shorts.forYou() }
        }

        Item {
            id: stage
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            Rectangle { anchors.fill: parent; color: "#101014" }

            // the tall frame: 9 by 16, as large as fits
            Item {
                id: frame
                height: stage.height
                width: Math.min(stage.width - Kirigami.Units.gridUnit * 5, height * 9 / 16)
                anchors.centerIn: parent
                visible: sp.cur !== null

                VideoOutput {
                    id: video
                    anchors.fill: parent
                    fillMode: VideoOutput.PreserveAspectFit
                }
                MouseArea {
                    anchors.fill: parent
                    onClicked: sp.togglePause()
                    onWheel: (wheel) => {
                        if (wheelGate.running) return
                        wheelGate.start()
                        if (wheel.angleDelta.y < 0) sp.next(); else if (wheel.angleDelta.y > 0) sp.previous()
                    }
                }
                Timer { id: wheelGate; interval: 450 }

                // title and channel over a soft shade
                Rectangle {
                    anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
                    height: info.implicitHeight + Kirigami.Units.gridUnit * 2
                    gradient: Gradient {
                        GradientStop { position: 0; color: "transparent" }
                        GradientStop { position: 1; color: "#cc000000" }
                    }
                    ColumnLayout {
                        id: info
                        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: Kirigami.Units.largeSpacing }
                        anchors.bottomMargin: Kirigami.Units.largeSpacing + 4
                        spacing: 2
                        QQC2.Label { text: sp.channelText; color: "white"; font.bold: true; Layout.fillWidth: true; elide: Text.ElideRight }
                        QQC2.Label { text: sp.titleText; color: "white"; wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight; Layout.fillWidth: true }
                    }
                }
                // progress, which you can click to seek
                Rectangle {
                    anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
                    height: 4
                    color: "#55ffffff"
                    Rectangle { width: player.duration > 0 ? parent.width * player.position / player.duration : 0; height: parent.height; color: "white" }
                    MouseArea {
                        anchors { fill: parent; topMargin: -8 }
                        onClicked: (mouse) => { if (player.duration > 0) player.position = mouse.x / width * player.duration }
                    }
                }
                Kirigami.Icon {
                    anchors.centerIn: parent
                    source: "media-playback-start"
                    color: "white"
                    width: Kirigami.Units.iconSizes.huge; height: width
                    visible: sp.paused && sp.phase === "playing"
                }
                QQC2.BusyIndicator { id: spin; anchors.centerIn: parent; running: sp.phase === "loading"; visible: running }
                QQC2.Label {
                    anchors { horizontalCenter: parent.horizontalCenter; top: spin.bottom; topMargin: Kirigami.Units.smallSpacing }
                    visible: sp.phase === "loading" && youtube.note !== ""
                    text: youtube.note; color: "white"; opacity: 0.7
                }
                QQC2.Label {
                    anchors { centerIn: parent }
                    width: parent.width - Kirigami.Units.gridUnit * 2
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    color: "white"
                    visible: sp.phase === "error"
                    text: sp.errorText + "\nPress Down to try the next one."
                }
            }

            // controls beside the frame
            ColumnLayout {
                anchors { left: frame.right; leftMargin: Kirigami.Units.largeSpacing; verticalCenter: parent.verticalCenter }
                visible: sp.cur !== null
                spacing: Kirigami.Units.smallSpacing
                QQC2.RoundButton { icon.name: "go-up"; enabled: sp.index > 0; onClicked: sp.previous(); QQC2.ToolTip.text: "Previous (Up)"; QQC2.ToolTip.visible: hovered }
                QQC2.RoundButton { icon.name: "go-down"; enabled: sp.index + 1 < sp.items.length; onClicked: sp.next(); QQC2.ToolTip.text: "Next (Down)"; QQC2.ToolTip.visible: hovered }
                QQC2.RoundButton {
                    icon.name: sp.muted ? "audio-volume-muted" : "audio-volume-high"
                    onClicked: sp.muted = !sp.muted
                    QQC2.ToolTip.text: sp.muted ? "Unmute (M)" : "Mute (M)"; QQC2.ToolTip.visible: hovered
                }
                QQC2.RoundButton {
                    icon.name: "view-list-details"
                    enabled: !!(sp.cur && sp.cur.channelId)
                    onClicked: shorts.channel(sp.cur.channelId, sp.channelText)
                    QQC2.ToolTip.text: "More Shorts from this channel"; QQC2.ToolTip.visible: hovered
                }
                QQC2.RoundButton {
                    icon.name: "tag"
                    enabled: !!(sp.cur && sp.titleText)
                    onClicked: shorts.similar(sp.titleText)
                    QQC2.ToolTip.text: "More like this"; QQC2.ToolTip.visible: hovered
                }
                QQC2.Label {
                    text: (sp.index + 1) + " / " + sp.items.length
                    opacity: 0.6; color: "white"; Layout.alignment: Qt.AlignHCenter
                }
            }

            ColumnLayout {
                anchors.centerIn: parent
                width: Math.min(parent.width - Kirigami.Units.gridUnit * 4, Kirigami.Units.gridUnit * 24)
                visible: sp.cur === null
                spacing: Kirigami.Units.largeSpacing
                QQC2.BusyIndicator { running: shorts.busy; visible: running; Layout.alignment: Qt.AlignHCenter }
                QQC2.Label {
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    color: "white"
                    text: shorts.busy ? "Finding Shorts…"
                        : shorts.error !== "" ? shorts.error
                        : "Search for Shorts, paste a link, or press For you."
                }
            }
            QQC2.Label {
                anchors { left: parent.left; top: parent.top; margins: Kirigami.Units.largeSpacing }
                text: shorts.label; color: "white"; opacity: 0.6; visible: shorts.label !== ""
            }
        }
    }
}
