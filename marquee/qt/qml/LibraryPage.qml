import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

Kirigami.Page {
    id: page
    padding: 0

    property bool listMode: false
    property real coverSize: Kirigami.Units.gridUnit * 9
    property string menuId: ""
    property var music            // the window's music player: its bar shows above the status line while a song plays
    property var menuItem: ({ node: "", kind: "", title: "", fav: false })

    background: Rectangle { color: applicationWindow().pane(0.78) }
    title: marquee.heading
    titleDelegate: RowLayout {
        Kirigami.Heading { level: 1; text: page.title; Layout.fillWidth: true; elide: Text.ElideRight }
        QQC2.BusyIndicator { running: marquee.busy; visible: running; implicitHeight: Kirigami.Units.iconSizes.smallMedium; implicitWidth: implicitHeight }
    }

    actions: [
        Kirigami.Action {
            text: "Shuffle All"
            icon.name: "media-playlist-shuffle"
            visible: marquee.inArtist
            onTriggered: { var id = marquee.shuffleArtist(); if (id) applicationWindow().play(id) }
        },
        Kirigami.Action {
            text: "Add Folder…"
            icon.name: "folder-open"
            onTriggered: applicationWindow().openFolder()
        },
        Kirigami.Action {
            text: "Add Files…"
            icon.name: "list-add"
            onTriggered: applicationWindow().openFiles()
        },
        Kirigami.Action {
            text: "Find Missing Artwork and Episode Names"
            icon.name: "download"
            displayHint: Kirigami.DisplayHint.AlwaysHide
            onTriggered: marquee.findAllCovers()
        },
        Kirigami.Action {
            text: page.listMode ? "Show as Grid" : "Show as List"
            icon.name: page.listMode ? "view-list-icons" : "view-list-details"
            onTriggered: page.listMode = !page.listMode
        },
        Kirigami.Action {
            text: "Settings"
            icon.name: "configure"
            displayHint: Kirigami.DisplayHint.IconOnly
            tooltip: "Settings (Ctrl+,)"
            onTriggered: applicationWindow().openSettings()
        }
    ]

    header: QQC2.ToolBar {
        background: Rectangle { color: applicationWindow().pane(0.6) }
        contentItem: RowLayout {
            spacing: Kirigami.Units.largeSpacing
            RowLayout {
                spacing: 0
                QQC2.ToolButton {
                    icon.name: "go-previous"
                    enabled: marquee.canGoBack
                    onClicked: marquee.back()
                    QQC2.ToolTip.text: "Back (Alt+Left)"
                    QQC2.ToolTip.visible: hovered
                }
                QQC2.ToolButton {
                    icon.name: "go-next"
                    enabled: marquee.canGoForward
                    onClicked: marquee.forward()
                    QQC2.ToolTip.text: "Forward (Alt+Right)"
                    QQC2.ToolTip.visible: hovered
                }
                QQC2.ToolButton {
                    icon.name: "go-home"
                    enabled: marquee.canGoHome
                    onClicked: marquee.home()
                    QQC2.ToolTip.text: "Top of " + marquee.collectionName + " (Alt+Home)"
                    QQC2.ToolTip.visible: hovered
                }
            }
            Kirigami.SearchField {
                id: search
                Layout.fillWidth: true
                Layout.maximumWidth: Kirigami.Units.gridUnit * 20
                placeholderText: marquee.searchHint
                onTextChanged: marquee.setQuery(text)
                Connections {
                    target: marquee
                    function onStateChanged() { if (search.text !== marquee.query) search.text = marquee.query }
                }
            }
            Item { Layout.fillWidth: true }
            QQC2.ToolButton {
                text: "Clear Continue…"
                icon.name: "edit-clear-history"
                visible: marquee.onContinue
                enabled: grid.count > 0
                onClicked: clearDialog.open()
            }
            QQC2.TabBar {
                id: tabs
                currentIndex: ["library", "continue", "favorites"].indexOf(marquee.tab)
                onCurrentIndexChanged: {
                    var t = ["library", "continue", "favorites"][currentIndex]
                    if (t !== marquee.tab) marquee.setTab(t)        // not when Back or Forward moved the tab for us
                }
                QQC2.TabButton { text: "Library" }
                QQC2.TabButton { text: "Continue" }
                QQC2.TabButton { text: "Favorites" }
            }
            QQC2.Slider {
                visible: !page.listMode && page.width > Kirigami.Units.gridUnit * 34
                from: Kirigami.Units.gridUnit * 6
                to: Kirigami.Units.gridUnit * 16
                value: page.coverSize
                implicitWidth: Kirigami.Units.gridUnit * 7
                onMoved: page.coverSize = value
                QQC2.ToolTip.text: "Cover size"
                QQC2.ToolTip.visible: hovered
            }
        }
    }

    // The music bar sits in the page itself, not the window, so it lines up with this page and not the sidebar.
    footer: ColumnLayout {
        spacing: 0
        MiniPlayer {
            Layout.fillWidth: true
            music: page.music
            visible: page.music && page.music.active
            nowPlayingOpen: false
            onOpenRequested: applicationWindow().openNowPlaying()
        }
        QQC2.ToolBar {
            Layout.fillWidth: true
            background: Rectangle { color: applicationWindow().pane(0.6) }
            position: QQC2.ToolBar.Footer
            contentItem: RowLayout {
                QQC2.Label { text: marquee.statusText; opacity: 0.7 }
                Item { Layout.fillWidth: true }
                QQC2.ToolButton {
                    text: "Find Missing Artwork"
                    flat: true
                    visible: page.width > Kirigami.Units.gridUnit * 30
                    onClicked: marquee.findAllCovers()
                }
            }
        }
    }

    // ---- context menu shared by grid and list -------------------------------------------------------
    QQC2.Menu {
        id: itemMenu
        readonly property bool isNode: (page.menuItem || ({})).node !== undefined && (page.menuItem || ({})).node !== ""
        readonly property bool isShow: (page.menuItem || ({})).kind === "show"
        QQC2.MenuItem { text: "Play"; icon.name: "media-playback-start"; visible: !itemMenu.isNode; height: visible ? implicitHeight : 0; onTriggered: applicationWindow().play(page.menuId) }
        QQC2.MenuItem { text: "Open"; icon.name: "document-open"; visible: itemMenu.isNode; height: visible ? implicitHeight : 0; onTriggered: { var p = marquee.activate(page.menuId); if (p) applicationWindow().play(p) } }
        QQC2.MenuItem {
            text: "Remove from Continue"
            icon.name: "edit-clear-history"
            visible: marquee.onContinue && !itemMenu.isNode
            height: visible ? implicitHeight : 0
            onTriggered: marquee.removeFromContinue(page.menuId)
        }
        QQC2.MenuSeparator {}
        QQC2.MenuItem {
            text: itemMenu.isShow ? "Find Episode Names and Artwork" : "Find Cover Art"
            icon.name: "search"
            visible: itemMenu.isShow || (page.menuItem || ({})).kind === "movie" || (page.menuItem || ({})).kind === "music" || (page.menuItem || ({})).kind === "podcast" || (page.menuItem || ({})).kind === "book"
            height: visible ? implicitHeight : 0
            onTriggered: marquee.findCover(page.menuId)
        }
        QQC2.MenuItem {
            text: "Change Match…"
            icon.name: "edit-find-replace"
            visible: itemMenu.isShow
            height: visible ? implicitHeight : 0
            onTriggered: matchDialog.open()
        }
        QQC2.MenuItem { text: "Choose Cover Image…"; icon.name: "image-x-generic"; visible: !itemMenu.isNode; height: visible ? implicitHeight : 0; onTriggered: applicationWindow().pickCover(page.menuId) }
        QQC2.MenuItem {
            text: (page.menuItem || ({})).fav ? "Remove from Favorites" : "Add to Favorites"
            icon.name: (page.menuItem || ({})).fav ? "non-starred-symbolic" : "starred-symbolic"
            visible: !itemMenu.isNode
            height: visible ? implicitHeight : 0
            onTriggered: marquee.toggleFav(page.menuId)
        }
        QQC2.MenuItem { text: "Edit Info…"; icon.name: "document-edit"; visible: !itemMenu.isNode; height: visible ? implicitHeight : 0; onTriggered: { editDialog.open() } }
        QQC2.MenuSeparator { visible: !itemMenu.isNode; height: visible ? implicitHeight : 0 }
        QQC2.MenuItem { text: "Remove from Library"; icon.name: "edit-delete"; visible: !itemMenu.isNode; height: visible ? implicitHeight : 0; onTriggered: removeDialog.open() }
    }
    function showMenu(delegateItem, m) {
        page.menuId = m.itemId
        page.menuItem = m
        itemMenu.popup(delegateItem)
    }

    Kirigami.Dialog {
        id: editDialog
        title: "Edit Info"
        standardButtons: Kirigami.Dialog.Ok | Kirigami.Dialog.Cancel
        padding: Kirigami.Units.largeSpacing
        onAboutToShow: { titleField.text = (page.menuItem || ({})).title || ""; subField.text = (page.menuItem || ({})).sub || ""; kindBox.currentIndex = Math.max(0, kindBox.indexOfValue((page.menuItem || ({})).kind)) }
        onAccepted: marquee.edit(page.menuId, titleField.text, subField.text, kindBox.currentValue)
        Kirigami.FormLayout {
            QQC2.TextField { id: titleField; Kirigami.FormData.label: "Title:"; Layout.minimumWidth: Kirigami.Units.gridUnit * 16 }
            QQC2.TextField { id: subField; Kirigami.FormData.label: "Artist, year or episode:" }
            QQC2.ComboBox { id: kindBox; Kirigami.FormData.label: "Media:"; model: marquee.kinds; textRole: "name"; valueRole: "id" }
        }
    }
    Kirigami.Dialog {
        id: matchDialog
        title: "Change Match"
        standardButtons: Kirigami.Dialog.Ok | Kirigami.Dialog.Cancel
        padding: Kirigami.Units.largeSpacing
        onAboutToShow: { matchField.text = marquee.showName((page.menuItem || ({})).showKey); matchField.forceActiveFocus(); matchField.selectAll() }
        onAccepted: marquee.matchShow((page.menuItem || ({})).showKey, matchField.text)
        ColumnLayout {
            spacing: Kirigami.Units.largeSpacing
            QQC2.Label {
                Layout.maximumWidth: Kirigami.Units.gridUnit * 20
                wrapMode: Text.Wrap
                text: "Type the show's name as TVmaze lists it. Add the year if there is more than one, for example “Ted 2024”. Artwork and episode names for the whole show are replaced."
            }
            QQC2.TextField { id: matchField; Layout.fillWidth: true; onAccepted: matchDialog.accept() }
        }
    }
    Kirigami.PromptDialog {
        id: clearDialog
        title: "Clear Continue?"
        subtitle: "Everything leaves the Continue list and will start from the beginning next time. Nothing is deleted from your library."
        standardButtons: Kirigami.Dialog.Cancel
        customFooterActions: [
            Kirigami.Action {
                text: "Clear"
                icon.name: "edit-clear-history"
                onTriggered: { marquee.clearContinue(); clearDialog.close() }
            }
        ]
    }
    Kirigami.PromptDialog {
        id: removeDialog
        title: "Remove “" + ((page.menuItem || ({})).title || "") + "”?"
        subtitle: "It leaves the library and its saved cover is deleted. The file on your disk is not touched."
        standardButtons: Kirigami.Dialog.Cancel
        customFooterActions: [
            Kirigami.Action {
                text: "Remove"
                icon.name: "edit-delete"
                onTriggered: { marquee.remove(page.menuId); removeDialog.close() }
            }
        ]
    }

    // ---- content -------------------------------------------------------------------------------------
    // An artist opens as every album with its songs; everything else is the grid or list of tiles.
    ArtistView {
        id: artistView
        anchors.fill: parent
        visible: marquee.inArtist
        music: page.music
        onMenuRequested: (item, row) => page.showMenu(item, row)
    }
    GridView {
        id: grid
        anchors.fill: parent
        visible: !marquee.inArtist
        QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
        model: marquee.items
        clip: true
        topMargin: Kirigami.Units.largeSpacing
        bottomMargin: Kirigami.Units.largeSpacing
        readonly property real cellPad: Kirigami.Units.largeSpacing * 2
        readonly property int columns: Math.max(1, Math.floor(width / (page.coverSize + cellPad * 2)))
        cellWidth: page.listMode ? width : Math.floor(width / columns)
        cellHeight: page.listMode ? Kirigami.Units.gridUnit * 3.5 : page.coverSize * 1.5 + Kirigami.Units.gridUnit * 4.6
        boundsBehavior: Flickable.StopAtBounds
        // Quicker scrolling that keeps its smooth glide: each wheel notch throws the grid a good way. (A touchpad keeps its own.)
        maximumFlickVelocity: 9000
        flickDeceleration: 5000
        WheelHandler {
            acceptedDevices: PointerDevice.Mouse
            onWheel: event => grid.flick(0, event.angleDelta.y * 14)
        }

        delegate: QQC2.ItemDelegate {
            id: cell
            required property var model
            required property int index
            width: grid.cellWidth
            height: grid.cellHeight
            hoverEnabled: true
            onClicked: { var p = marquee.activate(model.itemId); if (p) applicationWindow().play(p) }
            TapHandler { acceptedButtons: Qt.RightButton; onTapped: page.showMenu(cell, cell.model) }
            TapHandler { acceptedButtons: Qt.NoButton; acceptedDevices: PointerDevice.TouchScreen; onLongPressed: page.showMenu(cell, cell.model) }
            QQC2.ToolTip.text: model.title
            QQC2.ToolTip.visible: hovered && !page.listMode
            QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
            Keys.onMenuPressed: page.showMenu(cell, cell.model)

            contentItem: Loader {
                sourceComponent: page.listMode ? rowView : tileView
            }

            Component {
                id: tileView
                ColumnLayout {
                    spacing: Kirigami.Units.smallSpacing
                    Item {
                        Layout.fillWidth: true
                        Layout.preferredHeight: page.coverSize * 1.5
                        Cover {
                            anchors.horizontalCenter: parent.horizontalCenter
                            anchors.bottom: parent.bottom
                            boxSize: Math.min(page.coverSize, parent.width)
                            source: cell.model.cover
                            title: cell.model.title
                            iconName: cell.model.iconName
                            ratio: cell.model.ratio
                            progress: cell.model.progress
                        }
                    }
                    QQC2.Label {
                        Layout.fillWidth: true
                        text: cell.model.title
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.Wrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                    }
                    QQC2.Label {
                        Layout.fillWidth: true
                        visible: text.length > 0
                        text: cell.model.sub
                        opacity: 0.7
                        font: Kirigami.Theme.smallFont
                        horizontalAlignment: Text.AlignHCenter
                        elide: Text.ElideRight
                    }
                    Item { Layout.fillHeight: true }
                }
            }
            Component {
                id: rowView
                RowLayout {
                    spacing: Kirigami.Units.largeSpacing
                    Cover {
                        Layout.preferredWidth: Kirigami.Units.gridUnit * 2.4
                        Layout.preferredHeight: Kirigami.Units.gridUnit * 2.4
                        boxSize: Kirigami.Units.gridUnit * 2.4
                        ratio: 1
                        source: cell.model.cover
                        title: cell.model.title
                        iconName: cell.model.iconName
                    }
                    ColumnLayout {
                        spacing: 0
                        Layout.fillWidth: true
                        QQC2.Label { text: cell.model.title; Layout.fillWidth: true; elide: Text.ElideRight }
                        QQC2.Label { text: cell.model.sub; visible: text.length > 0; opacity: 0.7; font: Kirigami.Theme.smallFont; Layout.fillWidth: true; elide: Text.ElideRight }
                    }
                    QQC2.Label {
                        visible: cell.model.duration > 0
                        text: { var s = Math.floor(cell.model.duration), h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60); s = s % 60
                                return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(s).padStart(2, "0") }
                        opacity: 0.7
                        Layout.minimumWidth: Kirigami.Units.gridUnit * 3
                        horizontalAlignment: Text.AlignRight
                    }
                }
            }
        }

        Kirigami.PlaceholderMessage {
            anchors.centerIn: parent
            width: parent.width - Kirigami.Units.gridUnit * 4
            visible: grid.count === 0
            icon.name: marquee.tab === "favorites" ? "starred-symbolic" : "applications-multimedia"
            text: marquee.emptyTitle
            explanation: marquee.emptyText
            helpfulAction: marquee.canAdd ? addAction : null
        }
    }

    Kirigami.Action {
        id: addAction
        text: "Add Folder…"
        icon.name: "folder-open"
        onTriggered: applicationWindow().openFolder()
    }
}
