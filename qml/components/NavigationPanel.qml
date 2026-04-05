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
        
        // Row 1: Insert, Home, Page Up
        KeyButton {
            keyText: "insert"
            displayText: "Ins"
            keyWidth: navPanel.keyW
            keyHeight: navPanel.keyH
            fontSize: 10
            isSpecial: true
            keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("insert")
        }
        KeyButton {
            keyText: "home"
            displayText: "Home"
            keyWidth: navPanel.keyW
            keyHeight: navPanel.keyH
            fontSize: 10
            isSpecial: true
            keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("home")
        }
        KeyButton {
            keyText: "pageup"
            displayText: "PgUp"
            keyWidth: navPanel.keyW
            keyHeight: navPanel.keyH
            fontSize: 10
            isSpecial: true
            keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("pageup")
        }
        
        // Row 2: Delete, End, Page Down
        KeyButton {
            keyText: "delete"
            displayText: "Del"
            keyWidth: navPanel.keyW
            keyHeight: navPanel.keyH
            fontSize: 10
            isSpecial: true
            keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("delete")
        }
        KeyButton {
            keyText: "end"
            displayText: "End"
            keyWidth: navPanel.keyW
            keyHeight: navPanel.keyH
            fontSize: 10
            isSpecial: true
            keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("end")
        }
        KeyButton {
            keyText: "pagedown"
            displayText: "PgDn"
            keyWidth: navPanel.keyW
            keyHeight: navPanel.keyH
            fontSize: 10
            isSpecial: true
            keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("pagedown")
        }
        
        // Row 3: Empty, Up Arrow, Empty
        Item { width: navPanel.keyW; height: navPanel.keyH; implicitWidth: width; implicitHeight: height }
        KeyButton {
            keyText: "up"
            displayText: "▲"
            keyWidth: navPanel.keyW
            keyHeight: navPanel.keyH
            fontSize: 14
            isSpecial: true
            keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("up")
        }
        Item { width: navPanel.keyW; height: navPanel.keyH; implicitWidth: width; implicitHeight: height }
        
        // Row 4: Left, Down, Right
        KeyButton {
            keyText: "left"
            displayText: "◀"
            keyWidth: navPanel.keyW
            keyHeight: navPanel.keyH
            fontSize: 14
            isSpecial: true
            keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("left")
        }
        KeyButton {
            keyText: "down"
            displayText: "▼"
            keyWidth: navPanel.keyW
            keyHeight: navPanel.keyH
            fontSize: 14
            isSpecial: true
            keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("down")
        }
        KeyButton {
            keyText: "right"
            displayText: "▶"
            keyWidth: navPanel.keyW
            keyHeight: navPanel.keyH
            fontSize: 14
            isSpecial: true
            keyColor: "#333333"
            onKeyPressed: keyboard.pressSpecialKey("right")
        }
    }
}
