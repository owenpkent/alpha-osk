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

    readonly property var keyGroups: [
        ["F1", "F2", "F3", "F4"],
        ["F5", "F6", "F7", "F8"],
        ["F9", "F10", "F11", "F12"]
    ]

    // 12 keys in 3 groups: 9 gaps of keySpacing inside the groups, and the
    // 2 gaps between them absorb what is left of the target width.
    //
    // **Capped at one key width, and the cap is the point.** How much is
    // left over depends entirely on the layout: a compact grid is 13
    // columns against these 12 keys, so the leftover is about one key and
    // the gaps land near 34 px, which reads as the grouping a function row
    // has on any keyboard. A full-size grid is 15.5 columns, so the
    // leftover is three and a half keys, and splitting that in two gave
    // two 108 px chasms with the keys bunched into islands. Past the cap
    // the row simply stops growing and stays centred, which is what it did
    // before it filled the width at all.
    //
    // Floored at keySpacing so a window narrow enough to make the leftover
    // negative degrades to a normal gap instead of overlapping keys.
    readonly property real groupGap: rowWidth > 0
        ? Math.min(keyW, Math.max(keySpacing, (rowWidth - 12 * keyW - 9 * keySpacing) / 2))
        : keySpacing * 3

    implicitWidth: fnLayout.implicitWidth
    implicitHeight: fnLayout.implicitHeight

    // A plain Row, NOT a RowLayout: QtQuick.Layouts rounds every child up to
    // a whole pixel, which pushes the panel wider than the keyboard grid it
    // has to sit flush with. Full rationale in NumberRow.qml.
    Row {
        id: fnLayout
        spacing: fnRow.groupGap

        Repeater {
            model: fnRow.keyGroups

            Row {
                spacing: fnRow.keySpacing

                Repeater {
                    model: modelData

                    KeyButton {
                        id: fnKey

                        // Same shape the layout-driven keys and the Number
                        // Row carry, so the registry and the tests that read
                        // it see one kind of key description, not two.
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
    }
}
