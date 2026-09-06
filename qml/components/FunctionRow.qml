import QtQuick 2.15

// A row of function keys, 4-4-4 grouped.
//
// Instantiated twice by Main.qml: once for F1-F12 (the standard keys
// every app already binds) and once for F13-F24, which exist because
// almost nothing binds them and are therefore the natural home for a
// macro.  One component rather than two so the geometry argument below
// is stated once and cannot drift between the rows.
Item {
    id: fnRow

    property real keyW: 48
    property real keyH: 36
    property real keySpacing: 2
    // Each key's share of the gap around it; see KeyButton's
    // `hitMarginH`.  A key reaches half an ordinary gap past its own
    // slot, so the wider `keySpacing * 4` between the three groups keeps
    // a dead strip in the middle of it.  That is the same trade as the
    // gutter between two panels: a separator, not a gap nobody meant to
    // leave.
    property real hitMarginH: 0
    property real hitMarginV: 0
    property color keyColor: "#333333"
    property color keyPressedColor: "#5a5a5a"
    property color keyTextColor: "#e0e0e0"
    property color accentColor: "#4a9eff"
    property color borderColor: "#505050"

    // Which keys this row draws.  Settable so the same component serves
    // F1-F12 and F13-F24; the 4-4-4 shape is the row's, the contents are
    // the caller's.
    property var keyGroups: [
        ["F1", "F2", "F3", "F4"],
        ["F5", "F6", "F7", "F8"],
        ["F9", "F10", "F11", "F12"]
    ]

    // The bridge's whole assignment map, keyed by lowercase key id.
    // Passed as *data* rather than as a lookup function on purpose: the
    // keycap bindings below then re-evaluate on their own when Main.qml
    // reassigns the map after an edit.  A function property would have
    // needed a revision counter threaded through every binding to force
    // the same thing, which is state that can go stale.
    property var actions: ({})

    // editFn(keyId) -> open the action editor for that key.
    property var editFn: null

    // **This row has no left-click route into the editor, on purpose.**
    //
    // It used to carry an Edit toggle that put every key into "tap to
    // program" mode, because right-click is unreachable for a dwell-click,
    // switch-access, head- or eye-tracker pointer and for a single-button
    // adaptive mouse, and without a second route such a user could press
    // an F-key and never program one.  That argument still holds; what
    // changed is where it is answered.  *Settings -> Function Keys* lists
    // all twenty-four with what each one does and opens the editor on a
    // tap, which is a bigger target than any key on this row, needs no
    // mode to get into or out of, and is the only surface that shows an
    // assignment you have forgotten.  **Do not put a mode toggle back on
    // the row without first checking that page is gone**, or the row pays
    // a key's width for a duplicate.

    function _actionFor(name) {
        var a = fnRow.actions ? fnRow.actions[name.toLowerCase()] : undefined
        return a ? a : null
    }

    // A programmed key reads "Save" rather than "F17", which is the whole
    // point of programming it: twelve identical F-keys cannot be told
    // apart on screen, so a macro nobody can find is a macro nobody uses.
    function _capFor(name) {
        var a = fnRow._actionFor(name)
        return (a && a.label) ? a.label : name
    }

    // "Carries an action" is not "has an entry": the `key` type exists so
    // a key can keep its own keystroke and only take a custom label (for
    // one the user has bound inside another app), and marking that as
    // reassigned would be a lie about what tapping it does.
    function _isProgrammed(name) {
        var a = fnRow._actionFor(name)
        return !!a && a.type !== "key"
    }

    // The width this row fills: the keyboard grid it sits above.  0
    // means unwired (a test that builds this component on its own), and
    // falls back to one grid column per key.  It is not an upper bound
    // the row happens to stay under, it is the width the keys are sized
    // from; see the geometry note below.
    property real maxWidth: 0

    readonly property int _keyCount: {
        var n = 0
        for (var i = 0; i < keyGroups.length; ++i)
            n += keyGroups[i].length
        return n
    }
    // Gaps *inside* the groups (three per group of four), and the gaps
    // *between* them, which are the outer Row's own spacing: one fewer
    // than there are groups.
    readonly property int _withinGaps: _keyCount - keyGroups.length
    readonly property int _groupGaps: Math.max(0, keyGroups.length - 1)

    // **The group gap never gives.  The keys do.**
    //
    // It used to be the other way round, and that is what erased the
    // 4-4-4 shape on compact: with the Edit toggle in the run this was 13
    // keys against a 13-unit grid, 3 px of slack, so the gap clamped to
    // `keySpacing` and twelve identical keys rendered as one
    // undifferentiated run, on the view where telling them apart matters
    // most.  The toggle has since moved to Settings and the row is twelve
    // keys again, but the rule stays: a gap that gives is a gap that
    // disappears exactly when the row is tightest.
    readonly property real _groupGap: keySpacing * 4

    // The rendered width of every key in this row.  The row spans the
    // keyboard grid exactly, so what used to sit as empty margin at both
    // ends is divided between the twelve keys instead.
    //
    // `maxWidth <= 0` means no caller wired the grid width (a test that
    // builds this component on its own), and falls back to one grid
    // column per key, which is what this row drew before.
    readonly property real _fillKeyW: {
        if (maxWidth <= 0)
            return keyW
        var w = (maxWidth - _withinGaps * keySpacing - _groupGaps * _groupGap) / _keyCount
        return w > 0 ? w : keyW
    }

    function _activate(name) {
        keyboard.pressSpecialKey(name.toLowerCase())
    }

    // **The row fills the keyboard grid, and that reverses an earlier
    // decision on purpose.**
    //
    // It used to draw each F-key exactly one grid column wide and centre
    // the result, which left a visible margin at both ends: twelve keys
    // against a 15.5-column grid, so most of two key widths a side at a
    // 940 px window.  That shape was chosen
    // the first time this came up, over three ways of filling the width
    // (spending the leftover on the group gaps, which made 108 px chasms;
    // capping that gap, which left it visibly inset and so fixed nothing;
    // and stretching the keys).  The note left behind said not to revisit
    // the inset without rendering the result next to the number row.
    //
    // That is exactly what was done the second time, and stretching won.
    // Every key gains 18% of target width, and on a keyboard driven by an
    // imprecise pointer that outranks the alignment argument; the panel
    // also reads as part of the keyboard's outline rather than a strip
    // floating above it.
    //
    // **The cost was accepted with the picture in front of us**: at full
    // size an F-key is 18% wider than the key directly below it while
    // staying 30% shorter, so the row no longer lines up with the grid
    // column by column.  Do not "fix" that back without rendering it next
    // to the number row, which is the same rule as before, pointing the
    // other way.
    //
    // The invariant that survived both decisions: this row must never be
    // wider than the grid it sits above.  It is now exactly that width,
    // so anything that adds a key or widens a gap has to come out of
    // `_fillKeyW` and never out of `maxWidth`.
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
        spacing: fnRow._groupGap

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

                        objectName: "fnKey_" + modelData.toLowerCase()

                        keyText: modelData.toLowerCase()
                        displayText: fnRow._capFor(modelData)
                        keyWidth: fnRow._fillKeyW
                        keyHeight: fnRow.keyH
                        hitMarginH: fnRow.hitMarginH
                        hitMarginV: fnRow.hitMarginV
                        // A custom label is a word, not a two-character key
                        // name, so a long one takes the smaller size rather
                        // than overflowing the cap.  The store caps the label
                        // at 12 characters for the same reason.  The
                        // threshold is 8 rather than 3 because the fill above
                        // bought 18% more width: at 10 px eight characters
                        // clear the cap and twelve do not, so only the
                        // longest labels still step down.
                        fontSize: displayText.length > 8 ? 9 : 10
                        isSpecial: true
                        enableRepeat: false
                        // A reassigned key is marked, so the user can see
                        // at a glance which keys no longer send what their
                        // cap used to say.
                        isActive: fnRow._isProgrammed(modelData)
                        keyColor: fnRow.keyColor
                        keyPressedColor: fnRow.keyPressedColor
                        keyTextColor: fnRow.keyTextColor
                        accentColor: fnRow.accentColor
                        borderColor: fnRow.borderColor
                        onKeyPressed: fnRow._activate(modelData)
                        // The fast route for a pointer that can right-click.
                        // Never the only route: *Settings -> Function Keys*
                        // is the one that needs no right button.  See the
                        // note at the top of this file.
                        onKeyRightPressed: if (fnRow.editFn)
                            fnRow.editFn(modelData.toLowerCase())
                    }
                }
            }
        }
    }
}
