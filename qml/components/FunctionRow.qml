import QtQuick 2.15
import QtQuick.Layouts 1.15

Item {
    id: fnRow

    property real keyW: 48
    property real keyH: 36
    property real keySpacing: 2
    property color keyColor: "#333333"
    property color keyPressedColor: "#5a5a5a"
    property color keyTextColor: "#e0e0e0"
    property color accentColor: "#4a9eff"
    property color borderColor: "#505050"

    implicitWidth: fnLayout.implicitWidth
    implicitHeight: fnLayout.implicitHeight

    // A plain Row, NOT a RowLayout, and that is load-bearing. QtQuick.Layouts
    // rounds every child up to a whole pixel, so 13 keys of 69.23 px each
    // became 13 of 70 and the row rendered 10 px wider than the keyboard grid
    // it is supposed to sit flush with, overhanging the window and clipping
    // its last key. The main keyboard rows are plain Rows for the same
    // reason: keyW is a float derived from the window width and every row
    // must consume it identically.
    Row {
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
                enableRepeat: false
                keyColor: fnRow.keyColor
                keyPressedColor: fnRow.keyPressedColor
                keyTextColor: fnRow.keyTextColor
                accentColor: fnRow.accentColor
                borderColor: fnRow.borderColor
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
                enableRepeat: false
                keyColor: fnRow.keyColor
                keyPressedColor: fnRow.keyPressedColor
                keyTextColor: fnRow.keyTextColor
                accentColor: fnRow.accentColor
                borderColor: fnRow.borderColor
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
                enableRepeat: false
                keyColor: fnRow.keyColor
                keyPressedColor: fnRow.keyPressedColor
                keyTextColor: fnRow.keyTextColor
                accentColor: fnRow.accentColor
                borderColor: fnRow.borderColor
                onKeyPressed: keyboard.pressSpecialKey(modelData.toLowerCase())
            }
        }
    }
}
