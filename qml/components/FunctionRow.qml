import QtQuick 2.15

// A row of function keys, 4-4-4 grouped, plus the toggle that puts the
// row into assign mode.
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

    // Assign mode is the **left-click route into the editor**, and it is
    // not a convenience duplicate of the right-click one.  Right-click is
    // unreachable for a dwell-click, switch-access, head- or eye-tracker
    // pointer, and for a single-button adaptive mouse: without this, such
    // a user could press an F-key and never program one.  That is the
    // same reachability regression the snippets grid documents, which is
    // why this mirrors its Manage mode rather than inventing a gesture.
    //
    // Owned by Main.qml (both rows share one mode), so the toggle emits
    // rather than assigning: writing to the property here would break the
    // binding that keeps the two rows agreeing.
    property bool assignMode: false
    signal assignToggled()

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

    // The width this row must not exceed: the keyboard grid it sits
    // above.  0 means unconstrained (tests and any caller that has not
    // wired it).  Only the group gap gives, never a key width, which is
    // what keeps the geometry argument below true.
    property real maxWidth: 0

    readonly property int _keyCount: {
        var n = 0
        for (var i = 0; i < keyGroups.length; ++i)
            n += keyGroups[i].length
        return n + 1  // + the assign toggle
    }
    // Gaps *inside* the groups; the gaps *between* them are the outer
    // Row's spacing, one per group (three groups, three gaps: two
    // between the groups and one before the toggle).
    readonly property int _withinGaps: _keyCount - 1 - keyGroups.length

    // The 4-4-4 group gap is the first thing to give, and the only thing.
    //
    // Adding the assign toggle made this row 13 keys wide, which is
    // exactly the compact grid's 13 units: the keys themselves fit, and
    // it was the group gaps that pushed the panel past the keyboard
    // underneath it.  Compressing them down to the ordinary key spacing
    // lands the row flush with the grid on compact (where the groups
    // merge into one continuous run, which reads fine against a uniform
    // 13-key grid) while leaving the full-size layouts untouched: there
    // the slack is most of a key width, so the clamp never bites and the
    // 4-4-4 shape is exactly what it was.
    readonly property real _groupGap: {
        if (maxWidth <= 0)
            return keySpacing * 4
        var slack = maxWidth - (_keyCount * keyW + _withinGaps * keySpacing)
        return Math.max(keySpacing,
                        Math.min(keySpacing * 4, slack / Math.max(1, keyGroups.length)))
    }

    function _activate(name) {
        if (fnRow.assignMode) {
            if (fnRow.editFn)
                fnRow.editFn(name.toLowerCase())
            return
        }
        keyboard.pressSpecialKey(name.toLowerCase())
    }

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
    //
    // The assign toggle spends some of that leftover space rather than
    // changing any key's width, which is what keeps the argument above
    // intact: the F-keys still line up with the grid below.
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

                        Component.onCompleted: if (fnRow.registerFn)
                            fnRow.registerFn(fnKey, fnKey.kd)
                        Component.onDestruction: if (fnRow.unregisterFn)
                            fnRow.unregisterFn(fnKey)

                        keyText: modelData.toLowerCase()
                        displayText: fnRow._capFor(modelData)
                        keyWidth: fnRow.keyW
                        keyHeight: fnRow.keyH
                        // A custom label is a word, not a two-character key
                        // name, so it takes the smaller size rather than
                        // overflowing the cap.  The store caps the label at
                        // 12 characters for the same reason.
                        fontSize: displayText.length > 3 ? 8 : 10
                        isSpecial: true
                        enableRepeat: false
                        // In assign mode every key is a target for the
                        // editor, so the whole row takes the accent the
                        // snippets grid uses for the same state.  Outside
                        // it, only a reassigned key is marked, so the user
                        // can see at a glance which keys no longer send
                        // what their cap used to say.
                        isActive: fnRow.assignMode || fnRow._isProgrammed(modelData)
                        keyColor: fnRow.keyColor
                        keyPressedColor: fnRow.keyPressedColor
                        keyTextColor: fnRow.keyTextColor
                        accentColor: fnRow.accentColor
                        borderColor: fnRow.borderColor
                        onKeyPressed: fnRow._activate(modelData)
                        // The fast route for a pointer that can right-click.
                        // Never the only route: see `assignMode` above.
                        onKeyRightPressed: if (fnRow.editFn)
                            fnRow.editFn(modelData.toLowerCase())
                    }
                }
            }
        }

        // The assign toggle.  One key-width, at the end of the row, drawn
        // through KeyButton so it debounces, flashes and sizes exactly
        // like the keys beside it rather than being a second kind of
        // target for an imprecise pointer to learn.  Its width does not
        // follow its label (Edit / Done are both four characters, but the
        // rule matters anyway): a control that resizes under a pointer
        // already travelling toward it is the trap the snippets Manage
        // button documents.
        KeyButton {
            id: assignToggle
            objectName: "fnAssignToggle"

            // Registered like every other key under the swipe overlay.
            // Not optional: the overlay takes every press inside its
            // bounds and resolves it against this registry, so a control
            // that is not in it is a dead tap whenever Swipe Typing is on.
            // That is issue #15, which was fixed for the main grid, the
            // Number Row and these F-keys and would have been reintroduced
            // by the one new key in the row.  It registers as a *special*
            // so it never reaches the recogniser's key-centre map.
            readonly property var kd: ({ type: "special", action: "assign" })
            Component.onCompleted: if (fnRow.registerFn)
                fnRow.registerFn(assignToggle, assignToggle.kd)
            Component.onDestruction: if (fnRow.unregisterFn)
                fnRow.unregisterFn(assignToggle)

            keyText: "assign"
            displayText: fnRow.assignMode ? "Done" : "Edit"
            keyWidth: fnRow.keyW
            keyHeight: fnRow.keyH
            fontSize: 8
            isSpecial: true
            enableRepeat: false
            isActive: fnRow.assignMode
            keyColor: fnRow.keyColor
            keyPressedColor: fnRow.keyPressedColor
            keyTextColor: fnRow.keyTextColor
            accentColor: fnRow.accentColor
            borderColor: fnRow.borderColor
            onKeyPressed: fnRow.assignToggled()
        }
    }
}
