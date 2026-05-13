import QtQuick 2.15
import QtQuick.Layouts 1.15

Item {
    id: numpadPanel

    property real keyW: 48
    property real keyH: 44
    property real keySpacing: 2
    property bool numLockOn: true
    property color keyColor: "#3a3a3a"
    property color specialKeyColor: "#333333"
    property color keyPressedColor: "#5a5a5a"
    property color keyTextColor: "#e0e0e0"
    property color enterKeyColor: "#2a5a2a"
    property color accentColor: "#4a9eff"
    property color borderColor: "#505050"

    implicitWidth: numGrid.implicitWidth
    implicitHeight: numGrid.implicitHeight

    GridLayout {
        id: numGrid
        columns: 4
        rowSpacing: numpadPanel.keySpacing
        columnSpacing: numpadPanel.keySpacing

        // Row 1: 7/Home, 8/Up, 9/PgUp, /
        KeyButton {
            displayText: numpadPanel.numLockOn ? "7" : "Home"
            keyWidth: numpadPanel.keyW
            keyHeight: numpadPanel.keyH
            fontSize: numpadPanel.numLockOn ? 14 : 12
            keyColor: numpadPanel.keyColor
            keyPressedColor: numpadPanel.keyPressedColor
            keyTextColor: numpadPanel.keyTextColor
            accentColor: numpadPanel.accentColor
            borderColor: numpadPanel.borderColor
            onKeyPressed: numpadPanel.numLockOn ? keyboard.pressKey("7") : keyboard.pressSpecialKey("home")
        }
        KeyButton {
            displayText: numpadPanel.numLockOn ? "8" : "↑"
            keyWidth: numpadPanel.keyW
            keyHeight: numpadPanel.keyH
            fontSize: 14
            keyColor: numpadPanel.keyColor
            keyPressedColor: numpadPanel.keyPressedColor
            keyTextColor: numpadPanel.keyTextColor
            accentColor: numpadPanel.accentColor
            borderColor: numpadPanel.borderColor
            onKeyPressed: numpadPanel.numLockOn ? keyboard.pressKey("8") : keyboard.pressSpecialKey("up")
        }
        KeyButton {
            displayText: numpadPanel.numLockOn ? "9" : "PgUp"
            keyWidth: numpadPanel.keyW
            keyHeight: numpadPanel.keyH
            fontSize: numpadPanel.numLockOn ? 14 : 12
            keyColor: numpadPanel.keyColor
            keyPressedColor: numpadPanel.keyPressedColor
            keyTextColor: numpadPanel.keyTextColor
            accentColor: numpadPanel.accentColor
            borderColor: numpadPanel.borderColor
            onKeyPressed: numpadPanel.numLockOn ? keyboard.pressKey("9") : keyboard.pressSpecialKey("pageup")
        }
        KeyButton {
            keyText: "/"
            displayText: "/"
            keyWidth: numpadPanel.keyW
            keyHeight: numpadPanel.keyH
            fontSize: 14
            keyColor: numpadPanel.specialKeyColor
            keyPressedColor: numpadPanel.keyPressedColor
            keyTextColor: numpadPanel.keyTextColor
            accentColor: numpadPanel.accentColor
            borderColor: numpadPanel.borderColor
            onKeyPressed: keyboard.pressKey("/")
        }

        // Row 2: 4/Left, 5, 6/Right, *
        KeyButton {
            displayText: numpadPanel.numLockOn ? "4" : "←"
            keyWidth: numpadPanel.keyW
            keyHeight: numpadPanel.keyH
            fontSize: 14
            keyColor: numpadPanel.keyColor
            keyPressedColor: numpadPanel.keyPressedColor
            keyTextColor: numpadPanel.keyTextColor
            accentColor: numpadPanel.accentColor
            borderColor: numpadPanel.borderColor
            onKeyPressed: numpadPanel.numLockOn ? keyboard.pressKey("4") : keyboard.pressSpecialKey("left")
        }
        KeyButton {
            displayText: numpadPanel.numLockOn ? "5" : ""
            keyWidth: numpadPanel.keyW
            keyHeight: numpadPanel.keyH
            fontSize: 14
            keyColor: numpadPanel.keyColor
            keyPressedColor: numpadPanel.keyPressedColor
            keyTextColor: numpadPanel.keyTextColor
            accentColor: numpadPanel.accentColor
            borderColor: numpadPanel.borderColor
            enabled: numpadPanel.numLockOn
            onKeyPressed: if (numpadPanel.numLockOn) keyboard.pressKey("5")
        }
        KeyButton {
            displayText: numpadPanel.numLockOn ? "6" : "→"
            keyWidth: numpadPanel.keyW
            keyHeight: numpadPanel.keyH
            fontSize: 14
            keyColor: numpadPanel.keyColor
            keyPressedColor: numpadPanel.keyPressedColor
            keyTextColor: numpadPanel.keyTextColor
            accentColor: numpadPanel.accentColor
            borderColor: numpadPanel.borderColor
            onKeyPressed: numpadPanel.numLockOn ? keyboard.pressKey("6") : keyboard.pressSpecialKey("right")
        }
        KeyButton {
            keyText: "*"
            displayText: "*"
            keyWidth: numpadPanel.keyW
            keyHeight: numpadPanel.keyH
            fontSize: 14
            keyColor: numpadPanel.specialKeyColor
            keyPressedColor: numpadPanel.keyPressedColor
            keyTextColor: numpadPanel.keyTextColor
            accentColor: numpadPanel.accentColor
            borderColor: numpadPanel.borderColor
            onKeyPressed: keyboard.pressKey("*")
        }

        // Row 3: 1/End, 2/Down, 3/PgDn, -
        KeyButton {
            displayText: numpadPanel.numLockOn ? "1" : "End"
            keyWidth: numpadPanel.keyW
            keyHeight: numpadPanel.keyH
            fontSize: numpadPanel.numLockOn ? 14 : 12
            keyColor: numpadPanel.keyColor
            keyPressedColor: numpadPanel.keyPressedColor
            keyTextColor: numpadPanel.keyTextColor
            accentColor: numpadPanel.accentColor
            borderColor: numpadPanel.borderColor
            onKeyPressed: numpadPanel.numLockOn ? keyboard.pressKey("1") : keyboard.pressSpecialKey("end")
        }
        KeyButton {
            displayText: numpadPanel.numLockOn ? "2" : "↓"
            keyWidth: numpadPanel.keyW
            keyHeight: numpadPanel.keyH
            fontSize: 14
            keyColor: numpadPanel.keyColor
            keyPressedColor: numpadPanel.keyPressedColor
            keyTextColor: numpadPanel.keyTextColor
            accentColor: numpadPanel.accentColor
            borderColor: numpadPanel.borderColor
            onKeyPressed: numpadPanel.numLockOn ? keyboard.pressKey("2") : keyboard.pressSpecialKey("down")
        }
        KeyButton {
            displayText: numpadPanel.numLockOn ? "3" : "PgDn"
            keyWidth: numpadPanel.keyW
            keyHeight: numpadPanel.keyH
            fontSize: numpadPanel.numLockOn ? 14 : 12
            keyColor: numpadPanel.keyColor
            keyPressedColor: numpadPanel.keyPressedColor
            keyTextColor: numpadPanel.keyTextColor
            accentColor: numpadPanel.accentColor
            borderColor: numpadPanel.borderColor
            onKeyPressed: numpadPanel.numLockOn ? keyboard.pressKey("3") : keyboard.pressSpecialKey("pagedown")
        }
        KeyButton {
            keyText: "-"
            displayText: "-"
            keyWidth: numpadPanel.keyW
            keyHeight: numpadPanel.keyH
            fontSize: 14
            keyColor: numpadPanel.specialKeyColor
            keyPressedColor: numpadPanel.keyPressedColor
            keyTextColor: numpadPanel.keyTextColor
            accentColor: numpadPanel.accentColor
            borderColor: numpadPanel.borderColor
            onKeyPressed: keyboard.pressKey("-")
        }

        // Row 4: 0/Ins (span 2), ./Del, +
        KeyButton {
            displayText: numpadPanel.numLockOn ? "0" : "Ins"
            keyWidth: numpadPanel.keyW * 2 + numpadPanel.keySpacing
            keyHeight: numpadPanel.keyH
            fontSize: numpadPanel.numLockOn ? 14 : 12
            keyColor: numpadPanel.keyColor
            keyPressedColor: numpadPanel.keyPressedColor
            keyTextColor: numpadPanel.keyTextColor
            accentColor: numpadPanel.accentColor
            borderColor: numpadPanel.borderColor
            Layout.columnSpan: 2
            onKeyPressed: numpadPanel.numLockOn ? keyboard.pressKey("0") : keyboard.pressSpecialKey("insert")
        }
        KeyButton {
            displayText: numpadPanel.numLockOn ? "." : "Del"
            keyWidth: numpadPanel.keyW
            keyHeight: numpadPanel.keyH
            fontSize: numpadPanel.numLockOn ? 14 : 12
            keyColor: numpadPanel.keyColor
            keyPressedColor: numpadPanel.keyPressedColor
            keyTextColor: numpadPanel.keyTextColor
            accentColor: numpadPanel.accentColor
            borderColor: numpadPanel.borderColor
            onKeyPressed: numpadPanel.numLockOn ? keyboard.pressKey(".") : keyboard.pressSpecialKey("delete")
        }
        KeyButton {
            displayText: "+"
            keyWidth: numpadPanel.keyW
            keyHeight: numpadPanel.keyH
            fontSize: 14
            keyColor: numpadPanel.specialKeyColor
            keyPressedColor: numpadPanel.keyPressedColor
            keyTextColor: numpadPanel.keyTextColor
            accentColor: numpadPanel.accentColor
            borderColor: numpadPanel.borderColor
            onKeyPressed: keyboard.pressKey("+")
        }

        // Row 5: Enter (span 3), NumLock
        KeyButton {
            displayText: "Enter"
            keyWidth: numpadPanel.keyW * 3 + numpadPanel.keySpacing * 2
            keyHeight: numpadPanel.keyH
            fontSize: 14
            isSpecial: true
            keyColor: numpadPanel.enterKeyColor
            keyPressedColor: numpadPanel.keyPressedColor
            keyTextColor: numpadPanel.keyTextColor
            accentColor: numpadPanel.accentColor
            borderColor: numpadPanel.borderColor
            Layout.columnSpan: 3
            onKeyPressed: keyboard.pressSpecialKey("return")
        }
        KeyButton {
            keyText: "numlock"
            displayText: "Num"
            keyWidth: numpadPanel.keyW
            keyHeight: numpadPanel.keyH
            fontSize: 12
            isSpecial: true
            isActive: numpadPanel.numLockOn
            keyColor: numpadPanel.specialKeyColor
            keyPressedColor: numpadPanel.keyPressedColor
            keyTextColor: numpadPanel.keyTextColor
            accentColor: numpadPanel.accentColor
            borderColor: numpadPanel.borderColor
            onKeyPressed: {
                keyboard.pressSpecialKey("numlock")
                numpadPanel.numLockOn = !numpadPanel.numLockOn
            }
        }
    }
}
