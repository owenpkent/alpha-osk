import QtQuick

// A suggestion-bar button (or its title-bar twin) that an external switch
// scanner can reach through UI Automation.  The plumbing is in one place
// because five controls need it and a copy per control is how one of them
// ends up missing the visibility check at activation.
//
// Contract: docs/architecture/UIA_TARGETS.md, section `bar`.  The rules this
// mirrors from KeyButton:
//   * presence means activatable, and the same condition (`_scanActivatable`)
//     gates both the accessible presence and the Invoke;
//   * Invoke is a one-shot that goes through the same `activated` signal a
//     click does, carries no pointer position, and flashes the button;
//   * every accessible value is a named property and the `Accessible` block
//     below is a bare pass-through (PySide cannot read attached
//     `Accessible.*` properties, so logic written only there is untestable).
//
// The visuals stay with the caller (they differ per control); a caller's
// MouseArea calls `activated()` so click and Invoke share one handler.
Rectangle {
    id: barButton

    // The full AutomationId, built by the caller through
    // Main.qml::scanTargetId so the format exists once.
    property string targetId: ""
    // Speakable label.  Never the tooltip (tooltips carry state prose).
    property string scanName: ""
    // True only for a control that is genuinely a toggle (start/stop).
    property bool isToggleTarget: false
    property bool scanOn: false

    // Raised by a click and by an Invoke alike.
    signal activated()

    readonly property bool _scanWindowShown:
        Window.visibility !== Window.Minimized && Window.visibility !== Window.Hidden
    readonly property bool _scanActivatable:
        barButton._scanWindowShown && barButton.visible && barButton.enabled
    readonly property bool _scanIgnored: barButton.targetId === "" || !barButton._scanActivatable
    readonly property string _scanName: barButton.scanName
    readonly property bool _scanChecked: barButton.isToggleTarget && barButton.scanOn

    property bool _flash: false

    Accessible.role: Accessible.Button
    Accessible.ignored: barButton._scanIgnored
    Accessible.name: barButton._scanName
    Accessible.id: barButton.targetId
    Accessible.checkable: barButton.isToggleTarget
    Accessible.checked: barButton._scanChecked
    Accessible.onPressAction: barButton.activateFromAssistiveClient()

    // A switch user watches the keyboard, not the text field, so the flash is
    // the acknowledgement.  Not `activated()` directly: that would skip the
    // activatable check a held element needs.
    function activateFromAssistiveClient() {
        if (!barButton._scanActivatable)
            return
        barButton._flash = true
        flashTimer.restart()
        barButton.activated()
    }

    Rectangle {
        anchors.fill: parent
        radius: barButton.radius
        color: "white"
        opacity: barButton._flash ? 0.3 : 0
        visible: opacity > 0
        z: 50
    }

    Timer {
        id: flashTimer
        interval: 120
        repeat: false
        onTriggered: barButton._flash = false
    }
}
