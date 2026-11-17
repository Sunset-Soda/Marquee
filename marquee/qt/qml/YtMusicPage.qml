import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

// YouTube Music with no account, laid out like the Music tab: cover tiles to browse, an album page with a numbered
// song list, and an artist page. Search, albums and artists come from YouTube Music's catalogue (core/ytmusic.py);
// the songs play in the window's music player, so the mini player, Now Playing, lyrics and media keys all work.
Kirigami.Page {
    id: page

    property var music
    readonly property var view: ytmusic.view
    readonly property bool inView: ytmusic.inView
    readonly property color ink: Kirigami.Theme.textColor
    readonly property real gutter: Kirigami.Units.gridUnit * 2
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
    function shuffled(list) {
        var a = list.slice()
        for (var i = a.length - 1; i > 0; i--) {
            var j = Math.floor(Math.random() * (i + 1)), t = a[i]; a[i] = a[j]; a[j] = t
        }
        return a
    }
    function runSearch() {
        if (searchField.text.trim() === "") return
        ytmusic.search(searchField.text)
    }

    title: "YouTube Music"
    padding: 0
    background: Rectangle { color: applicationWindow().pane(0.78) }

    actions: [
        Kirigami.Action {
            text: "Clear Recently Played"
            icon.name: "edit-clear-history"
            visible: !page.inView && !ytmusic.searched && ytmusic.recent.length > 0
            onTriggered: ytmusic.clearRecent()
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
            QQC2.ToolButton {
                visible: page.inView || ytmusic.searched
                icon.name: "go-previous"
                text: page.inView ? "Back" : "Home"
                onClicked: page.inView ? ytmusic.back() : ytmusic.home()
            }
            Kirigami.SearchField {
                id: searchField
                Layout.fillWidth: true
                Layout.maximumWidth: Kirigami.Units.gridUnit * 24
                placeholderText: "Search songs, albums and artists"
                autoAccept: false
                onAccepted: page.runSearch()
                Keys.onReturnPressed: page.runSearch()
                Keys.onEnterPressed: page.runSearch()
            }
            QQC2.BusyIndicator { running: ytmusic.busy; visible: running; implicitHeight: Kirigami.Units.iconSizes.smallMedium; implicitWidth: implicitHeight }
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

    // ---- one song, in the Music tab's rounded rows --------------------------------------------------------------
    component TrackRow: Rectangle {
        id: row
        property var track
        property bool numbered: false            // show the album's track number; otherwise a small cover
        property bool showAlbum: false
        signal picked()
        readonly property bool playing: !!page.music && page.music.active && page.music.itemId === track.id
        implicitHeight: Kirigami.Units.gridUnit * 3.2
        radius: Kirigami.Units.gridUnit
        color: playing ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.18)
                       : (hover.hovered ? page.inkA(0.08) : "transparent")
        HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: row.picked() }
        RowLayout {
            anchors { fill: parent; leftMargin: Kirigami.Units.gridUnit; rightMargin: Kirigami.Units.gridUnit * 1.4 }
            spacing: Kirigami.Units.largeSpacing
            Item {
                Layout.preferredWidth: row.numbered ? Kirigami.Units.gridUnit * 2 : Kirigami.Units.gridUnit * 2.2
                Layout.preferredHeight: Kirigami.Units.gridUnit * 2.2
                QQC2.Label { anchors.centerIn: parent; visible: row.numbered && !hover.hovered && !row.playing; text: row.track.n > 0 ? row.track.n : ""; opacity: 0.7 }
                Cover {
                    anchors.fill: parent
                    visible: !row.numbered
                    boxSize: Kirigami.Units.gridUnit * 2.2
                    ratio: 1
                    source: row.track.cover || ""
                    title: row.track.title || ""
                    iconName: "audio-x-generic"
                }
                Kirigami.Icon {
                    anchors.centerIn: parent
                    width: Kirigami.Units.iconSizes.small; height: width
                    visible: hover.hovered || row.playing
                    source: row.playing && !hover.hovered ? "audio-volume-high" : "media-playback-start"
                    color: row.numbered ? (row.playing ? Kirigami.Theme.highlightColor : page.ink) : "white"
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0
                QQC2.Label {
                    Layout.fillWidth: true
                    text: row.track.title
                    elide: Text.ElideRight
                    font.weight: row.playing ? Font.Bold : Font.Normal
                    color: row.playing ? Kirigami.Theme.highlightColor : page.ink
                }
                QQC2.Label {
                    Layout.fillWidth: true
                    text: [row.track.artist, row.showAlbum ? row.track.album : ""].filter(function (x) { return !!x }).join(" · ")
                    opacity: 0.65; elide: Text.ElideRight; font: Kirigami.Theme.smallFont
                }
            }
            QQC2.Label { text: row.track.dur > 0 ? page.clock(row.track.dur) : ""; opacity: 0.7 }
        }
    }

    // the pill that heads a song list, as on the Music tab's album pages
    component ColumnHead: Rectangle {
        implicitHeight: Kirigami.Units.gridUnit * 2.2
        radius: height / 2
        color: "transparent"
        border.width: 1
        border.color: page.inkA(0.25)
        RowLayout {
            anchors { fill: parent; leftMargin: Kirigami.Units.gridUnit; rightMargin: Kirigami.Units.gridUnit * 1.4 }
            spacing: Kirigami.Units.largeSpacing
            QQC2.Label { text: "#"; opacity: 0.7; Layout.preferredWidth: Kirigami.Units.gridUnit * 2; horizontalAlignment: Text.AlignHCenter }
            QQC2.Label { text: "Title"; opacity: 0.7; Layout.fillWidth: true }
            Kirigami.Icon { source: "chronometer"; implicitWidth: Kirigami.Units.iconSizes.small; implicitHeight: implicitWidth; opacity: 0.7 }
        }
    }

    // Play and Shuffle for a list of songs
    component PlayButtons: RowLayout {
        property var tracks: []
        spacing: Kirigami.Units.largeSpacing
        QQC2.RoundButton {
            highlighted: true
            enabled: parent.tracks.length > 0
            icon.name: "media-playback-start"
            display: QQC2.AbstractButton.IconOnly
            text: "Play"
            onClicked: applicationWindow().playYtm(parent.tracks, 0)
            QQC2.ToolTip.text: text
            QQC2.ToolTip.visible: hovered
        }
        QQC2.RoundButton {
            enabled: parent.tracks.length > 0
            icon.name: "media-playlist-shuffle"
            display: QQC2.AbstractButton.IconOnly
            text: "Shuffle"
            onClicked: applicationWindow().playYtm(page.shuffled(parent.tracks), 0)
            QQC2.ToolTip.text: text
            QQC2.ToolTip.visible: hovered
        }
    }

    // cover tiles: albums, artists and playlists
    component TileGrid: GridView {
        id: tg
        signal picked(var item)
        property string empty: "Nothing found"
        property bool busy: false
        clip: true
        readonly property int cols: Math.max(2, Math.floor((width - page.gutter * 2) / (Kirigami.Units.gridUnit * 11)))
        leftMargin: page.gutter
        rightMargin: page.gutter
        topMargin: Kirigami.Units.largeSpacing
        bottomMargin: Kirigami.Units.gridUnit * 2
        cellWidth: Math.floor((width - page.gutter * 2) / cols)
        cellHeight: cellWidth + Kirigami.Units.gridUnit * 3.6
        boundsBehavior: Flickable.StopAtBounds
        maximumFlickVelocity: 9000
        flickDeceleration: 5000
        WheelHandler {
            acceptedDevices: PointerDevice.Mouse
            onWheel: event => tg.flick(0, event.angleDelta.y * 14)
        }
        QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
        delegate: QQC2.ItemDelegate {
            id: tile
            required property var modelData
            width: tg.cellWidth
            height: tg.cellHeight
            onClicked: tg.picked(modelData)
            contentItem: ColumnLayout {
                spacing: Kirigami.Units.smallSpacing
                Cover {
                    Layout.fillWidth: true
                    Layout.preferredHeight: width
                    boxSize: width
                    ratio: 1
                    source: tile.modelData.cover || ""
                    title: tile.modelData.title || tile.modelData.name || ""
                    iconName: "audio-x-generic"
                }
                QQC2.Label {
                    Layout.fillWidth: true
                    text: tile.modelData.title || tile.modelData.name || ""
                    wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
                    font.weight: Font.DemiBold
                }
                QQC2.Label {
                    Layout.fillWidth: true
                    text: tile.modelData.artist !== undefined
                          ? [tile.modelData.kind, tile.modelData.artist, tile.modelData.year].filter(function (x) { return !!x }).join(" · ")
                          : (tile.modelData.author !== undefined ? tile.modelData.author : "Artist")
                    opacity: 0.65; elide: Text.ElideRight; font: Kirigami.Theme.smallFont
                }
                Item { Layout.fillHeight: true }
            }
        }
        Kirigami.PlaceholderMessage {
            anchors.centerIn: parent
            width: parent.width - Kirigami.Units.gridUnit * 4
            visible: tg.count === 0 && !tg.busy
            icon.name: "audio-x-generic"
            text: tg.empty
        }
        QQC2.BusyIndicator { anchors.centerIn: parent; running: tg.busy && tg.count === 0; visible: running }
    }

    // ---- the screens ---------------------------------------------------------------------------------------------
    Kirigami.InlineMessage {
        id: problem
        z: 5
        anchors { top: parent.top; left: parent.left; right: parent.right; margins: Kirigami.Units.largeSpacing }
        type: Kirigami.MessageType.Error
        visible: ytmusic.error !== "" && !(page.inView && page.view.error)
        text: ytmusic.error
        actions: [ Kirigami.Action { text: "Try Again"; icon.name: "view-refresh"; onTriggered: ytmusic.retry() } ]
    }
    Kirigami.InlineMessage {
        id: noYtdlp
        z: 4
        anchors { top: parent.top; left: parent.left; right: parent.right; margins: Kirigami.Units.largeSpacing }
        anchors.topMargin: problem.visible ? problem.height + Kirigami.Units.largeSpacing * 2 : Kirigami.Units.largeSpacing
        type: Kirigami.MessageType.Warning
        visible: !youtube.available
        text: "Playing songs needs yt-dlp. Arch: sudo pacman -S yt-dlp deno yt-dlp-ejs ffmpeg"
    }

    // landing: what you played last
    ListView {
        id: landing
        anchors.fill: parent
        visible: !page.inView && !ytmusic.searched
        clip: true
        model: ytmusic.recent
        spacing: Kirigami.Units.smallSpacing
        topMargin: Kirigami.Units.gridUnit * 2
        bottomMargin: Kirigami.Units.gridUnit * 2
        boundsBehavior: Flickable.StopAtBounds
        maximumFlickVelocity: 9000
        flickDeceleration: 5000
        WheelHandler { acceptedDevices: PointerDevice.Mouse; onWheel: event => landing.flick(0, event.angleDelta.y * 14) }
        QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
        header: ColumnLayout {
            width: landing.width
            visible: ytmusic.recent.length > 0
            spacing: Kirigami.Units.smallSpacing
            Kirigami.Heading {
                Layout.leftMargin: page.gutter; Layout.rightMargin: page.gutter; Layout.fillWidth: true
                level: 1
                font.pixelSize: Kirigami.Units.gridUnit * 2.4
                font.weight: Font.Bold
                text: "Recently Played"
            }
            Item { Layout.preferredHeight: Kirigami.Units.largeSpacing }
        }
        delegate: Item {
            required property var modelData
            required property int index
            width: ListView.view.width
            height: rowItem.implicitHeight
            TrackRow {
                id: rowItem
                x: page.gutter
                width: parent.width - page.gutter * 2
                track: parent.modelData
                showAlbum: true
                onPicked: applicationWindow().playYtm(ytmusic.recent, parent.index)
            }
        }
        Kirigami.PlaceholderMessage {
            anchors.centerIn: parent
            width: parent.width - Kirigami.Units.gridUnit * 4
            visible: landing.count === 0
            icon.source: Qt.resolvedUrl("ytmusic.svg")
            text: "YouTube Music"
            explanation: "Search for a song, album or artist. No account needed, and nothing is sent about you."
        }
    }

    // search results: tabs, then the list for the chosen kind
    ColumnLayout {
        anchors.fill: parent
        visible: !page.inView && ytmusic.searched
        spacing: 0
        Item { Layout.preferredHeight: problem.visible ? problem.height + Kirigami.Units.largeSpacing : 0 }
        RowLayout {
            Layout.leftMargin: page.gutter
            Layout.topMargin: Kirigami.Units.largeSpacing
            Layout.bottomMargin: Kirigami.Units.smallSpacing
            spacing: Kirigami.Units.smallSpacing
            Repeater {
                model: [ { k: "songs", t: "Songs" }, { k: "albums", t: "Albums" }, { k: "artists", t: "Artists" }, { k: "playlists", t: "Playlists" } ]
                delegate: QQC2.Button {
                    required property var modelData
                    text: modelData.t
                    checkable: true
                    autoExclusive: true
                    checked: ytmusic.kind === modelData.k
                    onClicked: ytmusic.showKind(modelData.k)
                }
            }
        }
        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: ({ songs: 0, albums: 1, artists: 2, playlists: 3 })[ytmusic.kind]
            ListView {
                id: songResults
                clip: true
                model: ytmusic.songs
                spacing: Kirigami.Units.smallSpacing
                bottomMargin: Kirigami.Units.gridUnit * 2
                boundsBehavior: Flickable.StopAtBounds
                maximumFlickVelocity: 9000
                flickDeceleration: 5000
                WheelHandler { acceptedDevices: PointerDevice.Mouse; onWheel: event => songResults.flick(0, event.angleDelta.y * 14) }
                QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
                delegate: Item {
                    required property var modelData
                    required property int index
                    width: ListView.view.width
                    height: songRow.implicitHeight
                    TrackRow {
                        id: songRow
                        x: page.gutter
                        width: parent.width - page.gutter * 2
                        track: parent.modelData
                        showAlbum: true
                        onPicked: applicationWindow().playYtm(ytmusic.songs, parent.index)
                    }
                }
                Kirigami.PlaceholderMessage {
                    anchors.centerIn: parent
                    width: parent.width - Kirigami.Units.gridUnit * 4
                    visible: songResults.count === 0 && !ytmusic.busySearch
                    icon.name: "audio-x-generic"
                    text: "No songs found"
                }
                QQC2.BusyIndicator { anchors.centerIn: parent; running: ytmusic.busySearch && songResults.count === 0; visible: running }
            }
            TileGrid {
                model: ytmusic.albums
                busy: ytmusic.busySearch
                empty: "No albums found"
                onPicked: item => ytmusic.openAlbum(item.id, item.title, item.cover)
            }
            TileGrid {
                model: ytmusic.artists
                busy: ytmusic.busySearch
                empty: "No artists found"
                onPicked: item => ytmusic.openArtist(item.id, item.name, item.cover)
            }
            TileGrid {
                model: ytmusic.playlists
                busy: ytmusic.busySearch
                empty: "No playlists found"
                onPicked: item => ytmusic.openPlaylist(item.id, item.title, item.cover)
            }
        }
    }

    // an album or playlist: cover, title, the two big buttons and the numbered songs
    ListView {
        id: songsPage
        anchors.fill: parent
        visible: page.inView && page.view.type !== "artist"
        clip: true
        model: visible ? (page.view.tracks || []) : []
        spacing: Kirigami.Units.smallSpacing
        bottomMargin: Kirigami.Units.gridUnit * 2
        boundsBehavior: Flickable.StopAtBounds
        maximumFlickVelocity: 9000
        flickDeceleration: 5000
        WheelHandler { acceptedDevices: PointerDevice.Mouse; onWheel: event => songsPage.flick(0, event.angleDelta.y * 14) }
        QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
        header: ColumnLayout {
            width: songsPage.width
            spacing: Kirigami.Units.largeSpacing
            Item { Layout.preferredHeight: Kirigami.Units.gridUnit * 2 }
            RowLayout {
                Layout.leftMargin: page.gutter; Layout.rightMargin: page.gutter; Layout.fillWidth: true
                spacing: Kirigami.Units.gridUnit * 1.5
                Cover {
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 9
                    Layout.preferredHeight: Kirigami.Units.gridUnit * 9
                    boxSize: Kirigami.Units.gridUnit * 9
                    ratio: 1
                    source: page.view.cover || ""
                    title: page.view.title || ""
                    iconName: "audio-x-generic"
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing
                    Kirigami.Heading {
                        Layout.fillWidth: true
                        level: 1
                        font.weight: Font.Bold
                        text: page.view.title || ""
                        wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
                    }
                    QQC2.Label {
                        Layout.fillWidth: true
                        opacity: 0.7
                        wrapMode: Text.Wrap
                        text: {
                            var t = page.view.tracks || []
                            var parts = [page.view.type === "album" ? (page.view.kind || "Album") : "Playlist"]
                            var who = page.view.type === "album" ? page.view.artist : page.view.author
                            if (who) parts.push(who)
                            if (page.view.year) parts.push(page.view.year)
                            if (t.length > 0) parts.push(t.length + (t.length === 1 ? " song" : " songs"))
                            if (page.span(page.view.dur || 0) !== "") parts.push(page.span(page.view.dur))
                            return page.view.loading ? "Loading…" : parts.join(" · ")
                        }
                    }
                    PlayButtons { Layout.topMargin: Kirigami.Units.smallSpacing; tracks: page.view.tracks || [] }
                    QQC2.Button {
                        visible: page.view.type === "album" && !!page.view.artistId
                        flat: true
                        text: "Go to " + (page.view.artist || "artist")
                        icon.name: "view-media-artist"
                        onClicked: ytmusic.openArtist(page.view.artistId, page.view.artist, "")
                    }
                }
            }
            ColumnHead { Layout.leftMargin: page.gutter; Layout.rightMargin: page.gutter; Layout.fillWidth: true; visible: (page.view.tracks || []).length > 0 }
            Kirigami.PlaceholderMessage {
                Layout.fillWidth: true
                Layout.margins: Kirigami.Units.gridUnit * 2
                visible: !!page.view.error
                icon.name: "dialog-error"
                text: "Couldn’t load this"
                explanation: page.view.error || ""
                helpfulAction: Kirigami.Action { text: "Try Again"; icon.name: "view-refresh"; onTriggered: ytmusic.retry() }
            }
            QQC2.BusyIndicator { Layout.alignment: Qt.AlignHCenter; running: !!page.view.loading; visible: running }
        }
        delegate: Item {
            required property var modelData
            required property int index
            width: ListView.view.width
            height: albumRow.implicitHeight
            TrackRow {
                id: albumRow
                x: page.gutter
                width: parent.width - page.gutter * 2
                track: parent.modelData
                numbered: page.view.type === "album"
                showAlbum: page.view.type !== "album"
                onPicked: applicationWindow().playYtm(page.view.tracks, parent.index)
            }
        }
    }

    // an artist: their top songs, then albums and singles as tiles
    Flickable {
        id: artistPage
        anchors.fill: parent
        visible: page.inView && page.view.type === "artist"
        clip: true
        contentWidth: width
        contentHeight: artistCol.implicitHeight + Kirigami.Units.gridUnit * 3
        boundsBehavior: Flickable.StopAtBounds
        maximumFlickVelocity: 9000
        flickDeceleration: 5000
        WheelHandler { acceptedDevices: PointerDevice.Mouse; onWheel: event => artistPage.flick(0, event.angleDelta.y * 14) }
        QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
        ColumnLayout {
            id: artistCol
            width: artistPage.width
            spacing: Kirigami.Units.largeSpacing
            Item { Layout.preferredHeight: Kirigami.Units.gridUnit * 2 }
            RowLayout {
                Layout.leftMargin: page.gutter; Layout.rightMargin: page.gutter; Layout.fillWidth: true
                spacing: Kirigami.Units.gridUnit * 1.5
                Cover {
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 9
                    Layout.preferredHeight: Kirigami.Units.gridUnit * 9
                    boxSize: Kirigami.Units.gridUnit * 9
                    ratio: 1
                    source: page.view.cover || ""
                    title: page.view.name || page.view.title || ""
                    iconName: "view-media-artist"
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing
                    Kirigami.Heading {
                        Layout.fillWidth: true
                        level: 1
                        font.pixelSize: Kirigami.Units.gridUnit * 2.4
                        font.weight: Font.Bold
                        text: page.view.name || page.view.title || ""
                        wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
                    }
                    QQC2.Label {
                        Layout.fillWidth: true
                        opacity: 0.7
                        text: page.view.loading ? "Loading…" : (page.view.listeners ? page.view.listeners : "Artist")
                    }
                    PlayButtons { Layout.topMargin: Kirigami.Units.smallSpacing; tracks: page.view.songs || [] }
                }
            }
            Kirigami.PlaceholderMessage {
                Layout.fillWidth: true
                Layout.margins: Kirigami.Units.gridUnit * 2
                visible: !!page.view.error
                icon.name: "dialog-error"
                text: "Couldn’t load this artist"
                explanation: page.view.error || ""
                helpfulAction: Kirigami.Action { text: "Try Again"; icon.name: "view-refresh"; onTriggered: ytmusic.retry() }
            }
            QQC2.BusyIndicator { Layout.alignment: Qt.AlignHCenter; running: !!page.view.loading; visible: running }

            Kirigami.Heading {
                Layout.leftMargin: page.gutter; Layout.topMargin: Kirigami.Units.largeSpacing
                visible: (page.view.songs || []).length > 0
                level: 2; font.weight: Font.Bold; text: "Top Songs"
            }
            Repeater {
                model: artistPage.visible ? (page.view.songs || []) : []
                delegate: TrackRow {
                    required property var modelData
                    required property int index
                    Layout.leftMargin: page.gutter
                    Layout.rightMargin: page.gutter
                    Layout.fillWidth: true
                    track: modelData
                    numbered: true
                    showAlbum: true
                    onPicked: applicationWindow().playYtm(page.view.songs, index)
                }
            }

            Kirigami.Heading {
                Layout.leftMargin: page.gutter; Layout.topMargin: Kirigami.Units.gridUnit
                visible: (page.view.albums || []).length > 0
                level: 2; font.weight: Font.Bold; text: "Albums"
            }
            Flow {
                Layout.leftMargin: page.gutter; Layout.rightMargin: page.gutter; Layout.fillWidth: true
                spacing: Kirigami.Units.largeSpacing
                Repeater {
                    model: artistPage.visible ? (page.view.albums || []) : []
                    delegate: QQC2.ItemDelegate {
                        required property var modelData
                        width: Kirigami.Units.gridUnit * 11
                        height: Kirigami.Units.gridUnit * 11 + Kirigami.Units.gridUnit * 3.6
                        onClicked: ytmusic.openAlbum(modelData.id, modelData.title, modelData.cover)
                        contentItem: ColumnLayout {
                            spacing: Kirigami.Units.smallSpacing
                            Cover { Layout.fillWidth: true; Layout.preferredHeight: width; boxSize: width; ratio: 1; source: modelData.cover || ""; title: modelData.title; iconName: "audio-x-generic" }
                            QQC2.Label { Layout.fillWidth: true; text: modelData.title; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight; font.weight: Font.DemiBold }
                            QQC2.Label { Layout.fillWidth: true; text: [modelData.kind, modelData.year].filter(function (x) { return !!x }).join(" · "); opacity: 0.65; elide: Text.ElideRight; font: Kirigami.Theme.smallFont }
                            Item { Layout.fillHeight: true }
                        }
                    }
                }
            }

            Kirigami.Heading {
                Layout.leftMargin: page.gutter; Layout.topMargin: Kirigami.Units.gridUnit
                visible: (page.view.singles || []).length > 0
                level: 2; font.weight: Font.Bold; text: "Singles and EPs"
            }
            Flow {
                Layout.leftMargin: page.gutter; Layout.rightMargin: page.gutter; Layout.fillWidth: true
                spacing: Kirigami.Units.largeSpacing
                Repeater {
                    model: artistPage.visible ? (page.view.singles || []) : []
                    delegate: QQC2.ItemDelegate {
                        required property var modelData
                        width: Kirigami.Units.gridUnit * 11
                        height: Kirigami.Units.gridUnit * 11 + Kirigami.Units.gridUnit * 3.6
                        onClicked: ytmusic.openAlbum(modelData.id, modelData.title, modelData.cover)
                        contentItem: ColumnLayout {
                            spacing: Kirigami.Units.smallSpacing
                            Cover { Layout.fillWidth: true; Layout.preferredHeight: width; boxSize: width; ratio: 1; source: modelData.cover || ""; title: modelData.title; iconName: "audio-x-generic" }
                            QQC2.Label { Layout.fillWidth: true; text: modelData.title; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight; font.weight: Font.DemiBold }
                            QQC2.Label { Layout.fillWidth: true; text: [modelData.kind, modelData.year].filter(function (x) { return !!x }).join(" · "); opacity: 0.65; elide: Text.ElideRight; font: Kirigami.Theme.smallFont }
                            Item { Layout.fillHeight: true }
                        }
                    }
                }
            }
        }
    }
}
