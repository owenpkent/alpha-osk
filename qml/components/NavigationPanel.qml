import QtQuick 2.15

Item {
    id: navPanel

    property real keyW: 44
    property real keyH: 44
    property real keySpacing: 2
    // Each key's share of the gap around it; see KeyButton's
    // `hitMarginH`.  This panel is laid out on `keySpacing` in both
    // directions, so a caller passes half of it for both axes.
    property real hitMarginH: 0
    property real hitMarginV: 0
    property color keyColor: "#333333"
    property color keyPressedColor: "#5a5a5a"
    property color keyTextColor: "#e0e0e0"
    property color accentColor: "#4a9eff"
    property color borderColor: "#505050"
    // Hold-to-repeat timing, driven by user-tunable values in Main.qml.
    // Defaults match KeyButton.qml's hardcoded values for safety if a
    // caller doesn't pass them through.
    property int repeatDelay: 500
    property int repeatInterval: 120
    // The gutter above the arrow cluster. A physical keyboard separates the
    // nav block from the arrows, and without it the six-key nav block and the
    // arrows read as one undifferentiated field of keys, so the arrows have to
    // be found by reading rather than by shape.
    property real arrowGap: keySpacing * 4

    implicitWidth: navGrid.implicitWidth
    implicitHeight: navGrid.implicitHeight

    // A plain Grid, NOT a GridLayout. See the "plain Row, NOT a RowLayout"
    // note in NumberRow.qml: QtQuick.Layouts rounds every child up to a whole
    // pixel, and Main.qml reserves an exact float unit budget for this panel
    // when it derives the window's minimum width, so three columns rounding
    // up costs three pixels the window was never given. Grid honours
    // KeyButton's float width/height directly.
    Grid {
        id: navGrid
        columns: 3
        rowSpacing: navPanel.keySpacing
        columnSpacing: navPanel.keySpacing

        property real cellW: navPanel.keyW
        property real cellH: navPanel.keyH

        // Row 1: PrtSc, ScrLk, Pause — full cell height, same as Ins/Home/PgUp
        KeyButton {
            keyText: "print"; displayText: "PrtSc"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            fontSize: 12; isSpecial: true; keyColor: navPanel.keyColor
            keyPressedColor: navPanel.keyPressedColor
            keyTextColor: navPanel.keyTextColor
            accentColor: navPanel.accentColor
            borderColor: navPanel.borderColor
            hitMarginH: navPanel.hitMarginH
            hitMarginV: navPanel.hitMarginV
            onKeyPressed: keyboard.pressSpecialKey("print")
        }
        KeyButton {
            keyText: "scrolllock"; displayText: "ScrLk"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            fontSize: 12; isSpecial: true; keyColor: navPanel.keyColor
            keyPressedColor: navPanel.keyPressedColor
            keyTextColor: navPanel.keyTextColor
            accentColor: navPanel.accentColor
            borderColor: navPanel.borderColor
            hitMarginH: navPanel.hitMarginH
            hitMarginV: navPanel.hitMarginV
            onKeyPressed: keyboard.pressSpecialKey("scrolllock")
        }
        KeyButton {
            keyText: "pause"; displayText: "Pause"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            fontSize: 12; isSpecial: true; keyColor: navPanel.keyColor
            keyPressedColor: navPanel.keyPressedColor
            keyTextColor: navPanel.keyTextColor
            accentColor: navPanel.accentColor
            borderColor: navPanel.borderColor
            hitMarginH: navPanel.hitMarginH
            hitMarginV: navPanel.hitMarginV
            onKeyPressed: keyboard.pressSpecialKey("pause")
        }

        // Row 2: Insert, Home, Page Up
        KeyButton {
            keyText: "insert"; displayText: "Ins"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            fontSize: 12; isSpecial: true; keyColor: navPanel.keyColor
            keyPressedColor: navPanel.keyPressedColor
            keyTextColor: navPanel.keyTextColor
            accentColor: navPanel.accentColor
            borderColor: navPanel.borderColor
            hitMarginH: navPanel.hitMarginH
            hitMarginV: navPanel.hitMarginV
            onKeyPressed: keyboard.pressSpecialKey("insert")
        }
        KeyButton {
            keyText: "home"; displayText: "Home"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            fontSize: 12; isSpecial: true; keyColor: navPanel.keyColor
            keyPressedColor: navPanel.keyPressedColor
            keyTextColor: navPanel.keyTextColor
            accentColor: navPanel.accentColor
            borderColor: navPanel.borderColor
            hitMarginH: navPanel.hitMarginH
            hitMarginV: navPanel.hitMarginV
            onKeyPressed: keyboard.pressSpecialKey("home")
        }
        KeyButton {
            keyText: "pageup"; displayText: "PgUp"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            fontSize: 12; isSpecial: true; keyColor: navPanel.keyColor
            keyPressedColor: navPanel.keyPressedColor
            keyTextColor: navPanel.keyTextColor
            accentColor: navPanel.accentColor
            borderColor: navPanel.borderColor
            hitMarginH: navPanel.hitMarginH
            hitMarginV: navPanel.hitMarginV
            enableRepeat: true; repeatDelay: navPanel.repeatDelay; repeatInterval: navPanel.repeatInterval
            onKeyPressed: keyboard.pressSpecialKey("pageup")
        }

        // Row 3: Delete, End, Page Down
        KeyButton {
            keyText: "delete"; displayText: "Del"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            fontSize: 12; isSpecial: true; keyColor: navPanel.keyColor
            keyPressedColor: navPanel.keyPressedColor
            keyTextColor: navPanel.keyTextColor
            accentColor: navPanel.accentColor
            borderColor: navPanel.borderColor
            hitMarginH: navPanel.hitMarginH
            hitMarginV: navPanel.hitMarginV
            enableRepeat: true; repeatDelay: navPanel.repeatDelay; repeatInterval: navPanel.repeatInterval
            onKeyPressed: keyboard.pressSpecialKey("delete")
        }
        KeyButton {
            keyText: "end"; displayText: "End"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            fontSize: 12; isSpecial: true; keyColor: navPanel.keyColor
            keyPressedColor: navPanel.keyPressedColor
            keyTextColor: navPanel.keyTextColor
            accentColor: navPanel.accentColor
            borderColor: navPanel.borderColor
            hitMarginH: navPanel.hitMarginH
            hitMarginV: navPanel.hitMarginV
            onKeyPressed: keyboard.pressSpecialKey("end")
        }
        KeyButton {
            keyText: "pagedown"; displayText: "PgDn"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            fontSize: 12; isSpecial: true; keyColor: navPanel.keyColor
            keyPressedColor: navPanel.keyPressedColor
            keyTextColor: navPanel.keyTextColor
            accentColor: navPanel.accentColor
            borderColor: navPanel.borderColor
            hitMarginH: navPanel.hitMarginH
            hitMarginV: navPanel.hitMarginV
            enableRepeat: true; repeatDelay: navPanel.repeatDelay; repeatInterval: navPanel.repeatInterval
            onKeyPressed: keyboard.pressSpecialKey("pagedown")
        }

        // Row 4: [spacer], Up, [spacer].
        //
        // All three cells are `arrowGap` taller than a key, and Up is anchored
        // to the BOTTOM of its cell, so the extra height opens above the arrow
        // cluster rather than below it. A Grid aligns a cell's content to the
        // top, so growing the cells alone would have put the gutter between Up
        // and the Left/Down/Right row, splitting the cluster it is meant to
        // separate from the block above.
        //
        // The key itself stays exactly cellH, so its hit area is unchanged and
        // the gutter is dead space by construction. That is deliberate here,
        // unlike the gaps between keys: this one is a separator the eye uses,
        // the same trade FunctionRow makes for its group spacing.
        Item {
            width: navGrid.cellW; height: navGrid.cellH + navPanel.arrowGap
        }
        Item {
            width: navGrid.cellW; height: navGrid.cellH + navPanel.arrowGap
            KeyButton {
                anchors.bottom: parent.bottom
                keyText: "up"; displayText: "↑"
                keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
                fontSize: 16; isSpecial: true; keyColor: navPanel.keyColor
                keyPressedColor: navPanel.keyPressedColor
                keyTextColor: navPanel.keyTextColor
                accentColor: navPanel.accentColor
                borderColor: navPanel.borderColor
                hitMarginH: navPanel.hitMarginH
                hitMarginV: navPanel.hitMarginV
                enableRepeat: true; repeatDelay: navPanel.repeatDelay; repeatInterval: navPanel.repeatInterval
                onKeyPressed: keyboard.pressSpecialKey("up")
            }
        }
        Item {
            width: navGrid.cellW; height: navGrid.cellH + navPanel.arrowGap
        }

        // Row 5: Left, Down, Right
        KeyButton {
            keyText: "left"; displayText: "←"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            fontSize: 16; isSpecial: true; keyColor: navPanel.keyColor
            keyPressedColor: navPanel.keyPressedColor
            keyTextColor: navPanel.keyTextColor
            accentColor: navPanel.accentColor
            borderColor: navPanel.borderColor
            hitMarginH: navPanel.hitMarginH
            hitMarginV: navPanel.hitMarginV
            enableRepeat: true; repeatDelay: navPanel.repeatDelay; repeatInterval: navPanel.repeatInterval
            onKeyPressed: keyboard.pressSpecialKey("left")
        }
        KeyButton {
            keyText: "down"; displayText: "↓"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            fontSize: 16; isSpecial: true; keyColor: navPanel.keyColor
            keyPressedColor: navPanel.keyPressedColor
            keyTextColor: navPanel.keyTextColor
            accentColor: navPanel.accentColor
            borderColor: navPanel.borderColor
            hitMarginH: navPanel.hitMarginH
            hitMarginV: navPanel.hitMarginV
            enableRepeat: true; repeatDelay: navPanel.repeatDelay; repeatInterval: navPanel.repeatInterval
            onKeyPressed: keyboard.pressSpecialKey("down")
        }
        KeyButton {
            keyText: "right"; displayText: "→"
            keyWidth: navGrid.cellW; keyHeight: navGrid.cellH
            fontSize: 16; isSpecial: true; keyColor: navPanel.keyColor
            keyPressedColor: navPanel.keyPressedColor
            keyTextColor: navPanel.keyTextColor
            accentColor: navPanel.accentColor
            borderColor: navPanel.borderColor
            hitMarginH: navPanel.hitMarginH
            hitMarginV: navPanel.hitMarginV
            enableRepeat: true; repeatDelay: navPanel.repeatDelay; repeatInterval: navPanel.repeatInterval
            onKeyPressed: keyboard.pressSpecialKey("right")
        }
    }
}
