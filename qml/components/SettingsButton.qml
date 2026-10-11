import QtQuick 2.15

// A labelled button sized for an imprecise pointer.  Text only (no icon-only
// targets), at least 38 px tall and 72 px wide.  Used by the Dictation view
// for Paste, Save and the history row actions.
Rectangle {
    id: button

    property string text: ""
    // "neutral", "go" (confirming) or "danger" (destructive).
    property string tone: "neutral"

    signal clicked()

    implicitWidth: Math.max(72, label.implicitWidth + 28)
    implicitHeight: 38
    radius: 6
    opacity: button.enabled ? 1.0 : 0.4

    readonly property color _fill: tone === "go" ? (area.containsMouse ? "#3a5a3a" : "#2a3a2a")
                                   : (tone === "danger" ? (area.containsMouse ? "#5a3a3a" : "#3a2a2a")
                                      : (area.containsMouse ? "#3a3a3a" : "#2a2a2a"))
    readonly property color _edge: tone === "go" ? "#4a6a4a"
                                   : (tone === "danger" ? "#6a4a4a" : "#4a4a4a")
    readonly property color _ink: tone === "go" ? "#aaffaa"
                                  : (tone === "danger" ? "#ffaaaa" : "#ddd")

    color: _fill
    border.color: _edge
    border.width: 1

    Text {
        id: label
        anchors.centerIn: parent
        text: button.text
        textFormat: Text.PlainText
        color: button._ink
        font.pixelSize: 13
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: button.clicked()
    }

    Accessible.role: Accessible.Button
    Accessible.name: button.text
    Accessible.onPressAction: if (button.enabled) button.clicked()
}
