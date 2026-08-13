import QtQuick 2.15
import QtQuick.Layouts 1.15

Item {
    id: toggle
    
    property string text: ""
    // Optional second line under the label, for settings whose effect isn't
    // obvious from the name alone.  Empty by default, so every existing
    // toggle keeps the original single-line 28 px height.
    property string description: ""
    property bool checked: false

    signal toggled(bool checked)

    implicitHeight: Math.max(28, labelCol.implicitHeight + 10)

    // `enabled` propagates to the MouseArea below, so a disabled toggle
    // already ignores clicks; this is the matching visual.  Dimmed rather
    // than hidden: a setting that vanishes reads as a missing feature,
    // where a greyed one plus its description says "unavailable, and here
    // is why".
    opacity: toggle.enabled ? 1.0 : 0.4

    Rectangle {
        anchors.fill: parent
        radius: 4
        color: toggleArea.containsMouse ? "#333333" : "transparent"

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 4
            anchors.rightMargin: 4
            spacing: 8

            ColumnLayout {
                id: labelCol
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
                spacing: 1

                Text {
                    text: toggle.text
                    // Both fields can carry externally-sourced strings (e.g.
                    // an imported vocabulary pack's name/description from
                    // pack.json). Force plain rendering so Qt's AutoText
                    // HTML auto-detection can't turn a crafted <img> tag
                    // into an outbound request just from this list rendering.
                    textFormat: Text.PlainText
                    color: "#c0c0c0"
                    font.pixelSize: 12
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }

                Text {
                    visible: toggle.description !== ""
                    text: toggle.description
                    textFormat: Text.PlainText
                    color: "#8a8a8a"
                    font.pixelSize: 10
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                }
            }

            Rectangle {
                Layout.preferredWidth: 36
                Layout.minimumWidth: 36
                Layout.preferredHeight: 18
                Layout.alignment: Qt.AlignVCenter
                width: 36
                height: 18
                radius: 9
                color: toggle.checked ? "#4a9eff" : "#555555"
                
                Rectangle {
                    width: 14
                    height: 14
                    radius: 7
                    x: toggle.checked ? parent.width - width - 2 : 2
                    anchors.verticalCenter: parent.verticalCenter
                    color: "#ffffff"
                    
                    Behavior on x {
                        NumberAnimation { duration: 100 }
                    }
                }
                
                Behavior on color {
                    ColorAnimation { duration: 100 }
                }
            }
        }
        
        MouseArea {
            id: toggleArea
            anchors.fill: parent
            hoverEnabled: true
            onClicked: {
                toggle.checked = !toggle.checked
                toggle.toggled(toggle.checked)
            }
        }
    }
}
