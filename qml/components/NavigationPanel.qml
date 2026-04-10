import QtQuick 2.15
import QtQuick.Layouts 1.15

Item {
    id: navPanel

    property real keyW: 44
    property real keyH: 44
    property real keySpacing: 2

    implicitWidth: navGrid.implicitWidth
    implicitHeight: navGrid.implicitHeight

    GridLayout {
        id: navGrid
        columns: 3
        rowSpacing: navPanel.keySpacing
        columnSpacing: navPanel.keySpacing
        // Force uniform columns by setting all children to the same preferred size
        property real cellW: navPanel.keyW
        property real cellH: navPanel.keyH

        // Row 1: PrtSc, ScrLk, Pause (system keys — shorter height)
        KeyButton {
            keyText: "print"; displayText: "PrtSc"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH * 0.72
            Layout.preferredWidth: navGrid.cellW; Layout.preferredHeight: navGrid.cellH * 0.72
            fontSize: 9; isSpecial: true; keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("print")
        }
        KeyButton {
            keyText: "scrolllock"; displayText: "ScrLk"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH * 0.72
            Layout.preferredWidth: navGrid.cellW; Layout.preferredHeight: navGrid.cellH * 0.72
            fontSize: 9; isSpecial: true; keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("scrolllock")
        }
        KeyButton {
            keyText: "pause"; displayText: "Pause"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH * 0.72
            Layout.preferredWidth: navGrid.cellW; Layout.preferredHeight: navGrid.cellH * 0.72
            fontSize: 9; isSpecial: true; keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("pause")
        }

        // Row 2: Insert, Home, Page Up
        KeyButton {
            keyText: "insert"; displayText: "Ins"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            Layout.preferredWidth: navGrid.cellW; Layout.preferredHeight: navGrid.cellH
            fontSize: 10; isSpecial: true; keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("insert")
        }
        KeyButton {
            keyText: "home"; displayText: "Home"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            Layout.preferredWidth: navGrid.cellW; Layout.preferredHeight: navGrid.cellH
            fontSize: 10; isSpecial: true; keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("home")
        }
        KeyButton {
            keyText: "pageup"; displayText: "PgUp"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            Layout.preferredWidth: navGrid.cellW; Layout.preferredHeight: navGrid.cellH
            fontSize: 10; isSpecial: true; keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("pageup")
        }

        // Row 3: Delete, End, Page Down
        KeyButton {
            keyText: "delete"; displayText: "Del"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            Layout.preferredWidth: navGrid.cellW; Layout.preferredHeight: navGrid.cellH
            fontSize: 10; isSpecial: true; keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("delete")
        }
        KeyButton {
            keyText: "end"; displayText: "End"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            Layout.preferredWidth: navGrid.cellW; Layout.preferredHeight: navGrid.cellH
            fontSize: 10; isSpecial: true; keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("end")
        }
        KeyButton {
            keyText: "pagedown"; displayText: "PgDn"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            Layout.preferredWidth: navGrid.cellW; Layout.preferredHeight: navGrid.cellH
            fontSize: 10; isSpecial: true; keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("pagedown")
        }

        // Row 4: [spacer], Up, [spacer]
        Item {
            Layout.preferredWidth: navGrid.cellW; Layout.preferredHeight: navGrid.cellH
            implicitWidth: navGrid.cellW; implicitHeight: navGrid.cellH
        }
        KeyButton {
            keyText: "up"; displayText: "▲"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            Layout.preferredWidth: navGrid.cellW; Layout.preferredHeight: navGrid.cellH
            fontSize: 14; isSpecial: true; keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("up")
        }
        Item {
            Layout.preferredWidth: navGrid.cellW; Layout.preferredHeight: navGrid.cellH
            implicitWidth: navGrid.cellW; implicitHeight: navGrid.cellH
        }

        // Row 5: Left, Down, Right
        KeyButton {
            keyText: "left"; displayText: "◀"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            Layout.preferredWidth: navGrid.cellW; Layout.preferredHeight: navGrid.cellH
            fontSize: 14; isSpecial: true; keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("left")
        }
        KeyButton {
            keyText: "down"; displayText: "▼"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            Layout.preferredWidth: navGrid.cellW; Layout.preferredHeight: navGrid.cellH
            fontSize: 14; isSpecial: true; keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("down")
        }
        KeyButton {
            keyText: "right"; displayText: "▶"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            Layout.preferredWidth: navGrid.cellW; Layout.preferredHeight: navGrid.cellH
            fontSize: 14; isSpecial: true; keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("right")
        }
    }
}
