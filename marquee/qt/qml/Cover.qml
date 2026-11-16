import QtQuick
import org.kde.kirigami as Kirigami

// Cover art, or a generated placeholder whose hue comes from the title. Text colour on the placeholder is
// fixed white because the gradient behind it is fixed dark, independent of the colour scheme.
Item {
    id: cover
    property string source
    property string title
    property string iconName: "video-x-generic"
    property real ratio: 2 / 3          // width / height
    property real boxSize: Kirigami.Units.gridUnit * 10   // long edge budget: width for portrait, width for wide
    property real progress: 0

    implicitWidth: boxSize
    implicitHeight: boxSize / ratio

    function hue(s) {
        var h = 0
        for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0
        return (h % 360) / 360
    }

    Rectangle {
        anchors.fill: parent
        radius: Kirigami.Units.cornerRadius
        clip: true
        color: Kirigami.Theme.alternateBackgroundColor
        border.width: 1
        border.color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.2)

        Image {
            id: img
            anchors.fill: parent
            source: cover.source
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
            cache: true
            sourceSize.width: cover.width * 2
            visible: status === Image.Ready
        }

        Rectangle {
            anchors.fill: parent
            visible: img.status !== Image.Ready
            gradient: Gradient {
                GradientStop { position: 0; color: Qt.hsla(cover.hue(cover.title), 0.42, 0.36, 1) }
                GradientStop { position: 1; color: Qt.hsla((cover.hue(cover.title) + 0.12) % 1, 0.48, 0.17, 1) }
            }
            Kirigami.Icon {
                source: cover.iconName
                color: "white"
                opacity: 0.75
                width: Math.min(parent.width, parent.height) * 0.22
                height: width
                x: parent.width * 0.1
                y: parent.height * 0.1
            }
            Text {
                anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: parent.width * 0.1 }
                text: cover.title
                color: "white"
                wrapMode: Text.Wrap
                maximumLineCount: 4
                elide: Text.ElideRight
                font.weight: Font.DemiBold
                font.pixelSize: Math.max(10, cover.width * 0.11)
            }
        }

        Rectangle {
            visible: cover.progress > 0
            anchors { left: parent.left; bottom: parent.bottom }
            height: Math.max(3, Kirigami.Units.smallSpacing / 2)
            width: parent.width * cover.progress
            color: Kirigami.Theme.highlightColor
        }
    }
}
