import QtQuick

// One full-width action row in the snippets window's actions sheet.
//
// Deliberately plain: a 40 px bar with a single left-aligned label and no
// icon. The sheet's whole reason for existing is that a floating menu
// anchored to a 165 px tile puts every management action on a target
// smaller than the tile it came from, so the rows here take the full
// width of the window and say what they do in words. An icon at this size
// would be one more thing to interpret, and (see StrokeIcon) one more
// thing the host font could render as a colour smudge.
Rectangle {
    id: row

    property string label: ""
    property color ink: "#e0e0e0"
    property color surface: "#3a3a3a"
    property color hover: "#4a4a4a"
    property color hairline: "#505050"

    signal activated()

    implicitHeight: 40
    radius: 6
    color: rowMa.containsMouse ? row.hover : row.surface
    border.width: 1
    border.color: row.hairline
    // Disabled rows stay in place rather than disappearing, so the sheet
    // never reflows under a pointer already travelling toward a row.
    opacity: row.enabled ? 1.0 : 0.4

    Text {
        anchors.verticalCenter: parent.verticalCenter
        anchors.left: parent.left
        anchors.leftMargin: 12
        anchors.right: parent.right
        anchors.rightMargin: 12
        text: row.label
        // Rows can carry a user-supplied label, and snippets round-trip
        // through the Data Backup import.
        textFormat: Text.PlainText
        color: row.ink
        font.pixelSize: 13
        font.weight: Font.DemiBold
        elide: Text.ElideRight
    }

    MouseArea {
        id: rowMa
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: row.activated()
    }
}
