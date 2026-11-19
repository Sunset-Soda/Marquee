import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import QtMultimedia
import org.kde.kirigami as Kirigami

// Podcasts: search for a show or paste its RSS feed, subscribe, and play episodes. The player itself lives in the window
// (PodcastEngine.qml), so an episode carries on while you browse elsewhere.
Kirigami.Page {
    id: pg
    title: "Podcasts"
    padding: 0

    property var music                              // the window's music player; it stops when an episode starts
    property var pod                                // the window's podcast player
    property var win                                // the main window, to open the library
    readonly property bool showing: !!(pod && pod.active)

    // podcast files added to the library from this computer
    readonly property int localFiles: {
        var c = marquee.collections
        for (var i = 0; i < c.length; i++) if (c[i].id === "podcast") return c[i].count
        return 0
    }
    function showLocalFiles() {
        if (!win) return
        win.showLibrary()
        marquee.setCollection("podcast")
    }
    function playEpisode(key) {
        if (music) music.stop()
        pod.play(key)
    }
    function fmt(ms) {
        var s = Math.max(0, Math.floor(ms / 1000))
        var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60
        return (h > 0 ? h + ":" + (m < 10 ? "0" : "") : "") + m + ":" + (r < 10 ? "0" : "") + r
    }

    Component.onCompleted: podcasts.refreshAll()

    // a cover that falls back to a plain tile while it loads or when there is none
    component Art: Item {
        id: art
        property string source: ""
        Rectangle { anchors.fill: parent; radius: 6; color: Kirigami.Theme.alternateBackgroundColor }
        Kirigami.Icon { anchors.centerIn: parent; width: parent.width * 0.4; height: width; source: Qt.resolvedUrl("podcasts.svg"); isMask: true; color: Kirigami.Theme.disabledTextColor; visible: img.status !== Image.Ready }
        Image {
            id: img
            anchors.fill: parent
            source: art.source
            asynchronous: true
            fillMode: Image.PreserveAspectCrop
            sourceSize.width: 400
            sourceSize.height: 400
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.smallSpacing
            spacing: Kirigami.Units.smallSpacing
            QQC2.ToolButton {
                icon.name: "go-previous"
                visible: podcasts.view !== "subs"
                onClicked: podcasts.back()
                QQC2.ToolTip.text: "Back"; QQC2.ToolTip.visible: hovered
            }
            QQC2.TextField {
                id: searchField
                Layout.fillWidth: true
                placeholderText: "Search podcasts, or paste an RSS feed or Apple Podcasts link"
                onAccepted: { podcasts.search(text); focus = false }
            }
            QQC2.Button { text: "Search"; icon.name: "edit-find"; onClicked: { podcasts.search(searchField.text); searchField.focus = false } }
            QQC2.Button {
                text: "Files on this computer (" + pg.localFiles + ")"; icon.name: "folder-sound"
                visible: pg.localFiles > 0 && podcasts.view === "subs"
                onClicked: pg.showLocalFiles()
            }
            QQC2.Button {
                text: "Subscriptions"; icon.name: "bookmarks"
                visible: podcasts.view !== "subs"
                onClicked: podcasts.showSubs()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: Kirigami.Units.largeSpacing
            Layout.rightMargin: Kirigami.Units.largeSpacing
            visible: podcasts.busy || podcasts.error !== ""
            spacing: Kirigami.Units.smallSpacing
            QQC2.BusyIndicator { running: podcasts.busy; visible: running; implicitWidth: Kirigami.Units.iconSizes.smallMedium; implicitHeight: implicitWidth }
            QQC2.Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: podcasts.busy ? podcasts.label : podcasts.error
                color: podcasts.busy ? Kirigami.Theme.textColor : Kirigami.Theme.negativeTextColor
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            // ---- subscriptions ----
            GridView {
                id: grid
                anchors.fill: parent
                anchors.margins: Kirigami.Units.smallSpacing
                visible: podcasts.view === "subs" && podcasts.subs.length > 0
                clip: true
                cellWidth: Math.max(140, Math.floor(width / Math.max(1, Math.floor(width / 170))))
                cellHeight: cellWidth + Kirigami.Units.gridUnit * 3
                model: podcasts.subs
                delegate: Item {
                    width: grid.cellWidth
                    height: grid.cellHeight
                    required property var modelData
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: Kirigami.Units.smallSpacing
                        spacing: 4
                        Art {
                            Layout.fillWidth: true
                            Layout.preferredHeight: width
                            source: modelData.image
                            Rectangle {
                                visible: modelData.unplayed > 0
                                anchors { top: parent.top; right: parent.right; margins: 6 }
                                height: Kirigami.Units.gridUnit * 1.2
                                width: Math.max(height, badge.implicitWidth + 10)
                                radius: height / 2
                                color: Kirigami.Theme.highlightColor
                                QQC2.Label { id: badge; anchors.centerIn: parent; text: modelData.unplayed; color: Kirigami.Theme.highlightedTextColor; font.pixelSize: Kirigami.Units.gridUnit * 0.7; font.bold: true }
                            }
                        }
                        QQC2.Label { text: modelData.title; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true; maximumLineCount: 1 }
                        QQC2.Label { text: modelData.author; opacity: 0.65; elide: Text.ElideRight; Layout.fillWidth: true; maximumLineCount: 1; font: Kirigami.Theme.smallFont }
                    }
                    MouseArea { anchors.fill: parent; onClicked: podcasts.openFeed(modelData.url); cursorShape: Qt.PointingHandCursor }
                }
            }
            ColumnLayout {
                anchors.centerIn: parent
                width: Math.min(parent.width - Kirigami.Units.gridUnit * 4, Kirigami.Units.gridUnit * 26)
                visible: podcasts.view === "subs" && podcasts.subs.length === 0
                spacing: Kirigami.Units.largeSpacing
                Kirigami.Icon { source: Qt.resolvedUrl("podcasts.svg"); isMask: true; color: Kirigami.Theme.disabledTextColor; Layout.alignment: Qt.AlignHCenter; implicitWidth: Kirigami.Units.iconSizes.huge; implicitHeight: implicitWidth }
                QQC2.Label {
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    opacity: 0.8
                    text: "No podcasts yet. Search for a show above, or paste an RSS feed address or an Apple Podcasts link, then press Subscribe."
                }
            }

            // ---- search results ----
            ListView {
                id: results
                anchors.fill: parent
                anchors.margins: Kirigami.Units.smallSpacing
                visible: podcasts.view === "results"
                clip: true
                spacing: Kirigami.Units.smallSpacing
                model: podcasts.results
                delegate: QQC2.ItemDelegate {
                    width: results.width
                    required property var modelData
                    onClicked: podcasts.openFeed(modelData.feed)
                    contentItem: RowLayout {
                        spacing: Kirigami.Units.largeSpacing
                        Art { Layout.preferredWidth: Kirigami.Units.gridUnit * 4; Layout.preferredHeight: Kirigami.Units.gridUnit * 4; source: modelData.image }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            QQC2.Label { text: modelData.title; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                            QQC2.Label { text: modelData.author; opacity: 0.7; elide: Text.ElideRight; Layout.fillWidth: true }
                            QQC2.Label { text: modelData.genre; opacity: 0.55; font: Kirigami.Theme.smallFont; visible: text !== "" }
                        }
                        QQC2.Label { text: modelData.subscribed ? "Subscribed" : ""; opacity: 0.7; visible: modelData.subscribed }
                        QQC2.Button { text: "Open"; onClicked: podcasts.openFeed(modelData.feed) }
                    }
                }
            }

            // ---- one show ----
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Kirigami.Units.smallSpacing
                visible: podcasts.view === "show"
                spacing: Kirigami.Units.smallSpacing

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.largeSpacing
                    Art { Layout.preferredWidth: Kirigami.Units.gridUnit * 7; Layout.preferredHeight: Kirigami.Units.gridUnit * 7; source: podcasts.show.image || "" }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        QQC2.Label { text: podcasts.show.title || ""; font.bold: true; font.pointSize: Kirigami.Theme.defaultFont.pointSize * 1.3; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        QQC2.Label { text: podcasts.show.author || ""; opacity: 0.7; Layout.fillWidth: true; elide: Text.ElideRight }
                        QQC2.Label { text: podcasts.show.desc || ""; opacity: 0.8; wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight; Layout.fillWidth: true }
                        QQC2.Button {
                            text: podcasts.show.subscribed ? "Unsubscribe" : "Subscribe"
                            icon.name: podcasts.show.subscribed ? "list-remove" : "list-add"
                            highlighted: !podcasts.show.subscribed
                            onClicked: podcasts.show.subscribed ? podcasts.unsubscribe() : podcasts.subscribe()
                        }
                    }
                }

                ListView {
                    id: eps
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: podcasts.episodes
                    delegate: QQC2.ItemDelegate {
                        id: row
                        width: eps.width
                        required property var modelData
                        property bool open: false
                        readonly property bool current: !!(pg.pod && pg.pod.key === modelData.key)
                        onClicked: open = !open
                        contentItem: RowLayout {
                            spacing: Kirigami.Units.largeSpacing
                            QQC2.RoundButton {
                                icon.name: row.current && pg.pod && pg.pod.playing ? "media-playback-pause" : "media-playback-start"
                                highlighted: row.current
                                onClicked: row.current ? pg.pod.toggle() : pg.playEpisode(modelData.key)
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2
                                QQC2.Label {
                                    text: modelData.title
                                    font.bold: !modelData.played
                                    opacity: modelData.played ? 0.6 : 1
                                    wrapMode: Text.Wrap
                                    maximumLineCount: row.open ? 5 : 2
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                QQC2.Label {
                                    text: [modelData.date, modelData.number, modelData.durText, modelData.played ? "Played" : ""].filter(function (x) { return x !== "" }).join("  ·  ")
                                    opacity: 0.6
                                    font: Kirigami.Theme.smallFont
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                                QQC2.ProgressBar { visible: modelData.progress > 0; value: modelData.progress; Layout.fillWidth: true }
                                QQC2.Label {
                                    visible: row.open && modelData.desc !== ""
                                    text: modelData.desc
                                    wrapMode: Text.Wrap
                                    opacity: 0.8
                                    Layout.fillWidth: true
                                }
                            }
                            QQC2.ToolButton {
                                icon.name: modelData.played ? "edit-undo" : "checkmark"
                                onClicked: podcasts.markPlayed(modelData.key, !modelData.played)
                                QQC2.ToolTip.text: modelData.played ? "Mark as not played" : "Mark as played"; QQC2.ToolTip.visible: hovered
                            }
                        }
                    }
                }
            }
        }

        // ---- the player ----
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: bar.implicitHeight + Kirigami.Units.largeSpacing * 2
            visible: pg.showing
            color: Kirigami.Theme.alternateBackgroundColor

            ColumnLayout {
                id: bar
                anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: Kirigami.Units.largeSpacing }
                spacing: 2
                QQC2.Label {
                    visible: !!(pg.pod && pg.pod.errorText !== "")
                    text: pg.pod ? pg.pod.errorText : ""
                    color: Kirigami.Theme.negativeTextColor
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing
                    Art { Layout.preferredWidth: Kirigami.Units.gridUnit * 2.6; Layout.preferredHeight: Kirigami.Units.gridUnit * 2.6; source: pg.pod && pg.pod.info.cover ? pg.pod.info.cover : "" }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        spacing: 0
                        QQC2.Label { text: pg.pod ? (pg.pod.info.title || "") : ""; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                        QQC2.Label { text: pg.pod ? (pg.pod.info.sub || "") : ""; opacity: 0.7; elide: Text.ElideRight; Layout.fillWidth: true; font: Kirigami.Theme.smallFont }
                    }
                    QQC2.ToolButton { icon.name: "media-seek-backward"; onClicked: pg.pod.skip(-15); QQC2.ToolTip.text: "Back 15 seconds"; QQC2.ToolTip.visible: hovered }
                    QQC2.RoundButton {
                        icon.name: pg.pod && pg.pod.playing ? "media-playback-pause" : "media-playback-start"
                        highlighted: true
                        onClicked: pg.pod.toggle()
                    }
                    QQC2.ToolButton { icon.name: "media-seek-forward"; onClicked: pg.pod.skip(30); QQC2.ToolTip.text: "Forward 30 seconds"; QQC2.ToolTip.visible: hovered }
                    QQC2.ComboBox {
                        id: speedBox
                        model: ["0.8×", "1×", "1.25×", "1.5×", "1.75×", "2×"]
                        readonly property var rates: [0.8, 1.0, 1.25, 1.5, 1.75, 2.0]
                        currentIndex: { var i = rates.indexOf(podcasts.speed); return i >= 0 ? i : 1 }
                        onActivated: (i) => podcasts.setSpeed(rates[i])
                    }
                    QQC2.ToolButton { icon.name: "window-close"; onClicked: pg.pod.stop(); QQC2.ToolTip.text: "Stop and close the player"; QQC2.ToolTip.visible: hovered }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing
                    QQC2.Label { text: pg.pod ? pg.fmt(pg.pod.player.position) : ""; opacity: 0.7; font: Kirigami.Theme.smallFont }
                    QQC2.Slider {
                        id: seekBar
                        Layout.fillWidth: true
                        from: 0; to: 1
                        onMoved: pg.pod.seek(value)
                        Binding on value {
                            when: !seekBar.pressed
                            value: pg.pod && pg.pod.player.duration > 0 ? pg.pod.player.position / pg.pod.player.duration : 0
                        }
                    }
                    QQC2.Label { text: pg.pod && pg.pod.player.duration > 0 ? pg.fmt(pg.pod.player.duration) : ""; opacity: 0.7; font: Kirigami.Theme.smallFont }
                }
            }
        }
    }
}
