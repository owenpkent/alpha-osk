import QtQuick 2.15

// The game controller, drawn where the letter grid normally is.
//
// Docs: docs/architecture/GAMEPAD.md.  It takes exactly the grid's width and
// height (Main.qml sizes it), so showing it never changes the window.
//
// Geometry.  The controls sit on a 15.5 by 5 unit design grid, whatever the
// real grid is; in compact view the rectangle is 13 units wide with four rows
// and the design simply scales into it.  A control at design (x, y, w, h)
// is placed at x * (W + s) / 15.5 with width w * (W + s) / 15.5 - s (the
// same for y with the row gap), which puts the outermost key edges exactly on
// the view's edges, flush with the letter keys it replaced, and makes every
// gap between neighbours equal to the grid's own.  A plain Item with computed
// positions, never QtQuick.Layouts (which rounds to whole pixels).
//
// This component only raises signals.  Main.qml owns the `gamepad` context
// property, as it does for every other bridge, so the panel stays testable
// and a pad control never touches `keyboard` (sticky modifiers, predictions).
Item {
    id: view

    // Scan-target identity, set by Main.qml (see FunctionRow.qml).
    property string scanSection: ""
    property var scanIdFor: null
    function _scanId(index) {
        return (scanIdFor && scanSection !== "") ? scanIdFor(scanSection, 0, index) : ""
    }

    property real keySpacing: 2
    property real rowSpacing: 2
    property real hitMarginH: 0
    property real hitMarginV: 0
    property color keyColor: "#333333"
    property color enterKeyColor: "#333333"
    property color keyPressedColor: "#5a5a5a"
    property color keyTextColor: "#e0e0e0"
    property color accentColor: "#4a9eff"
    property color borderColor: "#505050"
    property var roleColors: null

    // Ids of the controls latched right now (`gamepad.latched`).
    property var latched: []

    // A press ending is the one thing that must never be lost, so the view
    // remembers what it has pressed and lets go of all of it if it is hidden
    // or torn down mid-press.
    property var _down: ({})

    signal padPressed(string controlId)
    signal padReleased(string controlId)
    // An assistive-technology Invoke: one press and one release, owned by the
    // bridge (it enforces the minimum hold), never a press with no end.
    signal padTapped(string controlId)
    signal padLatchToggled(string controlId)
    signal lettersRequested()
    signal offRequested()
    signal keyRequested(string action)

    function releaseHeld() {
        var held = view._down
        view._down = ({})
        for (var id in held)
            view.padReleased(id)
    }
    onVisibleChanged: if (!visible) view.releaseHeld()
    Component.onDestruction: view.releaseHeld()

    // kind: "pad" | "letters" | "off" | "key".  `scan` is the fixed index in
    // the scan section: append, never renumber (docs/architecture/UIA_TARGETS.md).
    // Arrow controls carry `angle` (degrees clockwise from up) and are drawn as
    // a stroked arrow, never a glyph: the diagonal arrows are in a range
    // Windows can resolve through the colour emoji font.
    readonly property var _controls: {
        var out = []
        function add(id, label, spoken, x, y, w, h, role, scan, extra) {
            var d = { id: id, label: label, spoken: spoken, x: x, y: y, w: w, h: h,
                      role: role, scan: scan, kind: "pad", angle: -1, action: "" }
            for (var k in extra) d[k] = extra[k]
            out.push(d)
        }
        var compass = [
            ["nw", "up left", 315], ["n", "up", 0], ["ne", "up right", 45],
            ["w", "left", 270], null, ["e", "right", 90],
            ["sw", "down left", 225], ["s", "down", 180], ["se", "down right", 135]
        ]
        function stick(prefix, name, x0, firstScan, clickId, clickLabel) {
            var cell = 4 / 3
            for (var i = 0; i < 9; ++i) {
                var cx = x0 + (i % 3) * cell
                var cy = 1 + Math.floor(i / 3) * cell
                if (compass[i] === null) {
                    add(clickId, clickLabel, name + " stick click", cx, cy, cell, cell,
                        "fn", firstScan + i, {})
                } else {
                    add(prefix + "_" + compass[i][0], "", name + " stick " + compass[i][1],
                        cx, cy, cell, cell, "nav", firstScan + i, { angle: compass[i][2] })
                }
            }
        }
        add("lt", "LT", "Left trigger", 0, 0, 2, 1, "fn", 0, {})
        add("lb", "LB", "Left bumper", 2, 0, 2, 1, "fn", 1, {})
        add("rb", "RB", "Right bumper", 11.5, 0, 2, 1, "fn", 2, {})
        add("rt", "RT", "Right trigger", 13.5, 0, 2, 1, "fn", 3, {})
        add("back", "Back", "Back button", 5.25, 0, 1.5, 1, "fn", 4, {})
        add("guide", "Guide", "Guide button", 7, 1, 1.5, 1, "fn", 5, {})
        add("start", "Start", "Start button", 8.75, 0, 1.5, 1, "fn", 6, {})
        add("off", "Off", "Controller off", 4.25, 1, 1.5, 1, "edit", 7, { kind: "off" })
        add("abc", "ABC", "Letters", 7, 0, 1.5, 1, "edit", 8, { kind: "letters" })
        stick("ls", "Left", 0, 9, "l3", "L3")
        stick("rs", "Right", 11.5, 18, "r3", "R3")
        add("dpad_up", "", "D-pad up", 5.25, 2, 1, 1, "nav", 27, { angle: 0 })
        add("dpad_left", "", "D-pad left", 4.25, 3, 1, 1, "nav", 28, { angle: 270 })
        add("dpad_right", "", "D-pad right", 6.25, 3, 1, 1, "nav", 29, { angle: 90 })
        add("dpad_down", "", "D-pad down", 5.25, 4, 1, 1, "nav", 30, { angle: 180 })
        add("y", "Y", "Y button", 9.25, 2, 1, 1, "op", 31, {})
        add("x", "X", "X button", 8.25, 3, 1, 1, "op", 32, {})
        add("b", "B", "B button", 10.25, 3, 1, 1, "op", 33, {})
        add("a", "A", "A button", 9.25, 4, 1, 1, "op", 34, {})
        add("esc", "Esc", "Escape", 7.25, 3, 1, 1, "edit", 35, { kind: "key", action: "escape" })
        add("enter", "Enter", "Enter", 7.25, 4, 1, 1, "commit", 36, { kind: "key", action: "return" })
        return out
    }

    // How many design units the view spans horizontally and vertically.
    readonly property real _unitsX: 15.5
    readonly property real _unitsY: 5
    readonly property real _pitchX: (view.width + view.keySpacing) / view._unitsX
    readonly property real _pitchY: (view.height + view.rowSpacing) / view._unitsY

    Repeater {
        model: view._controls

        KeyButton {
            id: padKey
            property var cd: modelData

            objectName: "padKey_" + cd.id
            keyText: cd.id
            displayText: cd.label
            scanLabel: cd.spoken
            x: cd.x * view._pitchX
            y: cd.y * view._pitchY
            keyWidth: Math.max(1, cd.w * view._pitchX - view.keySpacing)
            keyHeight: Math.max(1, cd.h * view._pitchY - view.rowSpacing)
            hitMarginH: view.hitMarginH
            hitMarginV: view.hitMarginV
            fontSize: Math.max(9, Math.min(15, Math.floor(
                Math.min(keyHeight * 0.34, 1.35 * keyWidth / Math.max(2, cd.label.length)))))
            isSpecial: true
            enableRepeat: false
            targetId: view._scanId(cd.scan)
            // A pad control is "on" while latched; that is a real toggle state
            // for a scanner, unlike the F-key's reassigned accent.
            readonly property bool latchedNow: cd.kind === "pad" && view.latched.indexOf(cd.id) >= 0
            isToggleTarget: cd.kind === "pad"
            isActive: latchedNow
            isLocked: latchedNow
            role: cd.role
            roleColors: view.roleColors
            keyColor: cd.kind === "key" && cd.action === "return" ? view.enterKeyColor
                                                                  : view.keyColor
            keyPressedColor: view.keyPressedColor
            keyTextColor: view.keyTextColor
            accentColor: view.accentColor
            borderColor: view.borderColor

            onKeyPressed: {
                if (cd.kind === "pad") {
                    // No pointer sample means an Invoke: it has no release to
                    // pair with, so it is one tap rather than a held press.
                    if (!padKey.pressFromPointer) {
                        view.padTapped(cd.id)
                    } else {
                        var down = view._down
                        down[cd.id] = true
                        view._down = down
                        view.padPressed(cd.id)
                    }
                } else if (cd.kind === "letters") {
                    view.lettersRequested()
                } else if (cd.kind === "off") {
                    view.offRequested()
                } else {
                    view.keyRequested(cd.action)
                }
            }
            // Right-click latches, the way it locks a modifier.
            onKeyRightPressed: {
                if (cd.kind === "pad")
                    view.padLatchToggled(cd.id)
            }
            // Fires from every way a press ends (release, cancel, drag off), and
            // also after a right-click, which pressed nothing: only a control
            // this view pressed is released.
            onKeyReleased: {
                if (cd.kind === "pad" && view._down[cd.id]) {
                    var down = view._down
                    delete down[cd.id]
                    view._down = down
                    view.padReleased(cd.id)
                }
            }

            StrokeIcon {
                objectName: "padArrow"
                visible: cd.angle >= 0
                anchors.centerIn: parent
                width: Math.min(parent.width, parent.height) * 0.5
                height: width
                rotation: cd.angle < 0 ? 0 : cd.angle
                paths: ["M12 19 L12 5", "M5 12 L12 5 L19 12"]
                ink: (padKey._visualPressed || padKey.isActive) ? padKey._onFillColor
                                                                : padKey._roleInk
            }
        }
    }
}
