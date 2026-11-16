import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import QtQuick.Dialogs
import QtMultimedia
import org.kde.kirigami as Kirigami

// Live TV, laid out like a set-top box: the selected channel on top (what is on, what is next, and a live preview),
// the scrolling TV guide below. Click a channel or programme to preview it; click the preview, or double-click a
// channel, to watch full size.
Kirigami.Page {
    id: page

    property var music                       // the window's music player, so its bar shows here too
    property var groups: []                  // [{key, label, count}]
    property string groupKey: "__all__"
    property var channels: []
    property var playlists: []
    property bool ready: false

    // selection
    property string selId: ""                // "live:<id>" of the highlighted channel
    property var sel: ({})                   // its row from liveChannel()
    property var selNN: ({})                 // {now, next}
    property var selProg: null               // a programme picked in the guide; null = whatever is on now
    property bool previewOn: false
    readonly property var shown: selProg ? selProg : (selNN.now && selNN.now.title ? selNN.now : null)
    readonly property bool shownOnAir: shown !== null && shown.start <= now && shown.end > now

    // Guide time axis: seconds since the epoch at the left edge, pixels per minute, and how far we are scrolled.
    property real gStart: 0
    property real now: marquee.nowSeconds()
    readonly property real ppm: Kirigami.Units.gridUnit * 0.42
    readonly property real hours: 36
    readonly property real gEnd: gStart + hours * 3600
    readonly property real totalW: hours * 60 * ppm
    readonly property real namesW: Kirigami.Units.gridUnit * (page.width > Kirigami.Units.gridUnit * 40 ? 10.5 : 7)
    readonly property real viewW: Math.max(1, guideArea.width - namesW)
    property real scrollX: 0
    readonly property real maxScroll: Math.max(0, totalW - viewW)
    readonly property real rowH: Kirigami.Units.gridUnit * 3.4
    readonly property real heroH: page.height < Kirigami.Units.gridUnit * 28 ? 0 : Math.max(Kirigami.Units.gridUnit * 12, Math.min(page.height * 0.42, Kirigami.Units.gridUnit * 20))
    readonly property color ink: Kirigami.Theme.textColor
    function inkA(a) { return Qt.rgba(ink.r, ink.g, ink.b, a) }

    background: Rectangle { color: applicationWindow().pane(0.78) }
    title: "Live TV"
    padding: 0

    // ---- time ---------------------------------------------------------------------------------------------
    function clampScroll(x) { return Math.max(0, Math.min(maxScroll, x)) }
    function scrollBy(dx) { scrollX = clampScroll(scrollX + dx) }
    function scrollToTime(t) { now = marquee.nowSeconds(); scrollX = clampScroll((t - gStart) / 60 * ppm - ppm * 12) }
    function scrollToNow() { scrollToTime(marquee.nowSeconds() - 1500) }
    function scrollToTonight() {
        var d = new Date(); d.setHours(20, 0, 0, 0)
        if (d.getTime() / 1000 < marquee.nowSeconds() + 1800) d.setDate(d.getDate() + 1)
        scrollToTime(d.getTime() / 1000)
    }
    function scrollToTomorrowMorning() {
        var d = new Date(); d.setDate(d.getDate() + 1); d.setHours(7, 0, 0, 0)
        scrollToTime(d.getTime() / 1000)
    }

    // ---- data ---------------------------------------------------------------------------------------------
    function reloadGroups() {
        groups = marquee.liveGroups()
        if (groups.findIndex(function (g) { return g.key === groupKey }) < 0) groupKey = "__all__"
    }
    // keep: stay where you were scrolled (used when coming back from watching, to refresh what is on now)
    function reload(keep) {
        var b = guideList.contentY
        now = marquee.nowSeconds()
        gStart = Math.floor(now / 1800) * 1800 - 3600
        channels = marquee.liveChannels(groupKey, search.text)
        if (keep === true) Qt.callLater(function () { guideList.contentY = b })
    }
    function updateSel() {
        if (selId === "") { sel = ({}); selNN = ({}); return }
        sel = marquee.liveChannel(selId)
        selNN = marquee.liveNowNext(selId)
    }
    function refreshAll() { reloadGroups(); reload(); updateSel(); playlists = marquee.livePlaylists() }

    // ---- selecting and previewing -----------------------------------------------------------------------------
    function select(id, play) {
        if (id !== selId) selProg = null
        selId = id
        if (play) marquee.setLiveLast(id)
        previewOn = play
        updateSel()
        syncPip()
    }
    function preview(id) { select(id, true) }
    function selectProgramme(row, p) {
        if (selId !== row.id) select(row.id, true)
        selProg = p
    }
    function stepChannel(d) {
        if (channels.length === 0) return
        var i = channels.findIndex(function (c) { return c.id === selId })
        var n = channels[((i < 0 ? 0 : i + d) + channels.length) % channels.length]
        select(n.id, true)
        guideList.positionViewAtIndex(Math.max(0, channels.indexOf(n)), ListView.Contain)
    }
    readonly property var live: applicationWindow().live      // the one live player; its picture is shown here or full size
    // Watching full size happens right here: the same picture grows to fill the page. (A separate player page would
    // need the stream moved between two video outputs, and Qt freezes the picture when that is done.)
    property bool expanded: false
    property bool fs: false
    property bool controlsUp: true
    function tune(id) { select(id, true); expand() }
    function expand() {
        if (selId === "") return
        marquee.setLiveLast(selId)
        previewOn = true
        syncPip()
        expanded = true
        poke()
    }
    function collapse() {
        if (applicationWindow().visibility === Window.FullScreen) applicationWindow().showNormal()
        expanded = false
    }
    function toggleFs() { applicationWindow().visibility === Window.FullScreen ? applicationWindow().showNormal() : applicationWindow().showFullScreen() }
    function poke() { controlsUp = true; hideTimer.restart() }
    property real lastX: -100
    property real lastY: -100
    function moved(p) {    // only a real movement counts: controls hiding makes Qt report the pointer "moving"
        if (Math.abs(p.x - lastX) + Math.abs(p.y - lastY) > 4) { lastX = p.x; lastY = p.y; poke() }
    }
    Timer {
        id: hideTimer
        interval: 3000
        onTriggered: { if (page.expanded && page.live.player.playbackState === MediaPlayer.PlayingState) page.controlsUp = false }
    }
    Connections {
        target: page.Window.window
        ignoreUnknownSignals: true
        function onVisibilityChanged() { page.fs = page.Window.window.visibility === Window.FullScreen }
    }
    globalToolBarStyle: expanded && fs ? Kirigami.ApplicationHeaderStyle.None : Kirigami.ApplicationHeaderStyle.Auto
    function syncPip() {
        if (!ready) return
        if (previewOn && selId !== "") {
            live.attach(vo)
            live.play(selId)                            // the channel already playing is left alone
        } else {
            live.stop()
        }
    }
    // First open: carry on with the channel you last watched (or the first one), playing if auto play is on.
    function startChannel() {
        if (channels.length === 0) return
        var auto = marquee.liveAutoplay()
        var last = marquee.liveLast()
        var id = channels.some(function (c) { return c.id === last }) ? last : channels[0].id
        select(id, auto)
    }
    function showProgramme() {
        var p = shown
        if (!p) return
        progDialog.channelId = selId
        progDialog.channelName = sel.name || ""
        progDialog.prog = p
        progDialog.open()
    }

    Component.onDestruction: {
        try { live.release(vo) } catch (e) {}
        try { if (page.Window.window && page.Window.window.visibility === Window.FullScreen) page.Window.window.showNormal() } catch (e) {}
    }
    Component.onCompleted: {
        refreshAll()
        var back = applicationWindow().pipId           // came back from watching: carry on with that channel
        if (back !== "") { applicationWindow().pipId = ""; select(back, true) }
        else startChannel()
        scrollToNow()
        ready = true
        syncPip()
    }
    onVisibleChanged: {
        if (!ready) return
        if (visible) {
            var back = applicationWindow().pipId
            if (back !== "") { applicationWindow().pipId = ""; select(back, true) }
            if (channels.length > 0) reload(true)
            updateSel()
        }
        syncPip()
    }
    Connections {
        target: marquee
        function onLiveChanged() {
            if (marquee.liveBusy) return
            page.refreshAll()
            if (page.selId === "") page.startChannel()
        }
    }
    Timer { interval: 30000; repeat: true; running: page.visible; onTriggered: { page.now = marquee.nowSeconds(); page.updateSel() } }
    Timer { id: searchDelay; interval: 200; onTriggered: page.reload() }

    actions: [
        Kirigami.Action {
            text: "Playlists…"
            icon.name: "list-add"
            onTriggered: playlistDialog.open()
        },
        Kirigami.Action {
            text: "Refresh Channels and Guide"
            icon.name: "view-refresh"
            enabled: !marquee.liveBusy && marquee.livePlaylistCount > 0
            onTriggered: marquee.refreshLive(true)
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
        visible: !page.expanded
        height: visible ? implicitHeight : 0
        background: Rectangle { color: applicationWindow().pane(0.6) }
        contentItem: RowLayout {
            spacing: Kirigami.Units.largeSpacing
            Kirigami.SearchField {
                id: search
                Layout.fillWidth: true
                Layout.maximumWidth: Kirigami.Units.gridUnit * 18
                placeholderText: "Search channels"
                onTextChanged: searchDelay.restart()
            }
            QQC2.BusyIndicator {
                running: marquee.liveBusy
                visible: running
                implicitHeight: Kirigami.Units.iconSizes.smallMedium
                implicitWidth: implicitHeight
            }
            Item { Layout.fillWidth: true }
        }
    }

    footer: ColumnLayout {
        visible: !page.expanded
        height: visible ? implicitHeight : 0
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
                QQC2.Label { text: marquee.liveStatus; opacity: 0.7; elide: Text.ElideRight; Layout.fillWidth: true }
                QQC2.ToolButton { text: "Playlists…"; flat: true; onClicked: playlistDialog.open() }
            }
        }
    }

    // ---- nothing added yet ----------------------------------------------------------------------------------
    Kirigami.PlaceholderMessage {
        anchors.centerIn: parent
        width: Math.min(parent.width - Kirigami.Units.gridUnit * 4, Kirigami.Units.gridUnit * 30)
        visible: marquee.livePlaylistCount === 0
        icon.name: "video-television"
        text: "No channels yet"
        explanation: "Add an M3U playlist from your IPTV provider, and optionally an XMLTV link to get the TV guide."
        helpfulAction: Kirigami.Action { text: "Add a Playlist…"; icon.name: "list-add"; onTriggered: playlistDialog.open() }
    }
    Kirigami.PlaceholderMessage {
        anchors.centerIn: parent
        width: Math.min(parent.width - Kirigami.Units.gridUnit * 4, Kirigami.Units.gridUnit * 30)
        visible: marquee.livePlaylistCount > 0 && page.channels.length === 0
        icon.name: marquee.liveBusy ? "view-refresh" : "edit-find"
        text: marquee.liveBusy ? "Loading channels…" : (search.text !== "" ? "No matching channels" : "No channels in this list")
        explanation: marquee.liveStatus
    }

    // ---- small pieces ------------------------------------------------------------------------------------
    component Logo: Item {
        property string logo
        property string label
        property real side: Kirigami.Units.gridUnit * 2
        implicitWidth: side
        implicitHeight: side
        Layout.preferredWidth: side
        Layout.preferredHeight: side
        Image {
            id: img
            anchors.fill: parent
            source: parent.logo
            asynchronous: true
            cache: true
            fillMode: Image.PreserveAspectFit
            sourceSize.width: 160
            sourceSize.height: 160
            visible: status === Image.Ready
        }
        Kirigami.Icon {
            anchors.centerIn: parent
            width: parent.width * 0.7; height: width
            source: "video-television"
            opacity: 0.5
            visible: img.status !== Image.Ready && parent.label === ""
        }
        QQC2.Label {
            anchors.fill: parent
            visible: img.status !== Image.Ready && parent.label !== ""
            text: parent.label
            wrapMode: Text.Wrap
            maximumLineCount: 2
            elide: Text.ElideRight
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            font: Kirigami.Theme.smallFont
        }
    }
    component RoundBtn: QQC2.RoundButton {
        property string tip
        display: QQC2.AbstractButton.IconOnly
        flat: false
        implicitWidth: Kirigami.Units.gridUnit * 2.6
        implicitHeight: implicitWidth
        QQC2.ToolTip.text: tip
        QQC2.ToolTip.visible: hovered && tip !== ""
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        visible: page.channels.length > 0

        // ======================= top: the selected channel ===========================================
        Item {
            id: hero
            Layout.fillWidth: true
            Layout.preferredHeight: page.heroH
            visible: page.heroH > 0
            clip: true

            Rectangle {                                  // a wash of the accent colour behind the selected channel
                anchors.fill: parent
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0; color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.22) }
                    GradientStop { position: 0.7; color: "transparent" }
                }
            }

            // where the preview sits; the picture itself is `stage`, further down
            Rectangle {
                id: pipBox
                readonly property real h0: hero.height - Kirigami.Units.largeSpacing * 2
                width: Math.min(hero.width * 0.5, h0 * 16 / 9)
                height: width * 9 / 16
                anchors { right: parent.right; rightMargin: Kirigami.Units.largeSpacing * 2; verticalCenter: parent.verticalCenter }
                color: "black"
            }

            // left side: number and logo at the top, the programme at the bottom
            RowLayout {
                id: heroTop
                anchors { left: parent.left; top: parent.top; margins: Kirigami.Units.largeSpacing * 2 }
                spacing: Kirigami.Units.largeSpacing
                Text { text: page.sel.num ? page.sel.num : ""; color: page.ink; font.pixelSize: Kirigami.Units.gridUnit * 2.6; font.weight: Font.Bold }
                Logo { logo: page.sel.logo || ""; label: page.sel.name || ""; side: Kirigami.Units.gridUnit * 3.2 }
            }
            ColumnLayout {
                anchors {
                    left: parent.left; right: pipBox.left; bottom: parent.bottom
                    leftMargin: Kirigami.Units.largeSpacing * 2; rightMargin: Kirigami.Units.largeSpacing * 2; bottomMargin: Kirigami.Units.largeSpacing * 1.5
                }
                spacing: Kirigami.Units.smallSpacing
                Text {
                    Layout.fillWidth: true
                    text: page.shown ? page.shown.title : (page.sel.name || "")
                    color: page.ink
                    font.pixelSize: Math.min(Kirigami.Units.gridUnit * 2.8, Math.max(Kirigami.Units.gridUnit * 1.6, hero.width * 0.035))
                    font.weight: Font.Bold
                    wrapMode: Text.Wrap
                    maximumLineCount: 2
                    elide: Text.ElideRight
                }
                RowLayout {
                    visible: page.shown !== null
                    spacing: Kirigami.Units.largeSpacing
                    QQC2.Label { text: page.shown ? marquee.clockText(page.shown.start) + " to " + marquee.clockText(page.shown.end) : ""; font.weight: Font.DemiBold }
                    Rectangle {
                        visible: page.shownOnAir
                        Layout.preferredWidth: Kirigami.Units.gridUnit * 10; height: 4; radius: 2; color: page.inkA(0.18)
                        Rectangle {
                            height: parent.height; radius: 2; color: page.ink
                            width: parent.width * (page.shown ? Math.max(0, Math.min(1, (page.now - page.shown.start) / Math.max(1, page.shown.end - page.shown.start))) : 0)
                        }
                    }
                    QQC2.Label { visible: page.shownOnAir; opacity: 0.7; text: page.shown ? Math.max(0, Math.ceil((page.shown.end - page.now) / 60)) + "m left" : "" }
                }
                QQC2.Label {
                    Layout.fillWidth: true
                    visible: text !== ""
                    opacity: 0.7
                    elide: Text.ElideRight
                    maximumLineCount: 1
                    text: {
                        if (!page.selNN.next || !page.selNN.next.title) return page.shown && page.shown.desc ? page.shown.desc : ""
                        if (page.shownOnAir && !page.selProg) return "Next  " + marquee.clockText(page.selNN.next.start) + "  " + page.selNN.next.title
                        return page.shown && page.shown.desc ? page.shown.desc : ""
                    }
                }
                RowLayout {
                    spacing: Kirigami.Units.largeSpacing
                    Layout.topMargin: Kirigami.Units.smallSpacing
                    RoundBtn { icon.name: page.sel.fav ? "starred-symbolic" : "non-starred-symbolic"; tip: page.sel.fav ? "Remove from Favourites" : "Add to Favourites"; onClicked: marquee.toggleLiveFavourite(page.selId) }
                    RoundBtn { icon.name: "go-down"; tip: "Previous channel"; onClicked: page.stepChannel(-1) }
                    RoundBtn { icon.name: "go-up"; tip: "Next channel"; onClicked: page.stepChannel(1) }
                    RoundBtn { icon.name: "help-about"; tip: "About this programme"; enabled: page.shown !== null; onClicked: page.showProgramme() }
                    RoundBtn { icon.name: "view-fullscreen"; tip: "Watch full size"; onClicked: page.tune(page.selId) }
                }
            }
        }

        // ======================= bottom: the guide ====================================================
        ColumnLayout {
            id: guideArea
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            // header: title, group pills, time shortcuts
            Rectangle {
                Layout.fillWidth: true
                Layout.leftMargin: Kirigami.Units.largeSpacing
                Layout.rightMargin: Kirigami.Units.largeSpacing
                Layout.topMargin: Kirigami.Units.smallSpacing
                implicitHeight: Kirigami.Units.gridUnit * 3
                radius: height / 2
                color: page.inkA(0.07)
                RowLayout {
                    anchors { fill: parent; leftMargin: Kirigami.Units.largeSpacing * 1.5; rightMargin: Kirigami.Units.smallSpacing }
                    spacing: Kirigami.Units.largeSpacing
                    Kirigami.Heading { level: 2; text: "Guide"; font.weight: Font.Bold }
                    ListView {
                        id: pills
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        orientation: ListView.Horizontal
                        clip: true
                        spacing: Kirigami.Units.smallSpacing
                        model: page.groups
                        boundsBehavior: Flickable.StopAtBounds
                        WheelHandler { onWheel: event => pills.flick(event.angleDelta.y * 8, 0) }
                        delegate: Rectangle {
                            id: pill
                            required property var modelData
                            readonly property bool on: page.groupKey === modelData.key
                            y: (ListView.view.height - height) / 2
                            height: Kirigami.Units.gridUnit * 2
                            width: pillText.implicitWidth + Kirigami.Units.gridUnit * 1.6
                            radius: height / 2
                            color: on ? page.ink : (pillHover.hovered ? page.inkA(0.12) : "transparent")
                            QQC2.Label { id: pillText; anchors.centerIn: parent; text: pill.modelData.label; color: pill.on ? Kirigami.Theme.backgroundColor : page.ink; font.weight: Font.DemiBold }
                            HoverHandler { id: pillHover }
                            TapHandler { onTapped: { page.groupKey = pill.modelData.key; page.reload() } }
                            QQC2.ToolTip.text: pill.modelData.count + " channels"
                            QQC2.ToolTip.visible: pillHover.hovered
                            QQC2.ToolTip.delay: 700
                        }
                    }
                    RoundBtn { icon.name: "go-previous"; tip: "Earlier"; implicitWidth: Kirigami.Units.gridUnit * 2; onClicked: page.scrollBy(-180 * page.ppm) }
                    QQC2.Button { text: "Now"; font.weight: Font.DemiBold; onClicked: page.scrollToNow() }
                    RoundBtn { icon.name: "go-next"; tip: "Later"; implicitWidth: Kirigami.Units.gridUnit * 2; onClicked: page.scrollBy(180 * page.ppm) }
                    QQC2.Button { flat: true; text: "Tonight 8 PM"; visible: page.width > Kirigami.Units.gridUnit * 46; onClicked: page.scrollToTonight() }
                    QQC2.Button { flat: true; text: "Tomorrow morning"; visible: page.width > Kirigami.Units.gridUnit * 58; onClicked: page.scrollToTomorrowMorning() }
                }
            }

            // time ruler
            Item {
                Layout.fillWidth: true
                implicitHeight: Kirigami.Units.gridUnit * 2.2
                QQC2.Label {
                    anchors { left: parent.left; leftMargin: Kirigami.Units.largeSpacing * 1.5; verticalCenter: parent.verticalCenter }
                    text: "Today  " + marquee.clockText(page.gStart + page.scrollX / page.ppm * 60)
                    font.weight: Font.DemiBold
                    opacity: 0.8
                }
                Item {
                    anchors { fill: parent; leftMargin: page.namesW }
                    clip: true
                    Repeater {
                        model: page.hours * 2
                        delegate: Item {
                            id: tick
                            required property int index
                            readonly property real t: page.gStart + index * 1800
                            x: index * 30 * page.ppm - page.scrollX
                            width: 30 * page.ppm
                            height: parent.height
                            visible: x + width > 0 && x < page.viewW
                            Rectangle { width: 1; height: parent.height * 0.6; anchors.verticalCenter: parent.verticalCenter; color: page.inkA(0.3) }
                            QQC2.Label {
                                anchors { left: parent.left; leftMargin: 8; verticalCenter: parent.verticalCenter }
                                text: marquee.clockText(tick.t)
                                font.weight: Font.DemiBold
                                opacity: 0.8
                            }
                        }
                    }
                    Rectangle {                          // "now" tag on the ruler
                        readonly property real lx: (page.now - page.gStart) / 60 * page.ppm - page.scrollX
                        visible: lx > -20 && lx < page.viewW + 20
                        x: lx - width / 2
                        anchors.verticalCenter: parent.verticalCenter
                        width: nowTag.implicitWidth + Kirigami.Units.gridUnit
                        height: Kirigami.Units.gridUnit * 1.5
                        radius: height / 2
                        color: Kirigami.Theme.highlightColor
                        z: 2
                        QQC2.Label { id: nowTag; anchors.centerIn: parent; text: marquee.clockText(page.now); color: Kirigami.Theme.highlightedTextColor; font.weight: Font.Bold }
                    }
                }
            }

            // rows
            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true

                ListView {
                    id: guideList
                    anchors.fill: parent
                    clip: true
                    model: page.channels
                    spacing: 4
                    boundsBehavior: Flickable.StopAtBounds
                    QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
                    // Shift+wheel moves through time; the plain wheel scrolls the channels.
                    WheelHandler {
                        acceptedModifiers: Qt.ShiftModifier
                        onWheel: event => page.scrollBy(-(event.angleDelta.y !== 0 ? event.angleDelta.y : event.angleDelta.x) * 1.2)
                    }
                    delegate: Item {
                        id: row
                        required property var modelData
                        required property int index
                        width: ListView.view.width
                        height: page.rowH
                        readonly property var progs: marquee.liveProgrammes(modelData.cid, page.gStart, page.gEnd)
                        readonly property bool picked: page.selId === modelData.id

                        // channel card
                        Rectangle {
                            id: nameCell
                            x: Kirigami.Units.largeSpacing
                            width: page.namesW - Kirigami.Units.largeSpacing * 1.5
                            height: parent.height
                            radius: Kirigami.Units.largeSpacing
                            z: 3
                            color: row.picked ? page.ink : (nameHover.hovered ? page.inkA(0.16) : page.inkA(0.09))
                            HoverHandler { id: nameHover; cursorShape: Qt.PointingHandCursor }
                            TapHandler {
                                onTapped: page.select(row.modelData.id, true)
                                onDoubleTapped: page.tune(row.modelData.id)
                            }
                            RowLayout {
                                anchors { fill: parent; leftMargin: Kirigami.Units.largeSpacing; rightMargin: Kirigami.Units.smallSpacing }
                                spacing: Kirigami.Units.smallSpacing
                                Text {
                                    text: row.modelData.num ? row.modelData.num : ""
                                    color: row.picked ? Kirigami.Theme.backgroundColor : page.ink
                                    font.pixelSize: Kirigami.Units.gridUnit * 1.15
                                    font.weight: Font.Bold
                                    Layout.minimumWidth: Kirigami.Units.gridUnit * 2.2
                                }
                                Logo { logo: row.modelData.logo; label: row.modelData.name; side: Kirigami.Units.gridUnit * 2.4 }
                                Item { Layout.fillWidth: true }
                            }
                            QQC2.ToolTip.text: row.modelData.name + "  (double-click to watch)"
                            QQC2.ToolTip.visible: nameHover.hovered
                            QQC2.ToolTip.delay: 700
                        }
                        // programmes
                        Item {
                            anchors { left: nameCell.right; leftMargin: Kirigami.Units.smallSpacing; right: parent.right; top: parent.top; bottom: parent.bottom }
                            clip: true
                            QQC2.Label {
                                visible: row.progs.length === 0
                                anchors { left: parent.left; leftMargin: Kirigami.Units.largeSpacing; verticalCenter: parent.verticalCenter }
                                text: row.modelData.now !== "" ? row.modelData.now : "No guide data"
                                opacity: 0.5
                            }
                            Repeater {
                                model: row.progs
                                delegate: Rectangle {
                                    id: block
                                    required property var modelData
                                    readonly property real s: Math.max(modelData.start, page.gStart)
                                    readonly property real e: Math.min(modelData.end, page.gEnd)
                                    readonly property bool onAir: modelData.start <= page.now && modelData.end > page.now
                                    readonly property bool chosen: row.picked && page.selProg !== null && page.selProg.start === modelData.start
                                    x: (s - page.gStart) / 60 * page.ppm - page.scrollX + 1
                                    width: Math.max(2, (e - s) / 60 * page.ppm - 3)
                                    height: parent.height
                                    visible: x + width > 0 && x < page.viewW
                                    radius: Kirigami.Units.largeSpacing
                                    color: onAir
                                        ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, blockHover.hovered ? 0.45 : 0.32)
                                        : page.inkA(blockHover.hovered ? 0.2 : 0.1)
                                    border.width: chosen ? 2 : 0
                                    border.color: page.ink
                                    opacity: modelData.end <= page.now ? 0.55 : 1
                                    HoverHandler { id: blockHover; cursorShape: Qt.PointingHandCursor }
                                    TapHandler {
                                        onTapped: page.selectProgramme(row.modelData, block.modelData)
                                        onDoubleTapped: page.tune(row.modelData.id)
                                    }
                                    ColumnLayout {
                                        // keeps the title in view while the start of a long programme is scrolled off
                                        x: Math.max(0, Math.min(-block.x, block.width - width - 4)) + Kirigami.Units.largeSpacing
                                        width: Math.min(parent.width - Kirigami.Units.largeSpacing * 2, Kirigami.Units.gridUnit * 30)
                                        anchors.verticalCenter: parent.verticalCenter
                                        spacing: 0
                                        QQC2.Label { Layout.fillWidth: true; text: block.modelData.title; elide: Text.ElideRight; font.weight: Font.Bold }
                                        QQC2.Label {
                                            Layout.fillWidth: true
                                            visible: block.width > Kirigami.Units.gridUnit * 5
                                            text: marquee.clockText(block.modelData.start) + (block.modelData.desc ? " · " + block.modelData.desc : "")
                                            opacity: 0.7; elide: Text.ElideRight; font: Kirigami.Theme.smallFont
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                // the "now" line
                Rectangle {
                    readonly property real lx: (page.now - page.gStart) / 60 * page.ppm - page.scrollX
                    visible: lx >= 0 && lx <= page.viewW
                    x: page.namesW + Kirigami.Units.smallSpacing + lx - 1
                    width: 2
                    height: parent.height
                    color: Kirigami.Theme.highlightColor
                    z: 5
                }
            }
        }
    }

    // ---- the picture: one VideoOutput that never moves between players or parents. Collapsed it sits over the preview
    // spot in the top right; expanded it fills the page and the controls appear over it.
    Item {
        id: stage
        z: 30
        visible: page.expanded || (page.previewOn && hero.visible && page.channels.length > 0)
        x: page.expanded ? 0 : pipBox.x + hero.x
        y: page.expanded ? 0 : pipBox.y + hero.y
        width: page.expanded ? page.contentItem.width : pipBox.width
        height: page.expanded ? page.contentItem.height : pipBox.height
        clip: true
        Rectangle { anchors.fill: parent; color: "black" }
        VideoOutput { id: vo; anchors.fill: parent; fillMode: VideoOutput.PreserveAspectFit }

        QQC2.BusyIndicator {
            anchors.centerIn: parent
            running: page.previewOn && page.live.errorText === "" && (page.live.player.mediaStatus === MediaPlayer.LoadingMedia || page.live.player.mediaStatus === MediaPlayer.StalledMedia || page.live.player.mediaStatus === MediaPlayer.BufferingMedia)
            visible: running
        }
        Kirigami.Icon {                                  // not previewing yet: a play button
            anchors.centerIn: parent
            width: Kirigami.Units.gridUnit * 3; height: width
            source: "media-playback-start"
            color: "white"
            visible: !page.previewOn
            opacity: 0.85
        }
        Text {                                           // a failed channel, small version
            visible: !page.expanded && text !== ""
            text: page.previewOn ? page.live.errorText : ""
            anchors.centerIn: parent
            width: parent.width - 16
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            maximumLineCount: 4
            elide: Text.ElideRight
            color: "white"
            font.pixelSize: 12
        }
        TapHandler {
            onTapped: {
                if (page.expanded) page.poke()
                else if (page.previewOn) page.expand()
                else { page.previewOn = true; page.syncPip() }
            }
            onDoubleTapped: if (page.expanded) page.toggleFs()
        }
        HoverHandler {
            id: stageHover
            cursorShape: page.expanded ? (page.controlsUp ? Qt.ArrowCursor : Qt.BlankCursor) : Qt.PointingHandCursor
            onPointChanged: if (page.expanded) page.moved(point.scenePosition)
        }
        QQC2.ToolTip.text: "Watch full size"
        QQC2.ToolTip.visible: !page.expanded && page.previewOn && stageHover.hovered
        QQC2.ToolTip.delay: 600

        // collapsed: channel and programme over the bottom of the picture
        Item {
            anchors.fill: parent
            visible: !page.expanded
            readonly property bool dummy: true
                Rectangle {
                    anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
                    height: overlay.implicitHeight + Kirigami.Units.largeSpacing * 2
                    gradient: Gradient {
                        GradientStop { position: 0; color: "#00000000" }
                        GradientStop { position: 1; color: "#d8000000" }
                    }
                    ColumnLayout {
                        id: overlay
                        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: Kirigami.Units.largeSpacing }
                        spacing: 2
                        RowLayout {
                            spacing: Kirigami.Units.smallSpacing
                            Logo { logo: page.sel.logo || ""; label: ""; side: Kirigami.Units.gridUnit * 1.6; visible: (page.sel.logo || "") !== "" }
                            Text { Layout.fillWidth: true; text: (page.sel.name || "").toUpperCase(); color: "#d0ffffff"; font.pixelSize: 12; font.letterSpacing: 1; elide: Text.ElideRight }
                        }
                        Text { Layout.fillWidth: true; visible: !!page.selNN.now && !!page.selNN.now.title; text: page.selNN.now ? (page.selNN.now.title || "") : ""; color: "white"; font.pixelSize: 15; font.weight: Font.DemiBold; elide: Text.ElideRight }
                        RowLayout {
                            visible: !!page.selNN.now && !!page.selNN.now.title
                            spacing: Kirigami.Units.largeSpacing
                            Text { color: "#d0ffffff"; font.pixelSize: 12; text: page.selNN.now && page.selNN.now.title ? marquee.clockText(page.selNN.now.start) + " to " + marquee.clockText(page.selNN.now.end) : "" }
                            Rectangle {
                                Layout.fillWidth: true; height: 3; radius: 1.5; color: "#40ffffff"
                                Rectangle {
                                    height: parent.height; radius: 1.5; color: Kirigami.Theme.highlightColor
                                    width: parent.width * Math.max(0, Math.min(1, ((page.selNN.now ? (page.now - page.selNN.now.start) : 0) / Math.max(1, page.selNN.now ? (page.selNN.now.end - page.selNN.now.start) : 1))))
                                }
                            }
                        }
                        Text { Layout.fillWidth: true; visible: !!page.selNN.next && !!page.selNN.next.title; text: page.selNN.next && page.selNN.next.title ? "Next " + marquee.clockText(page.selNN.next.start) + " · " + page.selNN.next.title : ""; color: "#b0ffffff"; font.pixelSize: 11; elide: Text.ElideRight }
                    }
                }
        }

        // expanded: what's on, in the top left
        Rectangle {
            id: osdBox
            visible: page.expanded && opacity > 0
            opacity: page.controlsUp ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 250 } }
            anchors { left: parent.left; top: parent.top; margins: Kirigami.Units.largeSpacing * 2 }
            width: Math.min(parent.width - Kirigami.Units.largeSpacing * 4, Kirigami.Units.gridUnit * 26)
            height: osdCol.implicitHeight + Kirigami.Units.largeSpacing * 2
            radius: Kirigami.Units.largeSpacing
            color: "#c8000000"
            readonly property var cur: page.selNN.now || ({})
            readonly property var nxt: page.selNN.next || ({})
            function span(p) { return p.title ? marquee.clockText(p.start) + "–" + marquee.clockText(p.end) : "" }
            ColumnLayout {
                id: osdCol
                anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: Kirigami.Units.largeSpacing }
                spacing: 2
                RowLayout {
                    spacing: Kirigami.Units.smallSpacing
                    Rectangle { width: 8; height: 8; radius: 4; color: "#ff4d4d" }
                    Text { text: "LIVE"; color: "#ff8080"; font.pixelSize: 11; font.weight: Font.Bold; font.letterSpacing: 1 }
                    Text { text: page.sel.num ? page.sel.num : ""; color: "#b0ffffff"; font.pixelSize: 15; font.weight: Font.Bold }
                    Text { Layout.fillWidth: true; text: page.sel.name || ""; color: "white"; font.pixelSize: 17; font.weight: Font.Bold; elide: Text.ElideRight }
                }
                Text { Layout.fillWidth: true; visible: !!osdBox.cur.title; text: osdBox.span(osdBox.cur) + "  " + (osdBox.cur.title || ""); color: "white"; font.pixelSize: 14; elide: Text.ElideRight }
                Rectangle {
                    Layout.fillWidth: true; height: 3; radius: 1.5; color: "#40ffffff"
                    visible: !!osdBox.cur.title
                    Rectangle {
                        height: parent.height; radius: 1.5; color: "#ff8080"
                        width: parent.width * Math.max(0, Math.min(1, (page.now - (osdBox.cur.start || 0)) / Math.max(1, (osdBox.cur.end || 1) - (osdBox.cur.start || 0))))
                    }
                }
                Text { Layout.fillWidth: true; visible: !!osdBox.nxt.title; text: "Next  " + osdBox.span(osdBox.nxt) + "  " + (osdBox.nxt.title || ""); color: "#b0ffffff"; font.pixelSize: 12; elide: Text.ElideRight }
            }
        }
        Kirigami.InlineMessage {
            anchors { left: parent.left; right: parent.right; bottom: bar.top; margins: Kirigami.Units.largeSpacing }
            visible: page.expanded && page.live.errorText !== ""
            type: Kirigami.MessageType.Error
            text: page.live.errorText
            actions: [ Kirigami.Action { text: "Next Channel"; icon.name: "media-skip-forward"; onTriggered: page.stepChannel(1) } ]
        }
        // expanded: controls along the bottom
        QQC2.ToolBar {
            id: bar
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            visible: page.expanded && page.controlsUp
            HoverHandler { onPointChanged: page.moved(point.scenePosition) }
            contentItem: RowLayout {
                spacing: Kirigami.Units.smallSpacing
                QQC2.ToolButton { icon.name: "media-skip-backward"; display: QQC2.AbstractButton.IconOnly; text: "Previous Channel (P)"; onClicked: page.stepChannel(-1); QQC2.ToolTip.text: text; QQC2.ToolTip.visible: hovered }
                QQC2.ToolButton { icon.name: "media-skip-forward"; display: QQC2.AbstractButton.IconOnly; text: "Next Channel (N)"; onClicked: page.stepChannel(1); QQC2.ToolTip.text: text; QQC2.ToolTip.visible: hovered }
                Rectangle { width: 9; height: 9; radius: 4.5; color: "#ff4d4d" }
                QQC2.Label { text: "LIVE"; font.bold: true; color: "#ff8080" }
                QQC2.ToolButton { icon.name: "view-list-details"; text: "Guide"; onClicked: page.collapse(); QQC2.ToolTip.text: "Back to the guide (Esc)"; QQC2.ToolTip.visible: hovered }
                Item { Layout.fillWidth: true }
                QQC2.ToolButton { icon.name: page.live.muted || volSlider.value === 0 ? "player-volume-muted" : "player-volume"; display: QQC2.AbstractButton.IconOnly; text: "Mute (M)"; onClicked: page.live.muted = !page.live.muted }
                QQC2.Slider {
                    id: volSlider
                    from: 0; to: 1
                    value: page.live.volume
                    onMoved: page.live.volume = value
                    implicitWidth: Kirigami.Units.gridUnit * 6
                }
                QQC2.ToolButton { icon.name: page.fs ? "view-restore" : "view-fullscreen"; display: QQC2.AbstractButton.IconOnly; text: "Fullscreen (F)"; onClicked: page.toggleFs() }
            }
        }
    }
    Shortcut { sequences: ["Escape"]; enabled: page.expanded; onActivated: page.collapse() }
    Shortcut { sequences: ["F", "F11"]; enabled: page.expanded; onActivated: page.toggleFs() }
    Shortcut { sequences: ["Up", "PgUp", "N"]; enabled: page.expanded; onActivated: page.stepChannel(1) }
    Shortcut { sequences: ["Down", "PgDown", "P"]; enabled: page.expanded; onActivated: page.stepChannel(-1) }
    Shortcut { sequences: ["M"]; enabled: page.expanded; onActivated: page.live.muted = !page.live.muted }

    // A programme: what it is, when, and a way to watch the channel.
    Kirigami.Dialog {
        id: progDialog
        property string channelId: ""
        property string channelName: ""
        property var prog: ({})
        title: prog.title || ""
        padding: Kirigami.Units.largeSpacing
        standardButtons: Kirigami.Dialog.Close
        customFooterActions: [
            Kirigami.Action {
                text: "Preview in a Small Window"
                icon.name: "window-duplicate"
                onTriggered: { progDialog.close(); page.preview(progDialog.channelId) }
            },
            Kirigami.Action {
                text: "Watch " + progDialog.channelName
                icon.name: "media-playback-start"
                onTriggered: { progDialog.close(); page.tune(progDialog.channelId) }
            }
        ]
        ColumnLayout {
            spacing: Kirigami.Units.smallSpacing
            QQC2.Label {
                text: progDialog.channelName + " · " + (progDialog.prog.start ? marquee.clockText(progDialog.prog.start) + "–" + marquee.clockText(progDialog.prog.end) : "") +
                      (progDialog.prog.cat ? " · " + progDialog.prog.cat : "")
                opacity: 0.75
            }
            QQC2.Label {
                Layout.maximumWidth: Kirigami.Units.gridUnit * 26
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: progDialog.prog.desc || "No description."
                opacity: progDialog.prog.desc ? 1 : 0.5
            }
        }
    }

    FileDialog {
        id: m3uFile
        title: "Choose an M3U Playlist"
        nameFilters: ["Playlists (*.m3u *.m3u8)", "All files (*)"]
        onAccepted: urlField.text = selectedFile.toString()
    }
    FileDialog {
        id: xmlFile
        title: "Choose an XMLTV Guide"
        nameFilters: ["XMLTV (*.xml *.xml.gz *.gz)", "All files (*)"]
        onAccepted: epgField.text = selectedFile.toString()
    }

    Kirigami.Dialog {
        id: playlistDialog
        title: "Live TV Playlists"
        standardButtons: Kirigami.Dialog.Close
        padding: Kirigami.Units.largeSpacing
        onAboutToShow: page.playlists = marquee.livePlaylists()
        ColumnLayout {
            spacing: Kirigami.Units.largeSpacing
            Layout.minimumWidth: Kirigami.Units.gridUnit * 28
            Repeater {
                model: page.playlists
                delegate: RowLayout {
                    id: plRow
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    Kirigami.Icon { source: "video-television"; implicitWidth: Kirigami.Units.iconSizes.smallMedium; implicitHeight: implicitWidth }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        QQC2.Label { text: plRow.modelData.name; font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight }
                        QQC2.Label { text: plRow.modelData.url + (plRow.modelData.epg ? "\nGuide: " + plRow.modelData.epg : ""); opacity: 0.6; font: Kirigami.Theme.smallFont; Layout.fillWidth: true; Layout.maximumWidth: Kirigami.Units.gridUnit * 24; elide: Text.ElideMiddle; maximumLineCount: 2 }
                    }
                    QQC2.ToolButton {
                        icon.name: "edit-delete"
                        display: QQC2.AbstractButton.IconOnly
                        text: "Remove"
                        onClicked: { marquee.removeLivePlaylist(plRow.index); page.playlists = marquee.livePlaylists() }
                        QQC2.ToolTip.text: text
                        QQC2.ToolTip.visible: hovered
                    }
                }
            }
            Kirigami.Separator { Layout.fillWidth: true; visible: page.playlists.length > 0 }
            QQC2.Label { text: "Add a playlist"; font.weight: Font.DemiBold }
            Kirigami.FormLayout {
                Layout.fillWidth: true
                QQC2.TextField { id: nameField; Kirigami.FormData.label: "Name:"; placeholderText: "My TV"; Layout.fillWidth: true }
                RowLayout {
                    Kirigami.FormData.label: "Playlist:"
                    Layout.fillWidth: true
                    QQC2.TextField { id: urlField; Layout.fillWidth: true; placeholderText: "https://… .m3u  or a file" }
                    QQC2.Button { icon.name: "document-open"; text: "Choose…"; onClicked: m3uFile.open() }
                }
                RowLayout {
                    Kirigami.FormData.label: "TV guide:"
                    Layout.fillWidth: true
                    QQC2.TextField { id: epgField; Layout.fillWidth: true; placeholderText: "optional XMLTV link (.xml or .xml.gz)" }
                    QQC2.Button { icon.name: "document-open"; text: "Choose…"; onClicked: xmlFile.open() }
                }
            }
            QQC2.Label {
                Layout.maximumWidth: Kirigami.Units.gridUnit * 28
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                opacity: 0.65
                font: Kirigami.Theme.smallFont
                text: "Many playlists name their own guide, so the guide link can stay empty. Channels are matched to the guide by their tvg-id, then by name."
            }
            RowLayout {
                Layout.fillWidth: true
                QQC2.Label { text: marquee.liveBusy ? marquee.liveStatus : ""; opacity: 0.7; Layout.fillWidth: true; elide: Text.ElideRight }
                QQC2.Button {
                    text: "Add Playlist"
                    icon.name: "list-add"
                    enabled: urlField.text.trim() !== "" && !marquee.liveBusy
                    onClicked: {
                        marquee.addLivePlaylist(nameField.text, urlField.text, epgField.text)
                        nameField.text = ""; urlField.text = ""; epgField.text = ""
                        page.playlists = marquee.livePlaylists()
                    }
                }
            }
        }
    }
}
