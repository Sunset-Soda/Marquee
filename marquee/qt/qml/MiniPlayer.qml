import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import QtMultimedia
import org.kde.kirigami as Kirigami

// The one control bar for music, along the bottom of the window in the library and on Now Playing alike: cover and
// title on the left, the controls and seek bar in the middle, volume and extras on the right.
Rectangle {
    id: bar
    required property var music
    property bool nowPlayingOpen: false
    signal openRequested()

    implicitHeight: Kirigami.Units.gridUnit * 4
    height: visible ? implicitHeight : 0
    color: music.pal.base
    Behavior on color { ColorAnimation { duration: 600 } }

    readonly property bool wide: width > Kirigami.Units.gridUnit * 46
    readonly property bool medium: width > Kirigami.Units.gridUnit * 30
    readonly property var player: music.player
    readonly property color dim: "#b3ffffff"

    function fmt(ms) {
        var s = Math.max(0, Math.floor(ms / 1000)), m = Math.floor(s / 60)
        return m + ":" + String(s % 60).padStart(2, "0")
    }

    // A thin accent line along the top, in the cover's colour.
    Rectangle {
        anchors { left: parent.left; right: parent.right; top: parent.top }
        height: 2
        color: bar.music.pal.bar
        opacity: 0.8
    }

    // ---- left: the song ------------------------------------------------
    Item {
        id: leftSection
        anchors { left: parent.left; top: parent.top; bottom: parent.bottom; leftMargin: Kirigami.Units.largeSpacing }
        width: bar.wide ? Math.min(bar.width * 0.28, Kirigami.Units.gridUnit * 20)
                        : (bar.medium ? Kirigami.Units.gridUnit * 12 : Kirigami.Units.gridUnit * 3.4)
        Cover {
            id: art
            anchors.verticalCenter: parent.verticalCenter
            boxSize: Kirigami.Units.gridUnit * 3
            ratio: 1
            source: bar.music.info.cover || ""
            title: bar.music.info.title || ""
            iconName: bar.music.info.icon || "audio-x-generic"
        }
        ColumnLayout {
            visible: bar.medium
            anchors { left: art.right; leftMargin: Kirigami.Units.largeSpacing; right: parent.right; verticalCenter: parent.verticalCenter }
            spacing: 0
            QQC2.Label { Layout.fillWidth: true; text: bar.music.info.title || ""; color: "white"; font.weight: Font.DemiBold; elide: Text.ElideRight }
            QQC2.Label { Layout.fillWidth: true; text: bar.music.loading ? "Loading…" : (bar.music.errorText !== "" ? "Couldn’t play this song" : (bar.music.info.sub || "")); color: bar.dim; font: Kirigami.Theme.smallFont; elide: Text.ElideRight }
        }
        TapHandler { onTapped: bar.openRequested() }
        HoverHandler { cursorShape: Qt.PointingHandCursor }
    }

    // ---- right: volume and extras --------------------------------------
    RowLayout {
        id: rightSection
        anchors { right: parent.right; rightMargin: Kirigami.Units.largeSpacing; verticalCenter: parent.verticalCenter }
        spacing: Kirigami.Units.smallSpacing
        QQC2.ToolButton {
            visible: bar.nowPlayingOpen
            icon.name: "view-media-lyrics"; icon.color: checked ? bar.music.pal.bar : "white"
            display: QQC2.AbstractButton.IconOnly; text: "Lyrics"; checkable: true; checked: bar.music.lyricsOn
            onClicked: bar.music.lyricsOn = !bar.music.lyricsOn
            QQC2.ToolTip.text: "Lyrics (L)"; QQC2.ToolTip.visible: hovered
        }
        QQC2.ToolButton {
            visible: bar.wide
            icon.name: bar.music.muted || bar.music.volume === 0 ? "player-volume-muted" : "player-volume"; icon.color: "white"
            display: QQC2.AbstractButton.IconOnly; text: "Mute"
            onClicked: bar.music.muted = !bar.music.muted
        }
        QQC2.Slider {
            visible: bar.wide
            Layout.preferredWidth: Kirigami.Units.gridUnit * 6
            from: 0; to: 1; value: bar.music.volume
            onMoved: bar.music.volume = value
        }
        QQC2.ToolButton {
            icon.name: bar.nowPlayingOpen ? "go-down" : "go-up"; icon.color: "white"
            display: QQC2.AbstractButton.IconOnly; text: bar.nowPlayingOpen ? "Back to Library" : "Open Now Playing"
            onClicked: bar.openRequested()
            QQC2.ToolTip.text: text; QQC2.ToolTip.visible: hovered
        }
        QQC2.ToolButton {
            icon.name: "window-close"; icon.color: bar.dim; display: QQC2.AbstractButton.IconOnly; text: "Stop"
            onClicked: bar.music.stop()
            QQC2.ToolTip.text: "Stop and close"; QQC2.ToolTip.visible: hovered
        }
    }

    // ---- middle: controls and seek --------------------------------------
    ColumnLayout {
        anchors {
            left: leftSection.right; right: rightSection.left; verticalCenter: parent.verticalCenter
            leftMargin: Kirigami.Units.largeSpacing; rightMargin: Kirigami.Units.largeSpacing
        }
        spacing: 0
        RowLayout {
            Layout.alignment: Qt.AlignHCenter
            spacing: Kirigami.Units.smallSpacing
            QQC2.ToolButton {
                visible: bar.medium
                icon.name: "media-playlist-shuffle"; icon.color: checked ? bar.music.pal.bar : bar.dim
                display: QQC2.AbstractButton.IconOnly; text: "Shuffle"; checkable: true; checked: marquee.shuffle
                onClicked: marquee.toggleShuffle()
                QQC2.ToolTip.text: "Shuffle " + (marquee.shuffle ? "on" : "off"); QQC2.ToolTip.visible: hovered
            }
            QQC2.ToolButton { icon.name: "media-skip-backward"; icon.color: "white"; display: QQC2.AbstractButton.IconOnly; text: "Previous"; onClicked: bar.music.previous() }
            QQC2.ToolButton {
                icon.name: bar.music.playing ? "media-playback-pause" : "media-playback-start"; icon.color: "white"
                display: QQC2.AbstractButton.IconOnly; text: bar.music.playing ? "Pause" : "Play"
                onClicked: bar.music.toggle()
            }
            QQC2.ToolButton { icon.name: "media-skip-forward"; icon.color: "white"; display: QQC2.AbstractButton.IconOnly; text: "Next"; onClicked: bar.music.next(false) }
            QQC2.ToolButton {
                visible: bar.medium
                icon.name: marquee.repeatMode === "one" ? "media-playlist-repeat-song" : "media-playlist-repeat"
                icon.color: marquee.repeatMode !== "off" ? bar.music.pal.bar : bar.dim
                display: QQC2.AbstractButton.IconOnly; text: "Repeat"; checkable: true; checked: marquee.repeatMode !== "off"
                onClicked: marquee.cycleRepeat()
                QQC2.ToolTip.text: marquee.repeatMode === "one" ? "Repeat this song" : (marquee.repeatMode === "all" ? "Repeat all" : "Repeat off")
                QQC2.ToolTip.visible: hovered
            }
        }
        RowLayout {
            Layout.fillWidth: true
            visible: bar.medium
            spacing: Kirigami.Units.smallSpacing
            QQC2.Label { text: bar.fmt(bar.player.position); color: bar.dim; font: Kirigami.Theme.smallFont }
            QQC2.Slider {
                id: seek
                Layout.fillWidth: true
                from: 0
                to: Math.max(1, bar.player.duration)
                enabled: bar.player.seekable
                onMoved: bar.player.position = value
                Binding { target: seek; property: "value"; value: bar.player.position; when: !seek.pressed }
            }
            QQC2.Label { text: bar.fmt(bar.player.duration); color: bar.dim; font: Kirigami.Theme.smallFont }
        }
    }
}
