import QtQuick 2.15

// Standalone number row: Esc 1 2 3 4 5 6 7 8 9 0 - =
//
// Thirteen 1u keys, which is exactly the compact grid's 13.0 units, so the
// row sits flush above a compact keyboard with no side gutters. Toggled from
// Settings -> Appearance -> Panels -> Number Row.
//
// It exists for the compact view, where the digits live behind the ?123 hop.
// The full-size layouts carry their own number row inside the layout JSON, so
// enabling this on top of one just gives a (narrower, centred) duplicate.
//
// The leading slot is Esc, not the physical keyboard's ` / ~ . Compact traded
// Esc onto the ?123 layer to make room for Del (docs/architecture/
// COMPACT_VIEW.md), which put the single most common "get me out of this
// dialog" key behind a hop; this row puts it back at the top-left corner
// where it lives on a real keyboard. Backtick is not lost - it stays on ?123
// row 2, and the full-size layouts keep their own ` in the layout JSON.
// Esc duplicates the ?123 one on purpose: this row is optional and off by
// default, so ?123 has to remain the fallback when it is hidden.
//
// Char behaviour deliberately mirrors the main grid's char keys key-for-key:
// shift shows and types the shifted glyph, right-click types it without
// touching the sticky shift state, and both paths flash the preview bubble.
// Esc takes none of that (a preview bubble over Esc isn't "what it typed",
// and a special key has no shifted variant). The three callbacks are passed
// in as function properties because a component in this directory cannot see
// Main.qml's `root` id.
Item {
    id: numRow

    property real keyW: 48
    property real keyH: 36
    property real keySpacing: 2
    property color keyColor: "#333333"
    property color keyPressedColor: "#5a5a5a"
    property color keyTextColor: "#e0e0e0"
    property color accentColor: "#4a9eff"
    // Fill for the panel's Esc, matching the compact grid's other editing
    // keys (see Main.qml's `accentKeyColor` for why this is a blend rather
    // than the raw accent). Defaults to the ordinary key colour so the
    // component still looks right if a caller does not set it.
    property color accentKeyColor: keyColor
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
        { special: "escape", display: "Esc" },
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

    // A plain Row, NOT a RowLayout, and that is load-bearing. This is the
    // canonical copy of the rationale; the other panels point here.
    //
    // QtQuick.Layouts rounds every child up to a whole pixel, so 13 keys of
    // 69.23 px each became 13 of 70 and the row rendered 10 px wider than the
    // keyboard grid it is supposed to sit flush with, overhanging the window
    // and clipping its last key. The main keyboard rows are plain Rows for
    // the same reason: keyW is a float derived from the window width, and
    // every panel that has to line up with the keys underneath it must
    // consume that float identically. Applies to NavigationPanel (Grid) and
    // NumpadPanel (Column of Rows) as well, which reserve their own unit
    // budget in Main.qml's minimumWidth.
    Row {
        id: numLayout
        spacing: numRow.keySpacing

        Repeater {
            model: numRow.keyDefs

            KeyButton {
                id: numKey
                property var kd: modelData

                // Every key registers, Esc included, so the swipe overlay can
                // hit-test it. Esc registers as a *special*, which is what
                // keeps it out of the recogniser's key-centre map: an "Esc"
                // centre would be a phantom letter in every shape match. See
                // the two-registry note in Main.qml's registerCharKey, which
                // is where the type filter lives.
                Component.onCompleted: if (numRow.registerFn)
                    numRow.registerFn(numKey, kd.special
                        ? { type: "special", action: kd.special }
                        : { type: "char", key: kd.key })
                Component.onDestruction: if (numRow.unregisterFn)
                    numRow.unregisterFn(numKey)

                keyText: kd.special ? kd.display : kd.key
                displayText: kd.special ? kd.display
                                        : (numRow.shiftOn ? kd.shifted : kd.key)
                keyWidth: numRow.keyW
                keyHeight: numRow.keyH
                fontSize: kd.special ? 11 : 14
                isSpecial: !!kd.special
                // Digits must not auto-repeat, same as every other char key.
                // Esc opts out too: a repeating Esc on a slow release would
                // close a dialog and then whatever is behind it.
                enableRepeat: false
                // Esc is the only special key here, and it is one of the
                // editing keys the compact grid accents.
                keyColor: kd.special ? numRow.accentKeyColor : numRow.keyColor
                keyPressedColor: numRow.keyPressedColor
                keyTextColor: numRow.keyTextColor
                accentColor: numRow.accentColor
                borderColor: numRow.borderColor

                onKeyPressed: {
                    if (kd.special) {
                        // No preview bubble: a bubble over Esc isn't "what it
                        // typed", matching the main grid's special keys.
                        keyboard.pressSpecialKey(kd.special)
                        return
                    }
                    var ch = numRow.shiftOn ? kd.shifted : kd.key
                    keyboard.pressKey(ch)
                    if (numRow.keyPreviewEnabled && numRow.previewFn)
                        numRow.previewFn(numKey, ch)
                }

                // pressKeyLiteral, not pressKey: the latter lowercases and
                // would undo the variant we just resolved.
                onKeyRightPressed: {
                    // A special key has no shifted variant to resolve.
                    if (kd.special) return
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
