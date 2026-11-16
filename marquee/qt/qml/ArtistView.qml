import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

// An artist's page, Spotify style: every album one after another, each with its cover, title and a numbered list of
// its songs. Click a song to play it; the rest of the artist's songs follow in the order shown.
ListView {
    id: av
    property var music                         // the window's music player, to mark the song that is playing
    signal menuRequested(var item, var row)

    clip: true
    model: marquee.artistAlbums
    spacing: Kirigami.Units.gridUnit * 2.5
    bottomMargin: Kirigami.Units.gridUnit * 2
    boundsBehavior: Flickable.StopAtBounds
    // Quicker scrolling that keeps its smooth glide: each wheel notch throws the list a good way, and it stops sooner.
    // (A touchpad keeps its own natural scrolling.)
    maximumFlickVelocity: 9000
    flickDeceleration: 5000
    WheelHandler {
        acceptedDevices: PointerDevice.Mouse
        onWheel: event => av.flick(0, event.angleDelta.y * 14)
    }
    QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
    readonly property real gutter: Kirigami.Units.gridUnit * 2
    readonly property color ink: Kirigami.Theme.textColor
    function inkA(a) { return Qt.rgba(ink.r, ink.g, ink.b, a) }

    function clock(sec) {
        var s = Math.round(sec), m = Math.floor(s / 60)
        return m + ":" + String(s % 60).padStart(2, "0")
    }
    function span(sec) {
        var m = Math.round(sec / 60)
        if (m < 1) return ""
        return m >= 60 ? Math.floor(m / 60) + " hr " + (m % 60) + " min" : m + " min"
    }
    function songCount() {
        var n = 0
        for (var i = 0; i < model.length; i++) n += model[i].count
        return n
    }

    // artist name and the two big buttons
    header: ColumnLayout {
        width: av.width
        spacing: Kirigami.Units.largeSpacing
        Item { Layout.preferredHeight: Kirigami.Units.largeSpacing }
        Kirigami.Heading {
            Layout.leftMargin: av.gutter; Layout.rightMargin: av.gutter; Layout.fillWidth: true
            level: 1
            font.pixelSize: Kirigami.Units.gridUnit * 2.4
            font.weight: Font.Bold
            text: marquee.heading
            elide: Text.ElideRight
        }
        QQC2.Label {
            Layout.leftMargin: av.gutter
            opacity: 0.7
            text: av.model.length + (av.model.length === 1 ? " album" : " albums") + " · " + av.songCount() + " songs"
        }
        Item { Layout.preferredHeight: Kirigami.Units.largeSpacing }
    }

    // one album
    delegate: Item {
        id: album
        required property var modelData
        width: ListView.view.width
        height: col.implicitHeight

        ColumnLayout {
            id: col
            anchors { left: parent.left; right: parent.right; leftMargin: av.gutter; rightMargin: av.gutter }
            spacing: Kirigami.Units.largeSpacing

            RowLayout {
                spacing: Kirigami.Units.gridUnit * 1.5
                Cover {
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 9
                    Layout.preferredHeight: Kirigami.Units.gridUnit * 9
                    boxSize: Kirigami.Units.gridUnit * 9
                    ratio: 1
                    source: album.modelData.cover
                    title: album.modelData.title
                    iconName: "audio-x-generic"
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing
                    Kirigami.Heading {
                        Layout.fillWidth: true
                        level: 1
                        font.weight: Font.Bold
                        text: album.modelData.title
                        wrapMode: Text.Wrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                    }
                    QQC2.Label {
                        opacity: 0.7
                        text: "Album · " + album.modelData.count + (album.modelData.count === 1 ? " song" : " songs") +
                              (av.span(album.modelData.dur) ? " · " + av.span(album.modelData.dur) : "")
                    }
                    RowLayout {
                        Layout.topMargin: Kirigami.Units.smallSpacing
                        spacing: Kirigami.Units.largeSpacing
                        QQC2.RoundButton {
                            highlighted: true
                            icon.name: "media-playback-start"
                            display: QQC2.AbstractButton.IconOnly
                            text: "Play Album"
                            onClicked: applicationWindow().play(album.modelData.songs[0].id)
                            QQC2.ToolTip.text: text
                            QQC2.ToolTip.visible: hovered
                        }
                        QQC2.RoundButton {
                            icon.name: "media-playlist-shuffle"
                            display: QQC2.AbstractButton.IconOnly
                            text: "Shuffle Album"
                            onClicked: { var id = marquee.shuffleAlbum(album.modelData.albumId); if (id) applicationWindow().play(id) }
                            QQC2.ToolTip.text: text
                            QQC2.ToolTip.visible: hovered
                        }
                    }
                }
            }

            // column headings
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: Kirigami.Units.gridUnit * 2.2
                radius: height / 2
                color: "transparent"
                border.width: 1
                border.color: av.inkA(0.25)
                RowLayout {
                    anchors { fill: parent; leftMargin: Kirigami.Units.gridUnit; rightMargin: Kirigami.Units.gridUnit * 1.4 }
                    spacing: Kirigami.Units.largeSpacing
                    QQC2.Label { text: "#"; opacity: 0.7; Layout.preferredWidth: Kirigami.Units.gridUnit * 2; horizontalAlignment: Text.AlignHCenter }
                    QQC2.Label { text: "Title"; opacity: 0.7; Layout.fillWidth: true }
                    Kirigami.Icon { source: "chronometer"; implicitWidth: Kirigami.Units.iconSizes.small; implicitHeight: implicitWidth; opacity: 0.7 }
                }
            }

            // songs
            Repeater {
                model: album.modelData.songs
                delegate: Rectangle {
                    id: row
                    required property var modelData
                    readonly property bool playing: !!av.music && av.music.active && av.music.itemId === modelData.id
                    Layout.fillWidth: true
                    implicitHeight: Kirigami.Units.gridUnit * 3.2
                    radius: Kirigami.Units.gridUnit
                    color: playing ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.18)
                                   : (hover.hovered ? av.inkA(0.08) : "transparent")
                    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: applicationWindow().play(row.modelData.id) }
                    TapHandler { acceptedButtons: Qt.RightButton; onTapped: av.menuRequested(row, row.modelData) }
                    RowLayout {
                        anchors { fill: parent; leftMargin: Kirigami.Units.gridUnit; rightMargin: Kirigami.Units.gridUnit * 1.4 }
                        spacing: Kirigami.Units.largeSpacing
                        Item {
                            Layout.preferredWidth: Kirigami.Units.gridUnit * 2
                            Layout.preferredHeight: Kirigami.Units.gridUnit * 2
                            QQC2.Label { anchors.centerIn: parent; visible: !hover.hovered && !row.playing; text: row.modelData.track; opacity: 0.7 }
                            Kirigami.Icon {
                                anchors.centerIn: parent
                                width: Kirigami.Units.iconSizes.small; height: width
                                visible: hover.hovered || row.playing
                                source: row.playing && !hover.hovered ? "audio-volume-high" : "media-playback-start"
                                color: row.playing ? Kirigami.Theme.highlightColor : av.ink
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 0
                            QQC2.Label {
                                Layout.fillWidth: true
                                text: row.modelData.title
                                elide: Text.ElideRight
                                font.weight: row.playing ? Font.Bold : Font.Normal
                                color: row.playing ? Kirigami.Theme.highlightColor : av.ink
                            }
                            QQC2.Label { Layout.fillWidth: true; text: row.modelData.sub; opacity: 0.65; elide: Text.ElideRight; font: Kirigami.Theme.smallFont }
                        }
                        QQC2.Label { text: row.modelData.dur > 0 ? av.clock(row.modelData.dur) : ""; opacity: 0.7 }
                    }
                }
            }
        }
    }
}
