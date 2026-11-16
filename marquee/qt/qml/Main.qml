import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import QtQuick.Dialogs
import org.kde.kirigami as Kirigami

Kirigami.ApplicationWindow {
    id: root

    title: "Marquee"
    minimumWidth: Kirigami.Units.gridUnit * 20
    minimumHeight: Kirigami.Units.gridUnit * 22
    width: Kirigami.Units.gridUnit * 58
    height: Kirigami.Units.gridUnit * 38

    // Translucent window. Better Blur DX (or KWin's own Blur effect) can then blur what is behind it.
    // Needs a restart to switch, because the window's alpha buffer is chosen when it is created.
    readonly property bool glass: marquee.translucent
    color: glass ? "transparent" : Kirigami.Theme.backgroundColor
    function pane(alpha) {
        var c = Kirigami.Theme.backgroundColor
        return Qt.rgba(c.r, c.g, c.b, glass ? alpha : 1)
    }

    function openFiles() { filesDialog.open() }
    function openSubtitleSettings() { subSettings.open() }
    function openSettings() { settings.open() }
    function openFolder() { folderDialog.open() }
    function pickCover(itemId) { coverDialog.itemId = itemId; coverDialog.open() }
    // True while a video or song fills the whole screen: the sidebar and toolbars are hidden then.
    readonly property bool playerFull: visibility === Window.FullScreen && pageStack.depth > 1
    function leavePlayer() {                                                  // stops playback and keeps its place
        var p = pageStack.lastItem
        if (p && p.stopPlayback) p.stopPlayback()
        while (pageStack.depth > 1) pageStack.pop()
    }
    // Songs play in the window's own music player, so they carry on while you browse. Videos play on their page.
    readonly property var music: musicEngine
    MusicEngine { id: musicEngine }
    // Live TV's single player, shared by the Live TV page's preview and the full-size player.
    readonly property var live: liveEngine
    LiveEngine { id: liveEngine }
    function openNowPlaying() {
        if (pageStack.depth <= 1 && musicEngine.active)
            pageStack.push(Qt.resolvedUrl("PlayerPage.qml"), { itemId: musicEngine.itemId, music: musicEngine, live: liveEngine })
    }
    // Live TV replaces the library page in place (depth stays 1), so the player still sits one level above either.
    property bool liveActive: false
    property bool youtubeActive: false
    readonly property bool inLibrary: !liveActive && !youtubeActive
    // The channel playing in the small preview box on the Live TV page ("" = none). Set when you leave a live
    // channel with Back or Guide, so it carries on while you look for another.
    property string pipId: ""
    function showLive() {
        leavePlayer()
        if (liveActive) return
        pageStack.replace(Qt.resolvedUrl("LivePage.qml"), { music: musicEngine })
        liveActive = true
        youtubeActive = false
    }
    function showYoutube() {
        pipId = ""
        liveEngine.stop()
        leavePlayer()
        if (youtubeActive) return
        pageStack.replace(Qt.resolvedUrl("YoutubePage.qml"), { music: musicEngine })
        youtubeActive = true
        liveActive = false
    }
    function showLibrary() {
        pipId = ""
        liveEngine.stop()
        leavePlayer()
        if (inLibrary) return
        pageStack.replace(Qt.resolvedUrl("LibraryPage.qml"), { music: musicEngine })
        liveActive = false
        youtubeActive = false
    }
    function playLive(channelId) {
        pipId = ""
        musicEngine.stop()
        pageStack.push(Qt.resolvedUrl("PlayerPage.qml"), { itemId: channelId, music: musicEngine, live: liveEngine })
    }
    function play(itemId) {
        var info = marquee.itemInfo(itemId)
        marquee.startQueue(itemId)
        liveEngine.stop()
        if (info.media === "video") {
            musicEngine.stop()
            pageStack.push(Qt.resolvedUrl("PlayerPage.qml"), { itemId: itemId, music: musicEngine, live: liveEngine })
        } else {
            musicEngine.play(itemId)
            openNowPlaying()
        }
    }

    globalDrawer: Kirigami.GlobalDrawer {
        id: drawer
        title: "Marquee"
        titleIcon: Qt.resolvedUrl("../icon.svg")
        modal: !root.wideScreen || root.playerFull
        handleVisible: !root.playerFull
        background: Rectangle { color: root.pane(root.wideScreen ? 0.55 : 0.96) }
        collapsible: false
        showHeaderWhenCollapsed: false
        width: Kirigami.Units.gridUnit * 14

        QQC2.Label {
            text: "Media"
            font.weight: Font.DemiBold
            color: Kirigami.Theme.disabledTextColor
            Layout.leftMargin: Kirigami.Units.largeSpacing
            Layout.topMargin: Kirigami.Units.smallSpacing
        }
        Repeater {
            model: marquee.collections
            delegate: QQC2.ItemDelegate {
                required property var modelData
                Layout.fillWidth: true
                highlighted: root.inLibrary && marquee.collection === modelData.id
                onClicked: {
                    root.showLibrary()
                    marquee.setCollection(modelData.id)
                    if (drawer.modal) drawer.close()
                }
                contentItem: RowLayout {
                    spacing: Kirigami.Units.largeSpacing
                    Kirigami.Icon {
                        // Movies and Music: the theme's own icons are coloured; these follow the text colour like the others
                        readonly property bool plain: modelData.id === "movie" || modelData.id === "music"
                        source: plain ? Qt.resolvedUrl(modelData.id + ".svg") : modelData.icon
                        isMask: plain
                        color: Kirigami.Theme.textColor
                        implicitWidth: Kirigami.Units.iconSizes.smallMedium
                        implicitHeight: Kirigami.Units.iconSizes.smallMedium
                    }
                    QQC2.Label { text: modelData.name; Layout.fillWidth: true; elide: Text.ElideRight }
                    QQC2.Label {
                        text: modelData.count > 0 ? modelData.count : ""
                        opacity: 0.7
                        font: Kirigami.Theme.smallFont
                    }
                }
            }
        }
        QQC2.Label {
            text: "Livestreaming"
            font.weight: Font.DemiBold
            color: Kirigami.Theme.disabledTextColor
            Layout.leftMargin: Kirigami.Units.largeSpacing
            Layout.topMargin: Kirigami.Units.largeSpacing
        }
        QQC2.ItemDelegate {
            Layout.fillWidth: true
            highlighted: root.liveActive
            onClicked: {
                root.showLive()
                if (drawer.modal) drawer.close()
            }
            contentItem: RowLayout {
                spacing: Kirigami.Units.largeSpacing
                Kirigami.Icon {
                    source: "video-television"
                    implicitWidth: Kirigami.Units.iconSizes.smallMedium
                    implicitHeight: Kirigami.Units.iconSizes.smallMedium
                }
                QQC2.Label { text: "Live TV"; Layout.fillWidth: true; elide: Text.ElideRight }
            }
        }
        QQC2.ItemDelegate {
            Layout.fillWidth: true
            highlighted: root.youtubeActive
            onClicked: {
                root.showYoutube()
                if (drawer.modal) drawer.close()
            }
            contentItem: RowLayout {
                spacing: Kirigami.Units.largeSpacing
                Kirigami.Icon {
                    source: Qt.resolvedUrl("stream.svg")
                    isMask: true
                    color: Kirigami.Theme.textColor
                    implicitWidth: Kirigami.Units.iconSizes.smallMedium
                    implicitHeight: Kirigami.Units.iconSizes.smallMedium
                }
                QQC2.Label { text: "YouTube"; Layout.fillWidth: true; elide: Text.ElideRight }
            }
        }
        Item { Layout.fillHeight: true }
    }

    Binding { target: drawer; property: "drawerOpen"; value: false; when: root.playerFull }

    pageStack.initialPage: LibraryPage { music: musicEngine }
    pageStack.globalToolBar.style: Kirigami.ApplicationHeaderStyle.ToolBar
    Component.onCompleted: pageStack.columnView.columnResizeMode = Kirigami.ColumnView.SingleColumn

    Connections {
        target: youtube
        function onReady(id) {
            musicEngine.stop()
            liveEngine.stop()
            if (pageStack.depth > 1) pageStack.pop()
            pageStack.push(Qt.resolvedUrl("PlayerPage.qml"), { itemId: id, music: musicEngine, live: liveEngine })
        }
    }

    Connections {
        target: marquee
        function onToast(text) { root.showPassiveNotification(text) }
    }

    FileDialog {
        id: filesDialog
        title: "Add Media Files"
        fileMode: FileDialog.OpenFiles
        nameFilters: ["Video and audio (*.mp4 *.m4v *.mov *.webm *.mkv *.avi *.ogv *.mpg *.mpeg *.ts *.3gp *.mp3 *.m4a *.aac *.flac *.wav *.ogg *.oga *.opus *.wma *.aif *.aiff)", "All files (*)"]
        onAccepted: marquee.addUrls(selectedFiles)
    }
    FolderDialog {
        id: folderDialog
        title: "Add Folder"
        onAccepted: marquee.addUrls([selectedFolder])
    }
    FileDialog {
        id: coverDialog
        property string itemId
        title: "Choose Cover Image"
        nameFilters: ["Images (*.png *.jpg *.jpeg *.webp *.bmp)", "All files (*)"]
        onAccepted: marquee.setCover(itemId, selectedFile)
    }

    Shortcut { sequences: ["Ctrl+,"]; onActivated: settings.open() }
    Kirigami.Dialog {
        id: settings
        title: "Settings"
        standardButtons: Kirigami.Dialog.Close
        padding: Kirigami.Units.largeSpacing
        readonly property var styles: [["bars", "Bars"], ["mirror", "Mirror"], ["wave", "Wave"], ["dots", "Dots"], ["scope", "Scope"], ["off", "Off"]]
        onAboutToShow: {
            var cur = marquee.vizStyle()
            vizBox.currentIndex = Math.max(0, styles.findIndex(function (x) { return x[0] === cur }))
            lyricsBox.checked = marquee.lyricsDefault()
            translucentBox.checked = marquee.translucent
            liveAutoBox.checked = marquee.liveAutoplay()
            syncBox.checked = marquee.autoSyncSubtitles()
            var bs = youtube.browsers
            ytFastBox.checked = youtube.fastStart
            ytCacheBox.currentIndex = Math.max(0, ytCacheBox.keys.indexOf(youtube.cacheMode))
            ytBrowserBox.currentIndex = Math.max(0, bs.findIndex(function (x) { return x[0] === youtube.browser }))
        }
        ColumnLayout {
            spacing: Kirigami.Units.largeSpacing
            Kirigami.FormLayout {
                Layout.minimumWidth: Kirigami.Units.gridUnit * 22
                Kirigami.Separator { Kirigami.FormData.isSection: true; Kirigami.FormData.label: "Window" }
                QQC2.CheckBox {
                    id: translucentBox
                    text: "Translucent window (lets blur show through; restart to apply)"
                    onToggled: marquee.setTranslucent(checked)
                }
                Kirigami.Separator { Kirigami.FormData.isSection: true; Kirigami.FormData.label: "Music" }
                QQC2.ComboBox {
                    id: vizBox
                    Kirigami.FormData.label: "Visualiser:"
                    model: settings.styles.map(function (x) { return x[1] })
                    onActivated: marquee.setVizStyle(settings.styles[currentIndex][0])
                }
                QQC2.CheckBox {
                    id: lyricsBox
                    text: "Show lyrics when a song starts"
                    onToggled: marquee.setLyricsDefault(checked)
                }
                Kirigami.Separator { Kirigami.FormData.isSection: true; Kirigami.FormData.label: "Live TV" }
                QQC2.CheckBox {
                    id: liveAutoBox
                    text: "Start playing the last channel when Live TV opens"
                    onToggled: marquee.setLiveAutoplay(checked)
                }
                Kirigami.Separator { Kirigami.FormData.isSection: true; Kirigami.FormData.label: "YouTube" }
                QQC2.ComboBox {
                    id: ytBrowserBox
                    Kirigami.FormData.label: "Recommended tab:"
                    model: youtube.browsers.map(function (x) { return x[1] })
                    onActivated: youtube.setBrowser(youtube.browsers[currentIndex][0])
                }
                QQC2.Label {
                    Layout.maximumWidth: Kirigami.Units.gridUnit * 22
                    wrapMode: Text.Wrap
                    opacity: 0.7
                    font: Kirigami.Theme.smallFont
                    text: "Optional. Pick the browser where you are signed in to YouTube and Marquee reads its login to fetch your Recommended videos, the way youtube.com does. That gives Marquee access to your YouTube session, and YouTube may not like automated use of an account, so leave it off if you would rather not risk it. Everything else works without an account."
                }
                QQC2.ComboBox {
                    id: ytQualityBox
                    Kirigami.FormData.label: "Default quality:"
                    readonly property var keys: ["auto", "1080", "720", "480", "360"]
                    model: ["Auto (by connection)", "1080p", "720p", "480p", "360p (starts fastest)"]
                    currentIndex: Math.max(0, keys.indexOf(youtube.quality))
                    onActivated: youtube.setQuality(keys[currentIndex])
                }
                RowLayout {
                    Kirigami.FormData.label: "Saved videos:"
                    QQC2.ComboBox {
                        id: ytCacheBox
                        readonly property var keys: ["1", "3", "10", "exit"]
                        model: ["Keep 1 video", "Keep 3 videos", "Keep 10 videos", "Delete when Marquee closes"]
                        onActivated: youtube.setCacheMode(keys[currentIndex])
                    }
                    QQC2.Label { text: "Using " + youtube.cacheText; opacity: 0.7 }
                    QQC2.Button { text: "Clear Now"; onClicked: youtube.clearCache() }
                }
                QQC2.CheckBox {
                    id: ytFastBox
                    text: "Start quickly at 360p, then switch to the chosen quality"
                    onToggled: youtube.setFastStart(checked)
                }
                Kirigami.Separator { Kirigami.FormData.isSection: true; Kirigami.FormData.label: "Subtitles" }
                QQC2.CheckBox {
                    id: syncBox
                    text: "Line downloaded subtitles up with the speech"
                    onToggled: marquee.setAutoSyncSubtitles(checked)
                }
                QQC2.Button {
                    text: "OpenSubtitles Account…"
                    icon.name: "preferences-system-network"
                    onClicked: { settings.close(); root.openSubtitleSettings() }
                }
            }
        }
    }

    Kirigami.Dialog {
        id: subSettings
        title: "Subtitle Settings"
        standardButtons: Kirigami.Dialog.Save | Kirigami.Dialog.Cancel
        padding: Kirigami.Units.largeSpacing
        onAboutToShow: {
            var c = marquee.subtitleSettings()
            keyField.text = c.apiKey; userField.text = c.username; passField.text = c.password
            langField.text = c.language; autoBox.checked = c.auto
        }
        onAccepted: marquee.saveSubtitleSettings(keyField.text, userField.text, passField.text, langField.text, autoBox.checked)
        ColumnLayout {
            spacing: Kirigami.Units.largeSpacing
            QQC2.Label {
                Layout.maximumWidth: Kirigami.Units.gridUnit * 24
                wrapMode: Text.Wrap
                text: "Marquee downloads subtitles from OpenSubtitles.com. Make a free account there, then create an API key under Profile › API consumers, and enter them below. They are saved only on this computer."
            }
            Kirigami.FormLayout {
                QQC2.TextField { id: keyField; Kirigami.FormData.label: "API key:"; Layout.minimumWidth: Kirigami.Units.gridUnit * 16 }
                QQC2.TextField { id: userField; Kirigami.FormData.label: "Username:" }
                QQC2.TextField { id: passField; Kirigami.FormData.label: "Password:"; echoMode: TextInput.Password }
                QQC2.TextField { id: langField; Kirigami.FormData.label: "Language code:"; placeholderText: "en" }
                QQC2.CheckBox { id: autoBox; text: "Download automatically when a video has none" }
            }
        }
    }

    // Back and Forward: Alt+Left / Alt+Right, Alt+Home, and the back and forward buttons on a mouse.
    Shortcut { sequences: ["Alt+Left"]; enabled: pageStack.depth === 1 && root.inLibrary; onActivated: marquee.back() }
    Shortcut { sequences: ["Alt+Right"]; enabled: pageStack.depth === 1 && root.inLibrary; onActivated: marquee.forward() }
    Shortcut { sequences: ["Alt+Home"]; enabled: pageStack.depth === 1 && root.inLibrary; onActivated: marquee.home() }
    TapHandler { acceptedButtons: Qt.BackButton; enabled: pageStack.depth === 1 && root.inLibrary; onTapped: marquee.back() }
    TapHandler { acceptedButtons: Qt.ForwardButton; enabled: pageStack.depth === 1 && root.inLibrary; onTapped: marquee.forward() }

    // Dropping files anywhere on the window adds them. DropArea does not take mouse input.
    DropArea {
        anchors.fill: parent
        keys: ["text/uri-list"]
        onDropped: (drop) => { if (drop.hasUrls) marquee.addUrls(drop.urls) }
    }
}
