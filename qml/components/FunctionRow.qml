import QtQuick 2.15

Item {
    id: fnRow

    property real keyW: 48
    property real keyH: 36
    property real keySpacing: 2

    // The width this row has to fill, so it lines up with the keyboard grid
    // underneath it.  Set from Main.qml to the widest visible row's own
    // width; 0 means "size to content", which is what a caller that has no
    // grid to match gets.
    //
    // This is a *target*, not a scale factor, because the row cannot line up
    // by construction the way the Number Row does: that panel has one key per
    // grid column, and this one has 12 keys against 13 columns.  The leftover
    // column has to go somewhere, and putting it in the two group gaps keeps
    // every F-key exactly as wide as every other key on the keyboard.  The
    // alternative, stretching 12 keys across 13 columns, would make this the
    // one row whose keys do not line up with the row above it.
    property real rowWidth: 0

    property color keyColor: "#333333"
    property color keyPressedColor: "#5a5a5a"
    property color keyTextColor: "#e0e0e0"
    property color accentColor: "#4a9eff"
    property color borderColor: "#505050"

    // Wired to root.registerCharKey / unregisterCharKey in Main.qml.  Not
    // optional decoration: the swipe overlay covers this panel too and
    // hit-tests the registry to pass taps through, so before these were
    // wired up every F-key was a dead tap whenever Swipe Typing was on.
    // Same failure as issue #15, which was fixed for the main grid and the
    // Number Row and missed here.  They register as *specials*, so they
    // never reach the recogniser's key-centre map: an "F7" centre would be
    // a phantom letter in every shape match.
    property var registerFn: null
    property var unregisterFn: null

    readonly property var keyNames: [
        "F1", "F2", "F3", "F4", "F5", "F6",
        "F7", "F8", "F9", "F10", "F11", "F12"
    ]

    // **Twelve equal keys spanning the row, like every other row here.**
    //
    // This went through two wrong answers first, and both were wrong the
    // same way: they tried to keep each F-key exactly one grid column wide
    // and then find somewhere to put the leftover. A compact grid is 13
    // columns against these 12 keys and a full-size one is 15.5, so there
    // is always a remainder, and there is nowhere good to put it. Spending
    // it all on two group gaps gave 108 px chasms with the keys in
    // islands; capping the gap left the row centred and visibly inset
    // while every other row ran edge to edge, which is what it was
    // reported as the second time.
    //
    // The keyboard's actual convention is the answer: *rows span the
    // width, and key widths vary per row to make that work*. The Tab row
    // has a 1.5-unit Tab, the bottom row has a 5-unit space bar. So these
    // keys are simply (row - gaps) / 12 wide, which is about 72 px on
    // full-size against 56 px letters, and within a pixel of the letters
    // on compact. The 4-4-4 grouping is gone with it: no other row on this
    // keyboard groups, and grouping is what forced the remainder to pile
    // up in one place.
    readonly property real fnKeyW: rowWidth > 0
        ? Math.max(keyW, (rowWidth - 11 * keySpacing) / 12)
        : keyW

    implicitWidth: fnLayout.implicitWidth
    implicitHeight: fnLayout.implicitHeight

    // A plain Row, NOT a RowLayout: QtQuick.Layouts rounds every child up to
    // a whole pixel, which pushes the panel wider than the keyboard grid it
    // has to sit flush with. Full rationale in NumberRow.qml.
    Row {
        id: fnLayout
        spacing: fnRow.keySpacing

        Repeater {
            model: fnRow.keyNames

            KeyButton {
                id: fnKey

                // Same shape the layout-driven keys and the Number Row
                // carry, so the registry and the tests that read it see one
                // kind of key description, not two.
                readonly property var kd: ({
                    type: "special",
                    action: modelData.toLowerCase()
                })

                Component.onCompleted: if (fnRow.registerFn)
                    fnRow.registerFn(fnKey, fnKey.kd)
                Component.onDestruction: if (fnRow.unregisterFn)
                    fnRow.unregisterFn(fnKey)

                keyText: modelData.toLowerCase()
                displayText: modelData
                keyWidth: fnRow.fnKeyW
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
