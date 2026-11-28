import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import QtQuick.Dialogs
import QtMultimedia
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
    function openSubtitleSettings() { settings.open() }
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
    // Podcast episodes play in the window's podcast player, so one carries on while you browse. Music and episodes never play together.
    readonly property var podcast: podEngine
    PodcastEngine { id: podEngine }
    Connections { target: musicEngine; function onTrackChanged() { if (musicEngine.active) podEngine.pause() } }
    // Live TV's single player, shared by the Live TV page's preview and the full-size player.
    readonly property var live: liveEngine
    LiveEngine { id: liveEngine }

    // Media keys and the desktop's media widget (MPRIS, see mpris.py). Whatever is on screen answers the keys:
    // a video or channel page if one is open, otherwise the music player.
    readonly property var mediaPage: (pageStack.depth > 1 && pageStack.lastItem && pageStack.lastItem.toggle) ? pageStack.lastItem : null
    // the Twitch page is the window's page (not one pushed on top), so it is looked up on its own
    readonly property var twitchPlaying: (twitchActive && pageStack.depth <= 1 && pageStack.lastItem && pageStack.lastItem.mediaPlaying) ? pageStack.lastItem : null
    Timer {
        interval: 700; repeat: true; running: true
        onTriggered: {
            var pg = root.mediaPage
            var tp = root.twitchPlaying
            if (tp)
                marquee.setMediaState(tp.mediaTitle, tp.mediaSub, "", tp.mediaIsPlaying, true)
            else if (pg && pg.player)
                marquee.setMediaState(pg.info.title || "", pg.info.sub || "", pg.info.cover || "",
                                      pg.player.playbackState === MediaPlayer.PlayingState, true)
            else if (podEngine.active && !musicEngine.active)
                marquee.setMediaState(podEngine.info.title || "", podEngine.info.sub || "", podEngine.info.cover || "", podEngine.playing, true)
            else
                marquee.setMediaState(musicEngine.info.title || "", musicEngine.info.sub || "", musicEngine.info.cover || "",
                                      musicEngine.playing, musicEngine.active)
        }
    }
    Connections {
        target: marquee
        function onMediaCommand(cmd) {
            var pg = root.mediaPage
            var live = pg ? pg.isLive : false
            var tp = root.twitchPlaying
            if (tp) {
                if (cmd === "playpause" || (cmd === "play" && !tp.mediaIsPlaying) || (cmd === "pause" && tp.mediaIsPlaying)) tp.mediaToggle()
                else if (cmd === "next") tp.jumpLive()
                else if (cmd === "stop") tp.mediaPause()
            } else if (pg) {
                var p = pg.player, playing = p.playbackState === MediaPlayer.PlayingState
                if (cmd === "playpause" || (cmd === "play" && !playing) || (cmd === "pause" && playing)) pg.toggle()
                else if (cmd === "next") pg.step(1)
                else if (cmd === "previous") pg.step(-1)
                else if (cmd === "stop") { p.pause() }
            } else if (podEngine.active && !musicEngine.active) {
                if (cmd === "playpause" || (cmd === "play" && !podEngine.playing) || (cmd === "pause" && podEngine.playing)) podEngine.toggle()
                else if (cmd === "next") podEngine.skip(30)
                else if (cmd === "previous") podEngine.skip(-15)
                else if (cmd === "stop") podEngine.pause()
            } else if (musicEngine.active) {
                if (cmd === "playpause" || (cmd === "play" && !musicEngine.playing) || (cmd === "pause" && musicEngine.playing)) musicEngine.toggle()
                else if (cmd === "next") musicEngine.next(false)
                else if (cmd === "previous") musicEngine.previous()
                else if (cmd === "stop") musicEngine.stop()
            }
        }
    }
    function openNowPlaying() {
        if (pageStack.depth <= 1 && musicEngine.active)
            pageStack.push(Qt.resolvedUrl("PlayerPage.qml"), { itemId: musicEngine.itemId, music: musicEngine, live: liveEngine })
    }
    // Live TV replaces the library page in place (depth stays 1), so the player still sits one level above either.
    property bool liveActive: false
    property bool youtubeActive: false
    property bool ytmusicActive: false
    property bool shortsActive: false
    property bool podcastsActive: false
    property bool twitchActive: false
    readonly property bool inLibrary: !liveActive && !youtubeActive && !ytmusicActive && !shortsActive && !podcastsActive && !twitchActive
    // The channel playing in the small preview box on the Live TV page ("" = none). Set when you leave a live
    // channel with Back or Guide, so it carries on while you look for another.
    property string pipId: ""
    function showLive() {
        leavePlayer()
        if (liveActive) return
        pageStack.replace(Qt.resolvedUrl("LivePage.qml"), { music: musicEngine })
        liveActive = true
        youtubeActive = false
        ytmusicActive = false
        shortsActive = false
        podcastsActive = false
        twitchActive = false
    }
    function showYoutube() {
        pipId = ""
        liveEngine.stop()
        leavePlayer()
        if (youtubeActive) return
        pageStack.replace(Qt.resolvedUrl("YoutubePage.qml"), { music: musicEngine })
        youtubeActive = true
        shortsActive = false
        podcastsActive = false
        twitchActive = false
        liveActive = false
        ytmusicActive = false
        shortsActive = false
        podcastsActive = false
        twitchActive = false
    }
    function showYtMusic() {
        pipId = ""
        liveEngine.stop()
        leavePlayer()
        if (ytmusicActive) return
        pageStack.replace(Qt.resolvedUrl("YtMusicPage.qml"), { music: musicEngine })
        ytmusicActive = true
        shortsActive = false
        podcastsActive = false
        twitchActive = false
        youtubeActive = false
        liveActive = false
    }
    function showShorts() {
        pipId = ""
        liveEngine.stop()
        leavePlayer()
        if (shortsActive) return
        pageStack.replace(Qt.resolvedUrl("ShortsPage.qml"), { music: musicEngine, pod: podEngine })
        shortsActive = true
        podcastsActive = false
        twitchActive = false
        ytmusicActive = false
        youtubeActive = false
        liveActive = false
    }
    function showTwitch() {
        pipId = ""
        liveEngine.stop()
        leavePlayer()
        if (twitchActive) return
        pageStack.replace(Qt.resolvedUrl("TwitchPage.qml"), { music: musicEngine, pod: podEngine })
        twitchActive = true
        podcastsActive = false
        shortsActive = false
        ytmusicActive = false
        youtubeActive = false
        liveActive = false
    }
    function showPodcasts() {
        pipId = ""
        liveEngine.stop()
        leavePlayer()
        if (podcastsActive) return
        pageStack.replace(Qt.resolvedUrl("PodcastsPage.qml"), { music: musicEngine, pod: podEngine, win: root })
        podcastsActive = true
        twitchActive = false
        shortsActive = false
        ytmusicActive = false
        youtubeActive = false
        liveActive = false
    }
    // Play a list of YouTube Music tracks from one of them. They are not library files, so the queue is built here.
    function playYtm(tracks, index) {
        pipId = ""
        liveEngine.stop()
        var id = ytmusic.setQueue(tracks, index)
        if (id !== "") musicEngine.play(id)
    }
    function showLibrary() {
        pipId = ""
        liveEngine.stop()
        leavePlayer()
        if (inLibrary) return
        pageStack.replace(Qt.resolvedUrl("LibraryPage.qml"), { music: musicEngine })
        liveActive = false
        youtubeActive = false
        ytmusicActive = false
        shortsActive = false
        podcastsActive = false
        twitchActive = false
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
                // Podcasts is the feeds page (search, RSS, subscriptions); podcast files on this computer are one button away
                readonly property bool feeds: modelData.id === "podcast"
                highlighted: feeds ? root.podcastsActive : (root.inLibrary && marquee.collection === modelData.id)
                onClicked: {
                    if (feeds) root.showPodcasts()
                    else {
                        root.showLibrary()
                        marquee.setCollection(modelData.id)
                    }
                    if (drawer.modal) drawer.close()
                }
                contentItem: RowLayout {
                    spacing: Kirigami.Units.largeSpacing
                    Kirigami.Icon {
                        // Movies and Music: the theme's own icons are coloured; these follow the text colour like the others
                        readonly property bool plain: modelData.id === "movie" || modelData.id === "music" || modelData.id === "podcast"
                        source: plain ? Qt.resolvedUrl((modelData.id === "podcast" ? "podcasts" : modelData.id) + ".svg") : modelData.icon
                        isMask: plain
                        color: Kirigami.Theme.textColor
                        implicitWidth: Kirigami.Units.iconSizes.smallMedium
                        implicitHeight: Kirigami.Units.iconSizes.smallMedium
                    }
                    QQC2.Label { text: modelData.name; Layout.fillWidth: true; elide: Text.ElideRight }
                    QQC2.Label {
                        text: modelData.count > 0 && modelData.id !== "podcast" ? modelData.count : ""
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
        QQC2.ItemDelegate {
            Layout.fillWidth: true
            highlighted: root.ytmusicActive
            onClicked: {
                root.showYtMusic()
                if (drawer.modal) drawer.close()
            }
            contentItem: RowLayout {
                spacing: Kirigami.Units.largeSpacing
                Kirigami.Icon {
                    source: Qt.resolvedUrl("ytmusic.svg")
                    isMask: true
                    color: Kirigami.Theme.textColor
                    implicitWidth: Kirigami.Units.iconSizes.smallMedium
                    implicitHeight: Kirigami.Units.iconSizes.smallMedium
                }
                QQC2.Label { text: "YouTube Music"; Layout.fillWidth: true; elide: Text.ElideRight }
            }
        }
        QQC2.ItemDelegate {
            Layout.fillWidth: true
            highlighted: root.shortsActive
            onClicked: {
                root.showShorts()
                if (drawer.modal) drawer.close()
            }
            contentItem: RowLayout {
                spacing: Kirigami.Units.largeSpacing
                Kirigami.Icon {
                    source: Qt.resolvedUrl("shorts.svg")
                    isMask: true
                    color: Kirigami.Theme.textColor
                    implicitWidth: Kirigami.Units.iconSizes.smallMedium
                    implicitHeight: Kirigami.Units.iconSizes.smallMedium
                }
                QQC2.Label { text: "Shorts"; Layout.fillWidth: true; elide: Text.ElideRight }
            }
        }
        QQC2.ItemDelegate {
            Layout.fillWidth: true
            highlighted: root.twitchActive
            onClicked: {
                root.showTwitch()
                if (drawer.modal) drawer.close()
            }
            contentItem: RowLayout {
                spacing: Kirigami.Units.largeSpacing
                Kirigami.Icon {
                    source: Qt.resolvedUrl("twitch.svg")
                    isMask: true
                    color: Kirigami.Theme.textColor
                    implicitWidth: Kirigami.Units.iconSizes.smallMedium
                    implicitHeight: Kirigami.Units.iconSizes.smallMedium
                }
                QQC2.Label { text: "Twitch"; Layout.fillWidth: true; elide: Text.ElideRight }
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
            podEngine.pause()
            musicEngine.stop()
            liveEngine.stop()
            if (pageStack.depth > 1) pageStack.pop()
            pageStack.push(Qt.resolvedUrl("PlayerPage.qml"), { itemId: id, music: musicEngine, live: liveEngine })
        }
    }

    Connections {
        target: marquee
        function onToast(text) { root.showPassiveNotification(text) }
        function onSubToast(text) {
            var page = pageStack.currentItem
            if (!page || page.subBadge === undefined) root.showPassiveNotification(text)
        }
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
        function saveSubs() { marquee.saveSubtitleSettings(keyField.text, userField.text, passField.text, langField.text, autoBox.checked, subdlField.text) }
        onAboutToHide: saveSubs()
        readonly property var styles: [["bars", "Bars"], ["mirror", "Mirror"], ["wave", "Wave"], ["dots", "Dots"], ["scope", "Scope"], ["off", "Off"]]
        onAboutToShow: {
            var cur = marquee.vizStyle()
            vizBox.currentIndex = Math.max(0, styles.findIndex(function (x) { return x[0] === cur }))
            lyricsBox.checked = marquee.lyricsDefault()
            translucentBox.checked = marquee.translucent
            liveAutoBox.checked = marquee.liveAutoplay()
            syncBox.checked = marquee.autoSyncSubtitles()
            var c = marquee.subtitleSettings()
            keyField.text = c.apiKey; userField.text = c.username; passField.text = c.password
            langField.text = c.language; autoBox.checked = c.auto; subdlField.text = c.subdlKey
            sponsorMaster.checked = marquee.sponsorBlockOn()
            sponsorList.model = marquee.sponsorCategories()
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
                Kirigami.Separator { Kirigami.FormData.isSection: true; Kirigami.FormData.label: "Updates" }
                ColumnLayout {
                    Kirigami.FormData.label: "Marquee " + updater.version
                    Layout.maximumWidth: Kirigami.Units.gridUnit * 22
                    spacing: Kirigami.Units.smallSpacing
                    QQC2.Label {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        text: updater.state === "checking" ? "Checking for a newer version…"
                            : updater.state === "uptodate" ? "You have the latest version."
                            : updater.state === "available" ? "Version " + updater.latest + " is available."
                            : updater.state === "downloading" ? "Downloading version " + updater.latest + "…"
                            : updater.state === "ready" ? "Version " + updater.latest + " is installed. Restart Marquee to use it."
                            : updater.state === "error" ? updater.message
                            : "Check whether a newer version has been released."
                    }
                    QQC2.ProgressBar { Layout.fillWidth: true; visible: updater.state === "downloading"; from: 0; to: 1; value: updater.progress }
                    QQC2.ScrollView {
                        Layout.fillWidth: true
                        Layout.preferredHeight: Kirigami.Units.gridUnit * 7
                        visible: updater.state === "available" && updater.notes.length > 0
                        QQC2.TextArea { readOnly: true; wrapMode: Text.Wrap; text: updater.notes; font: Kirigami.Theme.smallFont; background: null }
                    }
                    QQC2.Label {
                        Layout.fillWidth: true
                        visible: updater.state === "available" && !updater.canInstall
                        wrapMode: Text.Wrap
                        opacity: 0.7
                        font: Kirigami.Theme.smallFont
                        text: "This copy of Marquee is not an AppImage, so it can't replace itself. Open the release page to get the new version, or update it the way you installed it."
                    }
                    RowLayout {
                        spacing: Kirigami.Units.smallSpacing
                        QQC2.Button {
                            text: "Check for Updates"
                            visible: updater.state !== "available" && updater.state !== "ready" && updater.state !== "downloading"
                            enabled: updater.state !== "checking"
                            onClicked: updater.check()
                        }
                        QQC2.Button {
                            text: "Update Now"
                            highlighted: true
                            visible: updater.state === "available" && updater.canInstall
                            onClicked: updater.install()
                        }
                        QQC2.Button {
                            text: "Open Release Page"
                            visible: updater.state === "available" && !updater.canInstall
                            highlighted: visible
                            onClicked: updater.openPage()
                        }
                        QQC2.Button { text: "Restart Now"; highlighted: true; visible: updater.state === "ready"; onClicked: updater.restart() }
                    }
                }
                QQC2.CheckBox {
                    text: "Check for updates when Marquee starts"
                    checked: updater.autoCheck
                    onToggled: updater.setAutoCheck(checked)
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
                Kirigami.Separator { Kirigami.FormData.isSection: true; Kirigami.FormData.label: "SponsorBlock" }
                QQC2.CheckBox {
                    id: sponsorMaster
                    text: "Skip marked parts of YouTube videos"
                    onToggled: marquee.setSponsorBlock(checked)
                }
                QQC2.Label {
                    Layout.maximumWidth: Kirigami.Units.gridUnit * 22
                    wrapMode: Text.Wrap
                    opacity: 0.7
                    font: Kirigami.Theme.smallFont
                    text: "Parts are marked by the SponsorBlock community. Only a short code made from the video's ID is sent to look them up. Pick what to skip:"
                }
                ColumnLayout {
                    enabled: sponsorMaster.checked
                    Repeater {
                        id: sponsorList
                        delegate: QQC2.CheckBox {
                            required property var modelData
                            text: modelData[1]
                            checked: modelData[2]
                            onToggled: marquee.setSponsorCategory(modelData[0], checked)
                        }
                    }
                }
                Kirigami.Separator { Kirigami.FormData.isSection: true; Kirigami.FormData.label: "Subtitles" }
                QQC2.Label {
                    Layout.maximumWidth: Kirigami.Units.gridUnit * 22
                    wrapMode: Text.Wrap
                    opacity: 0.7
                    font: Kirigami.Theme.smallFont
                    text: "Marquee downloads subtitles from OpenSubtitles.com: make a free account there, then create an API key under Profile › API consumers. If OpenSubtitles has nothing, or its daily limit is reached, it can try Subdl as a backup: make a free account at subdl.com and paste its API key below. Keys are saved only on this computer."
                }
                QQC2.TextField { id: keyField; Kirigami.FormData.label: "OpenSubtitles API key:"; Layout.minimumWidth: Kirigami.Units.gridUnit * 16; onEditingFinished: settings.saveSubs() }
                QQC2.TextField { id: userField; Kirigami.FormData.label: "OpenSubtitles username:"; onEditingFinished: settings.saveSubs() }
                QQC2.TextField { id: passField; Kirigami.FormData.label: "OpenSubtitles password:"; echoMode: TextInput.Password; onEditingFinished: settings.saveSubs() }
                QQC2.TextField { id: subdlField; Kirigami.FormData.label: "Subdl API key (backup):"; placeholderText: "optional"; onEditingFinished: settings.saveSubs() }
                QQC2.TextField { id: langField; Kirigami.FormData.label: "Language code:"; placeholderText: "en"; onEditingFinished: settings.saveSubs() }
                QQC2.CheckBox { id: autoBox; text: "Download automatically when a video has none"; onToggled: settings.saveSubs() }
                QQC2.CheckBox {
                    id: syncBox
                    text: "Line downloaded subtitles up with the speech"
                    onToggled: marquee.setAutoSyncSubtitles(checked)
                }
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
