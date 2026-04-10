import QtQuick 2.15
import QtQuick.Layouts 1.15

Item {
    id: fnRow

    property real keyW: 48
    property real keyH: 36
    property real keySpacing: 2

    implicitWidth: fnLayout.implicitWidth
    implicitHeight: fnLayout.implicitHeight

    RowLayout {
        id: fnLayout
        spacing: fnRow.keySpacing

        // F1-F4
        Repeater {
            model: ["F1", "F2", "F3", "F4"]
            KeyButton {
                keyText: modelData.toLowerCase()
                displayText: modelData
                keyWidth: fnRow.keyW
                keyHeight: fnRow.keyH
                fontSize: 10
                isSpecial: true
                keyColor: "#333333"
                onKeyPressed: keyboard.pressSpecialKey(modelData.toLowerCase())
            }
        }

        // Spacer
        Item { width: fnRow.keySpacing * 2; height: 1; implicitWidth: width; implicitHeight: height }

        // F5-F8
        Repeater {
            model: ["F5", "F6", "F7", "F8"]
            KeyButton {
                keyText: modelData.toLowerCase()
                displayText: modelData
                keyWidth: fnRow.keyW
                keyHeight: fnRow.keyH
                fontSize: 10
                isSpecial: true
                keyColor: "#333333"
                onKeyPressed: keyboard.pressSpecialKey(modelData.toLowerCase())
            }
        }

        // Spacer
        Item { width: fnRow.keySpacing * 2; height: 1; implicitWidth: width; implicitHeight: height }

        // F9-F12
        Repeater {
            model: ["F9", "F10", "F11", "F12"]
            KeyButton {
                keyText: modelData.toLowerCase()
                displayText: modelData
                keyWidth: fnRow.keyW
                keyHeight: fnRow.keyH
                fontSize: 10
                isSpecial: true
                keyColor: "#333333"
                onKeyPressed: keyboard.pressSpecialKey(modelData.toLowerCase())
            }
        }
    }
}
