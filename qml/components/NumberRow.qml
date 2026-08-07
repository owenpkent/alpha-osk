import QtQuick 2.15
import QtQuick.Layouts 1.15

// Standalone number row: ` 1 2 3 4 5 6 7 8 9 0 - =
//
// Thirteen 1u keys, which is exactly the compact grid's 13.0 units, so the
// row sits flush above a compact keyboard with no side gutters. Toggled from
// Settings -> Appearance -> Panels -> Number Row.
//
// It exists for the compact view, where the digits live behind the ?123 hop.
// The full-size layouts carry their own number row inside the layout JSON, so
// enabling this on top of one just gives a (narrower, centred) duplicate.
//
// Behaviour deliberately mirrors the main grid's char keys key-for-key:
// shift shows and types the shifted glyph, right-click types it without
// touching the sticky shift state, and both paths flash the preview bubble.
// The three callbacks are passed in as function properties because a
// component in this directory cannot see Main.qml's `root` id.
Item {
    id: numRow

    property real keyW: 48
    property real keyH: 36
    property real keySpacing: 2
    property color keyColor: "#333333"
    property color keyPressedColor: "#5a5a5a"
    property color keyTextColor: "#e0e0e0"
    property color accentColor: "#4a9eff"
    property color borderColor: "#505050"

    property bool shiftOn: false
    property bool rightClickShift: true
    property bool keyPreviewEnabled: true

    // Wired to root.registerCharKey / unregisterCharKey / showKeyPreview /
    // hideKeyPreview in Main.qml.  Registration matters: the swipe overlay
    // covers the whole main-keyboard area and hit-tests the registry to pass
    // taps through, so an unregistered key is a dead tap while swipe is on.
    property var registerFn: null
    property var unregisterFn: null
    property var previewFn: null
    property var hidePreviewFn: null

    readonly property var keyDefs: [
        { key: "`", shifted: "~" },
        { key: "1", shifted: "!" },
        { key: "2", shifted: "@" },
        { key: "3", shifted: "#" },
        { key: "4", shifted: "$" },
        { key: "5", shifted: "%" },
        { key: "6", shifted: "^" },
        { key: "7", shifted: "&" },
        { key: "8", shifted: "*" },
        { key: "9", shifted: "(" },
        { key: "0", shifted: ")" },
        { key: "-", shifted: "_" },
        { key: "=", shifted: "+" }
    ]

    implicitWidth: numLayout.implicitWidth
    implicitHeight: numLayout.implicitHeight

    RowLayout {
        id: numLayout
        spacing: numRow.keySpacing

        Repeater {
            model: numRow.keyDefs

            KeyButton {
                id: numKey
                property var kd: modelData

                Component.onCompleted: if (numRow.registerFn)
                    numRow.registerFn(numKey, { type: "char", key: kd.key })
                Component.onDestruction: if (numRow.unregisterFn)
                    numRow.unregisterFn(numKey)

                keyText: kd.key
                displayText: numRow.shiftOn ? kd.shifted : kd.key
                keyWidth: numRow.keyW
                keyHeight: numRow.keyH
                fontSize: 14
                isSpecial: false
                // Digits must not auto-repeat, same as every other char key.
                enableRepeat: false
                keyColor: numRow.keyColor
                keyPressedColor: numRow.keyPressedColor
                keyTextColor: numRow.keyTextColor
                accentColor: numRow.accentColor
                borderColor: numRow.borderColor

                onKeyPressed: {
                    var ch = numRow.shiftOn ? kd.shifted : kd.key
                    keyboard.pressKey(ch)
                    if (numRow.keyPreviewEnabled && numRow.previewFn)
                        numRow.previewFn(numKey, ch)
                }

                // pressKeyLiteral, not pressKey: the latter lowercases and
                // would undo the variant we just resolved.
                onKeyRightPressed: {
                    if (!numRow.rightClickShift) return
                    keyboard.pressKeyLiteral(kd.shifted)
                    if (numRow.keyPreviewEnabled && numRow.previewFn)
                        numRow.previewFn(numKey, kd.shifted)
                }

                onKeyReleased: if (numRow.hidePreviewFn) numRow.hidePreviewFn()
            }
        }
    }
}
