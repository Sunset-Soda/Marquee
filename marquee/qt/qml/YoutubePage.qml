import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import QtQuick.Dialogs
import org.kde.kirigami as Kirigami

// YouTube, FreeTube style: no account. Subscriptions are kept here and their latest videos come from each channel's feed;
// search, channels and the stream itself come from yt-dlp. Optionally a Recommended tab uses your browser's login.
Kirigami.Page {
    id: page

    property var music
    property int searchMode: 0                   // 0 videos, 1 channels, 2 live now, 3 playlists
    readonly property bool searchChannels: searchMode === 1
    readonly property bool listMode: youtube.listMode      // Subscriptions and Recommended as a list instead of a grid (remembered)
    property string tab: "subs"                  // subs | recs | history | search
    readonly property bool inChannel: !!youtube.channel.id
    readonly property bool playlistOpen: !inChannel && !!youtube.playlist.id
    onInChannelChanged: chanTabs.currentIndex = 0
    readonly property color ink: Kirigami.Theme.textColor
    function inkA(a) { return Qt.rgba(ink.r, ink.g, ink.b, a) }

    title: "YouTube"
    padding: 0
    background: Rectangle { color: applicationWindow().pane(0.78) }

    Component.onCompleted: {
        if (youtube.available) {
            if (youtube.subs.length > 0) youtube.refreshFeed()
            if (youtube.browser !== "") youtube.refreshRecommended()
        }
    }
    function runSearch() {
        if (searchField.text.trim() === "" && searchMode !== 2) return
        if (youtube.openUrl(searchField.text)) return          // a pasted YouTube address: play, or open the playlist / channel
        youtube.closePlaylist()
        if (searchMode === 2) youtube.searchLive(searchField.text)
        else if (searchMode === 3) youtube.searchPlaylists(searchField.text)
        else youtube.search(searchField.text, searchChannels)
        tab = "search"
        youtube.closeChannel()
    }

    actions: [
        Kirigami.Action {
            text: "Refresh"
            icon.name: "view-refresh"
            onTriggered: { youtube.refreshFeed(); youtube.refreshRecommended() }
        },
        Kirigami.Action {
            text: page.listMode ? "Show as Grid" : "Show as List"
            icon.name: page.listMode ? "view-list-icons" : "view-list-details"
            visible: page.tab === "subs" || page.tab === "recs"
            onTriggered: youtube.setListMode(!youtube.listMode)
        },
        Kirigami.Action {
            text: "Import Subscriptions…"
            icon.name: "document-import"
            onTriggered: importHelp.open()
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
            Kirigami.SearchField {
                id: searchField
                Layout.fillWidth: true
                Layout.maximumWidth: Kirigami.Units.gridUnit * 24
                placeholderText: ["Search YouTube", "Search for channels", "Search live streams", "Search for playlists"][page.searchMode]
                autoAccept: false                       // otherwise it "accepts" while you are still typing, and Enter is ignored
                onAccepted: page.runSearch()
                Keys.onReturnPressed: page.runSearch()
                Keys.onEnterPressed: page.runSearch()
            }
            QQC2.ComboBox {
                model: ["Videos", "Channels", "Live", "Playlists"]
                onActivated: page.searchMode = currentIndex
            }
            QQC2.BusyIndicator { running: youtube.busySearch || youtube.busyFeed || youtube.busyRecs || youtube.busyChannel; visible: running; implicitHeight: Kirigami.Units.iconSizes.smallMedium; implicitWidth: implicitHeight }
            Item { Layout.fillWidth: true }
        }
    }

    footer: ColumnLayout {
        spacing: 0
        MiniPlayer {
            Layout.fillWidth: true
            music: page.music
            visible: page.music && page.music.active
            nowPlayingOpen: false
            onOpenRequested: applicationWindow().openNowPlaying()
        }
    }

    // ---- a video card, used by every list -------------------------------------------------------------------
    component VideoGrid: GridView {
        id: vg
        property string emptyTitle: "Nothing here yet"
        property string emptyText: ""
        property bool busy: false
        property bool list: false
        property bool inPlaylist: false              // clicking a video plays it with the rest of the playlist after it
        signal menu(var item, var video)
        clip: true
        model: []
        readonly property real want: Kirigami.Units.gridUnit * 17
        readonly property int cols: Math.max(1, Math.floor(width / want))
        cellWidth: list ? width : Math.floor(width / cols)
        cellHeight: list ? Kirigami.Units.gridUnit * 6 : cellWidth * 9 / 16 + Kirigami.Units.gridUnit * 5.4
        topMargin: Kirigami.Units.largeSpacing
        bottomMargin: Kirigami.Units.largeSpacing
        boundsBehavior: Flickable.StopAtBounds
        maximumFlickVelocity: 9000
        flickDeceleration: 5000
        WheelHandler {
            acceptedDevices: PointerDevice.Mouse
            onWheel: event => vg.flick(0, event.angleDelta.y * 14)
        }
        QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
        delegate: QQC2.ItemDelegate {
            id: card
            required property var modelData
            width: vg.cellWidth
            height: vg.cellHeight
            hoverEnabled: true
            onClicked: vg.inPlaylist ? youtube.playInPlaylist(modelData.id) : youtube.play(modelData.id)
            TapHandler { acceptedButtons: Qt.RightButton; onTapped: vg.menu(card, card.modelData) }
            readonly property real watched: youtube.positions[modelData.id] || 0
            readonly property string meta: [modelData.viewsText, modelData.agoText].filter(function (x) { return x !== "" }).join(" · ")
            contentItem: Loader { sourceComponent: vg.list ? rowView : tileView }

            // the picture, its length badge and a red bar showing how much you have watched
            Component {
                id: thumb
                Rectangle {
                    radius: Kirigami.Units.cornerRadius
                    color: page.inkA(0.1)
                    clip: true
                    Image {
                        anchors.fill: parent
                        source: card.modelData.thumb
                        fillMode: Image.PreserveAspectCrop
                        asynchronous: true
                        cache: true
                        sourceSize.width: 480
                    }
                    Rectangle {
                        visible: card.modelData.durText !== ""
                        anchors { right: parent.right; bottom: parent.bottom; margins: 6 }
                        width: durLabel.implicitWidth + 10
                        height: durLabel.implicitHeight + 4
                        radius: 4
                        color: card.modelData.live ? "#e62117" : "#cc000000"
                        Text { id: durLabel; anchors.centerIn: parent; text: card.modelData.durText; color: "white"; font.pixelSize: 12; font.weight: Font.DemiBold }
                    }
                    Rectangle {
                        visible: card.watched > 0
                        anchors { left: parent.left; bottom: parent.bottom }
                        width: parent.width * card.watched
                        height: 3
                        color: "#e62117"
                    }
                }
            }
            Component {
                id: tileView
                ColumnLayout {
                    spacing: Kirigami.Units.smallSpacing
                    Loader { Layout.fillWidth: true; Layout.preferredHeight: width * 9 / 16; sourceComponent: thumb }
                    QQC2.Label { Layout.fillWidth: true; text: card.modelData.title; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight; font.weight: Font.DemiBold }
                    QQC2.Label { Layout.fillWidth: true; text: card.modelData.channel; opacity: 0.7; elide: Text.ElideRight; font: Kirigami.Theme.smallFont }
                    QQC2.Label { Layout.fillWidth: true; text: card.meta; opacity: 0.55; elide: Text.ElideRight; font: Kirigami.Theme.smallFont }
                    Item { Layout.fillHeight: true }
                }
            }
            Component {
                id: rowView
                RowLayout {
                    spacing: Kirigami.Units.largeSpacing
                    Loader { Layout.fillHeight: true; Layout.preferredWidth: height * 16 / 9; sourceComponent: thumb }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Item { Layout.fillHeight: true }
                        QQC2.Label { Layout.fillWidth: true; text: card.modelData.title; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight; font.weight: Font.DemiBold }
                        QQC2.Label { Layout.fillWidth: true; text: [card.modelData.channel, card.meta].filter(function (x) { return x !== "" }).join(" · "); opacity: 0.65; elide: Text.ElideRight; font: Kirigami.Theme.smallFont }
                        Item { Layout.fillHeight: true }
                    }
                }
            }
        }
        Kirigami.PlaceholderMessage {
            anchors.centerIn: parent
            width: parent.width - Kirigami.Units.gridUnit * 4
            visible: vg.count === 0 && !vg.busy
            icon.name: "video-x-generic"
            text: vg.emptyTitle
            explanation: vg.emptyText
        }
        QQC2.BusyIndicator { anchors.centerIn: parent; running: vg.busy && vg.count === 0; visible: running }
    }

    // ---- playlists ----------------------------------------------------------------------------------------
    component PlaylistGrid: GridView {
        id: plGrid
        property bool busy: false
        clip: true
        readonly property int cols: Math.max(1, Math.floor(width / (Kirigami.Units.gridUnit * 15)))
        cellWidth: Math.floor(width / cols)
        cellHeight: cellWidth * 9 / 16 + Kirigami.Units.gridUnit * 4.4
        topMargin: Kirigami.Units.largeSpacing
        boundsBehavior: Flickable.StopAtBounds
        maximumFlickVelocity: 9000
        flickDeceleration: 5000
        WheelHandler {
            acceptedDevices: PointerDevice.Mouse
            onWheel: event => plGrid.flick(0, event.angleDelta.y * 14)
        }
        QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
        delegate: QQC2.ItemDelegate {
            id: plCard
            required property var modelData
            width: plGrid.cellWidth
            height: plGrid.cellHeight
            onClicked: youtube.openPlaylist(modelData.id, modelData.title)
            contentItem: ColumnLayout {
                spacing: Kirigami.Units.smallSpacing
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: width * 9 / 16
                    radius: Kirigami.Units.cornerRadius
                    color: page.inkA(0.1)
                    clip: true
                    Image { anchors.fill: parent; source: plCard.modelData.thumb; fillMode: Image.PreserveAspectCrop; asynchronous: true; sourceSize.width: 480; visible: status === Image.Ready }
                    Kirigami.Icon { anchors.centerIn: parent; visible: !plCard.modelData.thumb; source: "view-media-playlist"; width: Kirigami.Units.iconSizes.large; height: width }
                    Rectangle {
                        visible: plCard.modelData.count > 0
                        anchors { right: parent.right; bottom: parent.bottom; margins: 6 }
                        width: cntLabel.implicitWidth + 10; height: cntLabel.implicitHeight + 4; radius: 4; color: "#cc000000"
                        Text { id: cntLabel; anchors.centerIn: parent; text: plCard.modelData.count + " videos"; color: "white"; font.pixelSize: 12; font.weight: Font.DemiBold }
                    }
                }
                QQC2.Label { Layout.fillWidth: true; text: plCard.modelData.title; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight; font.weight: Font.DemiBold }
                QQC2.Label { Layout.fillWidth: true; visible: !!plCard.modelData.channel; text: plCard.modelData.channel || ""; opacity: 0.7; elide: Text.ElideRight; font: Kirigami.Theme.smallFont }
                Item { Layout.fillHeight: true }
            }
        }
        Kirigami.PlaceholderMessage {
            anchors.centerIn: parent
            width: parent.width - Kirigami.Units.gridUnit * 4
            visible: plGrid.count === 0 && !plGrid.busy
            icon.name: "view-media-playlist"
            text: youtube.error !== "" ? "Couldn’t load playlists" : "No playlists"
            explanation: youtube.error
        }
        QQC2.BusyIndicator { anchors.centerIn: parent; running: plGrid.busy && plGrid.count === 0; visible: running }
    }

    // one playlist opened: its videos, with Play All
    component PlaylistPane: ColumnLayout {
        property string backText: "Back"
        spacing: 0
        RowLayout {
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.largeSpacing
            QQC2.ToolButton { icon.name: "go-previous"; text: backText; onClicked: youtube.closePlaylist() }
            Kirigami.Heading { Layout.fillWidth: true; level: 2; text: youtube.playlist.title || ""; elide: Text.ElideRight }
            QQC2.Button {
                icon.name: "media-playback-start"
                text: "Play All"
                enabled: (youtube.playlist.videos || []).length > 0
                onClicked: youtube.playInPlaylist(youtube.playlist.videos[0].id)
            }
        }
        VideoGrid {
            Layout.fillWidth: true
            Layout.fillHeight: true
            inPlaylist: true
            list: true
            model: youtube.playlist.videos || []
            busy: youtube.busyPlaylist
            emptyTitle: youtube.error !== "" ? "Couldn’t load this playlist" : "No videos"
            emptyText: youtube.error
            onMenu: (item, v) => videoMenu.openFor(v)
        }
    }

    // ---- yt-dlp is missing -------------------------------------------------------------------------------------
    Kirigami.PlaceholderMessage {
        anchors.centerIn: parent
        width: Math.min(parent.width - Kirigami.Units.gridUnit * 4, Kirigami.Units.gridUnit * 32)
        visible: !youtube.available
        icon.name: "video-x-generic"
        text: "YouTube needs yt-dlp"
        explanation: "Marquee uses yt-dlp, a separate program, to search YouTube and find video streams. Install it, then check again.\n\nArch: sudo pacman -S yt-dlp\nFedora: sudo dnf install yt-dlp\nDebian/Ubuntu: sudo apt install yt-dlp"
        helpfulAction: Kirigami.Action { text: "Check Again"; icon.name: "view-refresh"; onTriggered: { youtube.recheck(); if (youtube.available) youtube.refreshFeed() } }
    }

    ColumnLayout {
        anchors.fill: parent
        visible: youtube.available
        spacing: 0

        Kirigami.InlineMessage {
            Layout.fillWidth: true
            visible: youtube.needsJs
            type: Kirigami.MessageType.Warning
            text: "Videos won’t play yet: yt-dlp needs a JavaScript runtime to unlock YouTube streams. Install one (Arch: sudo pacman -S deno yt-dlp-ejs), restart Marquee and try again."
        }

        // ---- a playlist opened from search results
        PlaylistPane {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: page.playlistOpen
            backText: "Results"
        }

        // ---- tabs (hidden while a channel is open)
        QQC2.TabBar {
            id: tabs
            Layout.fillWidth: true
            visible: !page.inChannel && !page.playlistOpen
            currentIndex: ["subs", "recs", "history", "search"].indexOf(page.tab)
            QQC2.TabButton { text: "Subscriptions"; onClicked: page.tab = "subs" }
            QQC2.TabButton { text: "Recommended"; visible: youtube.browser !== ""; width: visible ? implicitWidth : 0; onClicked: { page.tab = "recs"; if (youtube.recommended.length === 0) youtube.refreshRecommended() } }
            QQC2.TabButton { text: "History"; onClicked: page.tab = "history" }
            QQC2.TabButton { text: "Search"; onClicked: page.tab = "search" }
        }

        // ---- one channel
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: page.inChannel
            spacing: 0
            RowLayout {
                Layout.fillWidth: true
                Layout.margins: Kirigami.Units.largeSpacing
                spacing: Kirigami.Units.largeSpacing
                QQC2.ToolButton { icon.name: "go-previous"; text: "Back"; onClicked: youtube.closeChannel() }
                Kirigami.Heading { Layout.fillWidth: true; level: 1; font.weight: Font.Bold; text: youtube.channel.name || ""; elide: Text.ElideRight }
                QQC2.Button {
                    highlighted: !youtube.channel.subbed
                    icon.name: youtube.channel.subbed ? "list-remove" : "list-add"
                    text: youtube.channel.subbed ? "Unsubscribe" : "Subscribe"
                    onClicked: youtube.channel.subbed ? youtube.unsubscribe(youtube.channel.id) : youtube.subscribe(youtube.channel.id, youtube.channel.name, "")
                }
            }
            QQC2.TabBar {
                id: chanTabs
                Layout.fillWidth: true
                onCurrentIndexChanged: if (currentIndex === 1) youtube.loadPlaylists()
                QQC2.TabButton { text: "Videos" }
                QQC2.TabButton { text: "Playlists" }
            }
            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: chanTabs.currentIndex
                VideoGrid {
                    model: youtube.channel.videos || []
                    busy: youtube.busyChannel
                    emptyTitle: youtube.error !== "" ? "Couldn’t load this channel" : "No videos"
                    emptyText: youtube.error
                    onMenu: (item, v) => videoMenu.openFor(v)
                }
                // playlists: the list of them, or one opened
                Item {
                    PlaylistPane { anchors.fill: parent; visible: !!youtube.playlist.id; backText: "Playlists" }
                    PlaylistGrid { anchors.fill: parent; visible: !youtube.playlist.id; model: youtube.playlists; busy: youtube.busyPlaylists }
                }
            }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: !page.inChannel && !page.playlistOpen
            currentIndex: ["subs", "recs", "history", "search"].indexOf(page.tab)

            // subscriptions: the latest videos from the channels you follow
            ColumnLayout {
                spacing: 0
                VideoGrid {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    list: page.listMode
                    model: youtube.feed
                    busy: youtube.busyFeed
                    emptyTitle: youtube.subs.length === 0 ? "No subscriptions yet" : "No new videos"
                    emptyText: youtube.subs.length === 0 ? "Search for a channel and subscribe, or right-click any video and choose Subscribe. Subscriptions stay on this computer; no account is needed." : youtube.error
                    onMenu: (item, v) => videoMenu.openFor(v)
                }
            }
            // recommended
            VideoGrid {
                list: page.listMode
                model: youtube.recommended
                busy: youtube.busyRecs
                emptyTitle: youtube.error !== "" ? "Couldn’t load recommendations" : "No recommendations yet"
                emptyText: youtube.error !== "" ? youtube.error : "Marquee asks YouTube for your home page using the login in your browser."
                onMenu: (item, v) => videoMenu.openFor(v)
            }
            // history
            ColumnLayout {
                spacing: 0
                RowLayout {
                    Layout.margins: Kirigami.Units.largeSpacing
                    visible: youtube.history.length > 0
                    QQC2.Label { Layout.fillWidth: true; text: youtube.history.length + " watched"; opacity: 0.7 }
                    QQC2.Button { text: "Clear History"; icon.name: "edit-clear-history"; onClicked: youtube.clearHistory() }
                }
                VideoGrid {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: youtube.history
                    emptyTitle: "Nothing watched yet"
                    emptyText: "Videos you play show up here. History stays on this computer."
                    onMenu: (item, v) => videoMenu.openFor(v)
                }
            }
            // search results: videos, or channels
            Item {
                VideoGrid {
                    anchors.fill: parent
                    visible: page.searchMode !== 1 && page.searchMode !== 3
                    model: youtube.results
                    busy: youtube.busySearch
                    emptyTitle: youtube.error !== "" ? "Search failed" : "Search YouTube"
                    emptyText: youtube.error !== "" ? youtube.error : "Type in the box at the top and press Enter."
                    onMenu: (item, v) => videoMenu.openFor(v)
                }
                PlaylistGrid {
                    anchors.fill: parent
                    visible: page.searchMode === 3
                    model: youtube.playlistResults
                    busy: youtube.busySearch
                }
                ListView {
                    anchors.fill: parent
                    visible: page.searchChannels
                    clip: true
                    model: youtube.channelResults
                    QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
                    delegate: QQC2.ItemDelegate {
                        id: crow
                        required property var modelData
                        width: ListView.view.width
                        height: Kirigami.Units.gridUnit * 4
                        onClicked: youtube.openChannel(modelData.id, modelData.name)
                        contentItem: RowLayout {
                            spacing: Kirigami.Units.largeSpacing
                            Rectangle {
                                Layout.preferredWidth: Kirigami.Units.gridUnit * 2.8
                                Layout.preferredHeight: Kirigami.Units.gridUnit * 2.8
                                radius: width / 2
                                color: page.inkA(0.12)
                                clip: true
                                Image { anchors.fill: parent; source: crow.modelData.thumb; asynchronous: true; fillMode: Image.PreserveAspectCrop; visible: status === Image.Ready }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0
                                QQC2.Label { Layout.fillWidth: true; text: crow.modelData.name; font.weight: Font.DemiBold; elide: Text.ElideRight }
                                QQC2.Label { visible: crow.modelData.subs > 0; text: crow.modelData.subs + " subscribers"; opacity: 0.6; font: Kirigami.Theme.smallFont }
                            }
                            QQC2.Button {
                                highlighted: !crow.modelData.subbed
                                text: crow.modelData.subbed ? "Subscribed" : "Subscribe"
                                onClicked: crow.modelData.subbed ? youtube.unsubscribe(crow.modelData.id) : youtube.subscribe(crow.modelData.id, crow.modelData.name, crow.modelData.thumb)
                            }
                        }
                    }
                    QQC2.Label {
                        anchors.centerIn: parent
                        visible: parent.count === 0 && !youtube.busySearch
                        text: youtube.error !== "" ? youtube.error : "Search for a channel by name."
                        opacity: 0.6
                    }
                }
            }
        }
    }

    // opening a video takes a moment: yt-dlp is asking YouTube for the stream
    Rectangle {
        anchors.fill: parent
        visible: youtube.resolving !== ""
        color: "#99000000"
        z: 10
        MouseArea { anchors.fill: parent }
        ColumnLayout {
            anchors.centerIn: parent
            QQC2.BusyIndicator { Layout.alignment: Qt.AlignHCenter; running: parent.visible }
            Text { Layout.alignment: Qt.AlignHCenter; text: "Opening video…"; color: "white"; font.pixelSize: 16 }
            Text { Layout.alignment: Qt.AlignHCenter; visible: youtube.note !== ""; text: "Downloading " + youtube.note; color: "#ccffffff"; font.pixelSize: 13 }
        }
    }
    Kirigami.InlineMessage {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: Kirigami.Units.largeSpacing }
        z: 11
        visible: youtube.error !== "" && youtube.resolving === "" && page.tab !== "search" && !page.inChannel && (page.tab !== "subs" || youtube.feed.length > 0 || youtube.subs.length > 0)
        type: Kirigami.MessageType.Error
        text: youtube.error
    }

    FileDialog {
        id: takeoutDialog
        title: "Choose subscriptions.csv"
        nameFilters: ["Subscriptions (*.csv)", "All files (*)"]
        onAccepted: youtube.importTakeout(selectedFile)
    }
    Connections {
        target: youtube
        function onImported(added, had) {
            applicationWindow().showPassiveNotification(added === 0 ? "No new channels (" + had + " already subscribed)" : "Subscribed to " + added + " channels" + (had > 0 ? " (" + had + " already subscribed)" : ""))
        }
    }
    Kirigami.Dialog {
        id: importHelp
        title: "Import Subscriptions"
        standardButtons: Kirigami.Dialog.Cancel
        padding: Kirigami.Units.largeSpacing
        customFooterActions: [
            Kirigami.Action { text: "Choose subscriptions.csv…"; icon.name: "document-open"; onTriggered: { importHelp.close(); takeoutDialog.open() } }
        ]
        ColumnLayout {
        QQC2.Label {
            Layout.preferredWidth: Kirigami.Units.gridUnit * 26
            wrapMode: Text.Wrap
            text: "1. Go to takeout.google.com and press “Deselect all”.\n2. Scroll to YouTube and YouTube Music and tick it, then press “All YouTube data included” and keep only “subscriptions”.\n3. Create the export and download it, then unzip it.\n4. Choose the file Takeout › YouTube and YouTube Music › subscriptions › subscriptions.csv below."
        }
        }
    }

    QQC2.Menu {
        id: videoMenu
        property var video: ({})
        function openFor(v) { video = v; popup() }
        QQC2.MenuItem { text: "Play"; icon.name: "media-playback-start"; onTriggered: youtube.play(videoMenu.video.id) }
        QQC2.MenuItem {
            text: "Open Channel"
            icon.name: "view-media-artist"
            visible: !!videoMenu.video.channelId
            height: visible ? implicitHeight : 0
            onTriggered: youtube.openChannel(videoMenu.video.channelId, videoMenu.video.channel)
        }
        QQC2.MenuItem {
            text: "Subscribe to " + (videoMenu.video.channel || "Channel")
            icon.name: "list-add"
            visible: !!videoMenu.video.channelId
            height: visible ? implicitHeight : 0
            onTriggered: youtube.subscribe(videoMenu.video.channelId, videoMenu.video.channel, "")
        }
        QQC2.MenuItem { text: "Copy Link"; icon.name: "edit-copy"; onTriggered: youtube.copy("https://www.youtube.com/watch?v=" + videoMenu.video.id) }
    }
}
