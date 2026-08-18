import QtQuick 2.15

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

    readonly property var keyGroups: [
        ["F1", "F2", "F3", "F4"],
        ["F5", "F6", "F7", "F8"],
        ["F9", "F10", "F11", "F12"]
    ]

    // **The geometry here is deliberate and was chosen after trying the
    // alternatives on screen.**  Each F-key is exactly one grid column
    // wide and the row is centred, which leaves visible space at both ends
    // on a full-size layout, because the row is 12 keys against a grid of
    // 15.5 columns (13 on the compact layouts).
    //
    // That inset was reported as a bug and three different ways of filling
    // the width were built and rendered: spending the leftover on the two
    // group gaps (108 px chasms, the keys in islands), capping that gap
    // (still visibly inset, so it fixed nothing), and stretching all
    // twelve keys to fill (a third wider than the number key directly
    // below while staying 30% shorter, which reads as flat bars).
    //
    // Shown side by side, the original won: an F-key that is the same
    // width as the key under it belongs to the same keyboard, and the
    // empty space at the ends costs nothing.  **Do not "fix" the inset
    // again without rendering the result next to the number row.**  The
    // 4-4-4 grouping is part of that shape, not decoration.
    implicitWidth: fnLayout.implicitWidth
    implicitHeight: fnLayout.implicitHeight

    // A plain Row, NOT a RowLayout: QtQuick.Layouts rounds every child up to
    // a whole pixel, which pushes the panel wider than the keyboard grid it
    // has to sit flush with. Full rationale in NumberRow.qml.
    //
    // The 4-4-4 grouping is expressed as one KeyButton delegate reused by a
    // Repeater-of-Repeaters rather than three copies of the same ~30 lines:
    // the outer Row lays out the three group Rows, and its own `spacing` IS
    // the group gap, so there is no separate spacer Item to keep in sync
    // with it. That spacing is keySpacing * 4, not keySpacing * 2, because
    // the visible gap used to be the old spacer's own width (keySpacing * 2)
    // plus the ordinary Row spacing on either side of it (keySpacing each) -
    // the outer spacing here has to reproduce that whole width on its own,
    // since nothing sits between the two group Rows to contribute the rest.
    Row {
        id: fnLayout
        spacing: fnRow.keySpacing * 4

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
