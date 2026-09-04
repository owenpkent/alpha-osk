import QtQuick 2.15

Item {
    id: numpadPanel

    property real keyW: 48
    property real keyH: 44
    property real keySpacing: 2
    // Each key's share of the gap around it; see KeyButton's
    // `hitMarginH`.  This panel is laid out on `keySpacing` in both
    // directions, so a caller passes half of it for both axes.
    property real hitMarginH: 0
    property real hitMarginV: 0
    property bool numLockOn: true
    property color keyColor: "#3a3a3a"
    property color specialKeyColor: "#333333"
    property color keyPressedColor: "#5a5a5a"
    property color keyTextColor: "#e0e0e0"
    property color enterKeyColor: "#2a5a2a"
    property color accentColor: "#4a9eff"
    property color borderColor: "#505050"
    // Hold-to-repeat. Two different populations share this grid depending on
    // NumLock: with it on every key but Enter/NumLock types a character, so
    // those follow `characterRepeat` exactly like the main grid's letters
    // (operators included - "/", "*", "-", "+" are `pressKey` calls just
    // like a digit is, so there is no reason to treat them differently).
    // With it off, the digits become the navigation actions
    // NavigationPanel already carries, and each one repeats exactly as it
    // does over there, regardless of this setting: up/down/left/right,
    // pageup/pagedown and delete repeat, Home and End do not (the caret
    // cannot move past the line start or end, so every press after the
    // first is a no-op). See each key's `enableRepeat` below. Enter and
    // NumLock never repeat either way.
    property bool characterRepeat: true
    property int repeatDelay: 500
    property int repeatInterval: 120

    implicitWidth: numGrid.implicitWidth
    implicitHeight: numGrid.implicitHeight

    // Plain Column-of-Rows, NOT a GridLayout. See the "plain Row, NOT a
    // RowLayout" note in NumberRow.qml: QtQuick.Layouts rounds every child up
    // to a whole pixel, and Main.qml reserves an exact float unit budget for
    // this panel when it derives the window's minimum width, so four columns
    // rounding up costs four pixels the window was never given. The 0 and
    // Enter keys were the reason a GridLayout was reached for (columnSpan
    // 2 and 3); they already carry their own spanned width in keyWidth, so
    // in a Row they need no span at all.
    Column {
        id: numGrid
        spacing: numpadPanel.keySpacing

        // Row 1: 7/Home, 8/Up, 9/PgUp, /
        Row {
            spacing: numpadPanel.keySpacing
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
                hitMarginH: numpadPanel.hitMarginH
                hitMarginV: numpadPanel.hitMarginV
                // Home does not repeat with NumLock off, matching
                // NavigationPanel: the caret is already at the line start
                // after the first press, so every later one is a no-op.
                enableRepeat: numpadPanel.numLockOn && numpadPanel.characterRepeat
                repeatDelay: numpadPanel.repeatDelay
                repeatInterval: numpadPanel.repeatInterval
                repeatArmFloorMs: numpadPanel.numLockOn ? 800 : 0
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
                hitMarginH: numpadPanel.hitMarginH
                hitMarginV: numpadPanel.hitMarginV
                enableRepeat: numpadPanel.numLockOn ? numpadPanel.characterRepeat : true
                repeatDelay: numpadPanel.repeatDelay
                repeatInterval: numpadPanel.repeatInterval
                repeatArmFloorMs: numpadPanel.numLockOn ? 800 : 0
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
                hitMarginH: numpadPanel.hitMarginH
                hitMarginV: numpadPanel.hitMarginV
                enableRepeat: numpadPanel.numLockOn ? numpadPanel.characterRepeat : true
                repeatDelay: numpadPanel.repeatDelay
                repeatInterval: numpadPanel.repeatInterval
                repeatArmFloorMs: numpadPanel.numLockOn ? 800 : 0
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
                hitMarginH: numpadPanel.hitMarginH
                hitMarginV: numpadPanel.hitMarginV
                enableRepeat: numpadPanel.characterRepeat
                repeatDelay: numpadPanel.repeatDelay
                repeatInterval: numpadPanel.repeatInterval
                repeatArmFloorMs: 800
                onKeyPressed: keyboard.pressKey("/")
            }
        }

        // Row 2: 4/Left, 5, 6/Right, *
        Row {
            spacing: numpadPanel.keySpacing
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
                hitMarginH: numpadPanel.hitMarginH
                hitMarginV: numpadPanel.hitMarginV
                enableRepeat: numpadPanel.numLockOn ? numpadPanel.characterRepeat : true
                repeatDelay: numpadPanel.repeatDelay
                repeatInterval: numpadPanel.repeatInterval
                repeatArmFloorMs: numpadPanel.numLockOn ? 800 : 0
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
                hitMarginH: numpadPanel.hitMarginH
                hitMarginV: numpadPanel.hitMarginV
                enabled: numpadPanel.numLockOn
                // Blank and disabled with NumLock off, so nothing to repeat.
                enableRepeat: numpadPanel.numLockOn && numpadPanel.characterRepeat
                repeatDelay: numpadPanel.repeatDelay
                repeatInterval: numpadPanel.repeatInterval
                repeatArmFloorMs: numpadPanel.numLockOn ? 800 : 0
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
                hitMarginH: numpadPanel.hitMarginH
                hitMarginV: numpadPanel.hitMarginV
                enableRepeat: numpadPanel.numLockOn ? numpadPanel.characterRepeat : true
                repeatDelay: numpadPanel.repeatDelay
                repeatInterval: numpadPanel.repeatInterval
                repeatArmFloorMs: numpadPanel.numLockOn ? 800 : 0
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
                hitMarginH: numpadPanel.hitMarginH
                hitMarginV: numpadPanel.hitMarginV
                enableRepeat: numpadPanel.characterRepeat
                repeatDelay: numpadPanel.repeatDelay
                repeatInterval: numpadPanel.repeatInterval
                repeatArmFloorMs: 800
                onKeyPressed: keyboard.pressKey("*")
            }
        }

        // Row 3: 1/End, 2/Down, 3/PgDn, -
        Row {
            spacing: numpadPanel.keySpacing
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
                hitMarginH: numpadPanel.hitMarginH
                hitMarginV: numpadPanel.hitMarginV
                // End does not repeat with NumLock off, same reason as Home
                // on the 7 key above.
                enableRepeat: numpadPanel.numLockOn && numpadPanel.characterRepeat
                repeatDelay: numpadPanel.repeatDelay
                repeatInterval: numpadPanel.repeatInterval
                repeatArmFloorMs: numpadPanel.numLockOn ? 800 : 0
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
                hitMarginH: numpadPanel.hitMarginH
                hitMarginV: numpadPanel.hitMarginV
                enableRepeat: numpadPanel.numLockOn ? numpadPanel.characterRepeat : true
                repeatDelay: numpadPanel.repeatDelay
                repeatInterval: numpadPanel.repeatInterval
                repeatArmFloorMs: numpadPanel.numLockOn ? 800 : 0
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
                hitMarginH: numpadPanel.hitMarginH
                hitMarginV: numpadPanel.hitMarginV
                enableRepeat: numpadPanel.numLockOn ? numpadPanel.characterRepeat : true
                repeatDelay: numpadPanel.repeatDelay
                repeatInterval: numpadPanel.repeatInterval
                repeatArmFloorMs: numpadPanel.numLockOn ? 800 : 0
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
                hitMarginH: numpadPanel.hitMarginH
                hitMarginV: numpadPanel.hitMarginV
                enableRepeat: numpadPanel.characterRepeat
                repeatDelay: numpadPanel.repeatDelay
                repeatInterval: numpadPanel.repeatInterval
                repeatArmFloorMs: 800
                onKeyPressed: keyboard.pressKey("-")
            }
        }

        // Row 4: 0/Ins (2 cells wide), ./Del, +
        Row {
            spacing: numpadPanel.keySpacing
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
                hitMarginH: numpadPanel.hitMarginH
                hitMarginV: numpadPanel.hitMarginV
                // Insert is not in the repeat-worthy nav set (matches
                // NavigationPanel's own Insert key, which has no
                // enableRepeat either), so it stays off with NumLock off.
                enableRepeat: numpadPanel.numLockOn ? numpadPanel.characterRepeat : false
                repeatDelay: numpadPanel.repeatDelay
                repeatInterval: numpadPanel.repeatInterval
                repeatArmFloorMs: numpadPanel.numLockOn ? 800 : 0
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
                hitMarginH: numpadPanel.hitMarginH
                hitMarginV: numpadPanel.hitMarginV
                enableRepeat: numpadPanel.numLockOn ? numpadPanel.characterRepeat : true
                repeatDelay: numpadPanel.repeatDelay
                repeatInterval: numpadPanel.repeatInterval
                repeatArmFloorMs: numpadPanel.numLockOn ? 800 : 0
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
                hitMarginH: numpadPanel.hitMarginH
                hitMarginV: numpadPanel.hitMarginV
                enableRepeat: numpadPanel.characterRepeat
                repeatDelay: numpadPanel.repeatDelay
                repeatInterval: numpadPanel.repeatInterval
                repeatArmFloorMs: 800
                onKeyPressed: keyboard.pressKey("+")
            }
        }

        // Row 5: Enter (3 cells wide), NumLock
        Row {
            spacing: numpadPanel.keySpacing
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
                hitMarginH: numpadPanel.hitMarginH
                hitMarginV: numpadPanel.hitMarginV
                // Never repeats: not in root.repeatableActions, and holding
                // Enter/Return on a slow release firing twenty times is
                // exactly the hostile case that list exists to exclude.
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
                hitMarginH: numpadPanel.hitMarginH
                hitMarginV: numpadPanel.hitMarginV
                // Never repeats: it toggles a mode on each activation, so a
                // hold-driven repeat would just flip NumLock back and forth
                // for as long as the button stayed down.
                onKeyPressed: {
                    keyboard.pressSpecialKey("numlock")
                    numpadPanel.numLockOn = !numpadPanel.numLockOn
                }
            }
        }
    }
}
