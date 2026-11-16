import QtQuick
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

// Lyrics that follow the song. With timings (synced) the line being sung is bright and fills from left to right as it
// is sung, the lines around it are dim, and the list scrolls to keep it in view. Without timings it is plain text you
// scroll yourself. Click a line to jump to it.
Item {
    id: lv
    property var rows: []
    property bool synced: false
    property real pos: 0                 // seconds
    property string status: ""           // shown when there are no lines
    signal seekTo(real seconds)

    readonly property int fontPx: Math.round(Math.max(18, Math.min(36, width / 17)))
    property int current: -1
    property bool held: false            // the reader is scrolling by hand: leave the view alone for a moment

    function locate() {
        if (!synced || rows.length === 0) { current = -1; return }
        var lo = 0, hi = rows.length - 1, r = -1
        while (lo <= hi) {
            var mid = (lo + hi) >> 1
            if (rows[mid].t <= pos) { r = mid; lo = mid + 1 } else hi = mid - 1
        }
        current = r
    }
    onPosChanged: locate()
    onRowsChanged: { locate(); list.contentY = -list.height * 0.35 }
    onSyncedChanged: locate()

    // how far through the current line: the sweep takes as long as the words would take to sing, at most the gap
    readonly property real progress: {
        if (current < 0 || current >= rows.length) return 0
        var a = rows[current].t
        var b = current + 1 < rows.length ? rows[current + 1].t : a + 6
        var dur = Math.min(Math.max(0.3, b - a), Math.max(1.2, (rows[current].text || "").length * 0.075))
        return Math.max(0, Math.min(1, (pos - a) / dur))
    }

    function recentre() {
        if (held || current < 0 || current >= list.count) return
        var it = list.itemAtIndex(current)
        if (!it) return
        scroll.to = Math.max(-list.height * 0.35, it.y + it.height / 2 - list.height * 0.4)
        scroll.restart()
    }
    onCurrentChanged: recentre()

    NumberAnimation { id: scroll; target: list; property: "contentY"; duration: 450; easing.type: Easing.OutCubic }
    Timer { id: hold; interval: 3500; onTriggered: { lv.held = false; lv.recentre() } }

    ListView {
        id: list
        anchors.fill: parent
        model: lv.rows
        clip: true
        cacheBuffer: 200000
        spacing: Math.round(lv.fontPx * 0.55)
        topMargin: height * 0.35
        bottomMargin: height * 0.55
        boundsBehavior: Flickable.StopAtBounds
        onMovementStarted: { scroll.stop(); lv.held = true; hold.restart() }
        onMovementEnded: hold.restart()
        QQC2.ScrollBar.vertical: QQC2.ScrollBar { }

        delegate: Item {
            id: row
            required property int index
            required property var modelData
            readonly property bool isCurrent: lv.synced && index === lv.current
            readonly property bool sung: lv.synced && index < lv.current
            width: list.width - Kirigami.Units.largeSpacing * 4
            x: Kirigami.Units.largeSpacing * 2
            height: base.implicitHeight
            readonly property string words: modelData.text !== "" ? modelData.text : "♪"

            Text {
                id: base
                width: parent.width
                text: row.words
                wrapMode: Text.WordWrap
                font.pixelSize: lv.fontPx
                font.weight: Font.Bold
                color: lv.synced ? (row.sung ? "#99ffffff" : "#59ffffff") : "#e6ffffff"
                Behavior on color { ColorAnimation { duration: 300 } }
            }
            // the bright copy, revealed as the line is sung
            Item {
                visible: row.isCurrent
                width: Math.max(0, base.contentWidth * lv.progress)
                height: parent.height
                clip: true
                Text {
                    width: base.width
                    text: row.words
                    wrapMode: Text.WordWrap
                    font: base.font
                    color: "white"
                }
            }
            TapHandler { enabled: lv.synced; onTapped: lv.seekTo(row.modelData.t) }
        }
    }

    QQC2.Label {
        anchors.centerIn: parent
        visible: lv.rows.length === 0 && lv.status !== ""
        text: lv.status
        color: "#b3ffffff"
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        width: parent.width * 0.8
    }
}
