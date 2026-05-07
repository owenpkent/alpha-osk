import QtQuick 2.15
import QtQuick.Controls 2.15

Item {
    id: keyRoot

    // Public properties
    property string keyText: ""
    property string keyTextShifted: ""
    property string displayText: keyText
    property real keyWidth: 60
    property real keyHeight: 54
    property color keyColor: "#3a3a3a"
    property color keyPressedColor: "#5a5a5a"
    property color keyTextColor: "#e0e0e0"
    property color accentColor: "#4a9eff"
    property color borderColor: "#505050"
    property real fontSize: 16
    property real radius: 8
    property bool isSpecial: false
    property bool isActive: false  // For modifier keys (shift, ctrl, etc.)
    property bool isWide: false

    // Key repeat settings.  Default OFF — only callers that clearly
    // benefit from auto-repeat (backspace, arrow keys, delete, page
    // up/down) opt in.  Character keys do NOT repeat on this OSK: a
    // slightly-slow mouse click past the 400 ms threshold would fire
    // the character twice, and "type 'aaaa' by holding the button" is
    // not a real use case for mouse-driven typing.
    property bool enableRepeat: false
    property int repeatDelay: 500    // ms before repeat starts
    property int repeatInterval: 120 // ms between repeats (~8/sec — slow
                                     // enough that a slightly-too-long
                                     // press doesn't blast through extra
                                     // chars, fast enough to bulk-delete
                                     // a word in ~1 s)
    property int warmUpGrace: 300    // ms between the warm-up tick at
                                     // ``repeatDelay`` and the first
                                     // actual auto-repeat keystroke.
                                     // Widens the "1 vs 2 keystrokes"
                                     // boundary from
                                     // ``repeatDelay + repeatInterval``
                                     // (~620 ms) to
                                     // ``repeatDelay + warmUpGrace``
                                     // (~800 ms) so a slightly-too-long
                                     // tap on backspace doesn't fire
                                     // a second emit.  Once the user
                                     // is genuinely holding past the
                                     // grace, auto-repeat kicks in at
                                     // ``repeatInterval`` cadence as
                                     // before.

    // Debounce window (ms).  Consecutive MouseArea presses within this
    // window count as a single press — covers hardware button bounce
    // (cheap / worn mice emit two events per physical click) and
    // accidental double-clicks from slow motor control.  150 ms is
    // well below any deliberate rapid-click cadence but well above any
    // plausible bounce interval.
    property int debounceMs: 150
    property real _lastAcceptedPress: 0

    // Explicit pressed-state tracking — do NOT bind visuals directly to
    // mouseArea.pressed.  On Windows the OSK has WS_EX_NOACTIVATE, and
    // when the user drags off a key onto another app's window Qt
    // occasionally never sees the release event, leaving pressed=true
    // and the key visibly latched down.  We drive visuals off this
    // property instead and clear it on release, cancel, drag-off, AND
    // a safety timeout so a missed event can't strand the key visually.
    property bool _visualPressed: false

    // Signals
    signal keyPressed()
    // Right-click — emitted on right mouse button.  Caller decides what
    // to do (typically: type the shifted variant of this key without
    // flipping the sticky shift state).  Press visuals + ripple still
    // fire so the user gets the same tactile feedback as a left-click,
    // but the auto-repeat timer never starts — right-click is a
    // deliberate one-shot, not a hold.
    signal keyRightPressed()

    width: keyWidth
    height: keyHeight
    // implicitWidth/Height are what Qt Quick Layouts (RowLayout, GridLayout)
    // use for size allocation.  Without these, layouts see 0×0 and keys overflow.
    implicitWidth: keyWidth
    implicitHeight: keyHeight

    // Key repeat timer.  Three phases per hold cycle:
    //   phase 0 (pre-warmup): scheduled at ``repeatDelay``.  When it
    //                         fires, transition to phase 1.  Does NOT
    //                         emit a keystroke.
    //   phase 1 (grace):      scheduled at ``warmUpGrace``.  When it
    //                         fires, emit the first auto-repeat
    //                         keystroke and transition to phase 2.
    //   phase 2 (repeating):  scheduled at ``repeatInterval`` cadence,
    //                         emit each tick.
    //
    // The grace phase exists because phase 0 alone left a 120 ms
    // boundary between "one tap" and "tap that fires twice".  Slow-
    // motor users systematically tipped past it on backspace and felt
    // it as "Backspace sometimes sends 2".  Adding the grace widens
    // the boundary from ``repeatDelay + repeatInterval`` (~620 ms) to
    // ``repeatDelay + warmUpGrace`` (~800 ms) without slowing down
    // bulk-delete once auto-repeat is genuinely engaged.
    //
    // Any press shorter than ``repeatDelay + warmUpGrace`` gives
    // exactly one keystroke.  ``phase`` must be reset to 0 wherever
    // the timer is stopped (``onReleased``, ``onCanceled``,
    // ``onContainsMouseChanged``); otherwise a subsequent press would
    // skip the warm-up and resume mid-cycle.
    Timer {
        id: repeatTimer
        interval: keyRoot.repeatDelay
        repeat: false
        property int phase: 0
        onTriggered: {
            if (phase === 0) {
                phase = 1
                interval = keyRoot.warmUpGrace
                repeat = false
                start()
                pressSafetyTimer.restart()
                return
            }
            keyRoot.keyPressed()
            // Push the safety deadline forward.  The safety timer only
            // exists to recover from a *stranded* press (Qt dropped the
            // release under WS_EX_NOACTIVATE) — but if repeats are
            // firing, the press is genuinely held, not stranded.  Reset
            // each tick so a long-held key (e.g. backspace deleting a
            // paragraph) doesn't get cut off mid-hold.
            pressSafetyTimer.restart()
            if (phase === 1) {
                phase = 2
                interval = keyRoot.repeatInterval
                repeat = true
                start()
            }
        }
    }

    // Final safety net for stuck visuals — even with the explicit clear
    // paths in the MouseArea handlers below, force the key back to its
    // resting state after 5 s of inactivity.  Restarted on each repeat
    // tick so it only fires when nothing is happening.
    Timer {
        id: pressSafetyTimer
        interval: 5000
        repeat: false
        onTriggered: {
            keyRoot._visualPressed = false
            repeatTimer.stop()
        }
    }

    Rectangle {
        id: keyBackground
        anchors.fill: parent
        anchors.margins: 2
        radius: keyRoot.radius
        clip: true
        color: keyRoot._visualPressed ? keyPressedColor
             : isActive ? accentColor
             : mouseArea.containsMouse ? Qt.lighter(keyColor, 1.25)
             : keyColor

        border.color: isActive ? Qt.lighter(accentColor, 1.3)
                    : mouseArea.containsMouse ? Qt.lighter(borderColor, 1.4)
                    : borderColor
        border.width: 1

        // Subtle gradient overlay — enhanced depth on press
        Rectangle {
            anchors.fill: parent
            anchors.margins: 1
            radius: parent.radius - 1
            gradient: Gradient {
                GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, keyRoot._visualPressed ? 0.02 : 0.06) }
                GradientStop { position: 1.0; color: Qt.rgba(0, 0, 0, keyRoot._visualPressed ? 0.14 : 0.08) }
            }
        }

        // Ripple effect on press
        Rectangle {
            id: ripple
            property real centerX: 0
            property real centerY: 0
            x: centerX - width / 2
            y: centerY - height / 2
            width: 0
            height: width
            radius: width / 2
            color: Qt.rgba(1, 1, 1, 0.15)
            opacity: 0

            ParallelAnimation {
                id: rippleAnim
                NumberAnimation {
                    target: ripple; property: "width"
                    from: 0; to: keyBackground.width * 2
                    duration: 300; easing.type: Easing.OutQuad
                }
                NumberAnimation {
                    target: ripple; property: "opacity"
                    from: 0.3; to: 0
                    duration: 300; easing.type: Easing.OutQuad
                }
            }
        }

        // Key label
        Text {
            anchors.centerIn: parent
            text: keyRoot.displayText
            // Ensure readable contrast: use dark text on bright backgrounds, white on dark
            color: {
                var bg = keyRoot._visualPressed ? keyPressedColor : isActive ? accentColor : keyColor
                // Luminance approximation: bright backgrounds need dark text
                var lum = bg.r * 0.299 + bg.g * 0.587 + bg.b * 0.114
                if (keyRoot._visualPressed || isActive) {
                    return lum > 0.5 ? "#111111" : "#ffffff"
                }
                return keyTextColor
            }
            font.pixelSize: keyRoot.fontSize
            font.family: "Segoe UI, Inter, Ubuntu, Noto Sans, sans-serif"
            font.weight: isSpecial ? Font.DemiBold : Font.DemiBold
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }

        // Smooth color transition
        Behavior on color {
            ColorAnimation { duration: 120 }
        }

        // Scale animation on press — slight bounce for tactile feel
        transform: Scale {
            id: scaleTransform
            origin.x: keyBackground.width / 2
            origin.y: keyBackground.height / 2
            xScale: keyRoot._visualPressed ? 0.94 : 1.0
            yScale: keyRoot._visualPressed ? 0.94 : 1.0
            Behavior on xScale { NumberAnimation { duration: 100; easing.type: Easing.OutBack; easing.overshoot: 1.2 } }
            Behavior on yScale { NumberAnimation { duration: 100; easing.type: Easing.OutBack; easing.overshoot: 1.2 } }
        }
    }

    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton

        onPressed: function(mouse) {
            // Debounce: drop any second press within debounceMs of the
            // previous accepted one.  Catches hardware bounce and
            // accidental double-clicks without affecting deliberate
            // typing (150 ms is well under a human's repeat cadence).
            var now = Date.now()
            if (now - keyRoot._lastAcceptedPress < keyRoot.debounceMs) {
                mouse.accepted = true
                return
            }
            keyRoot._lastAcceptedPress = now

            // Visual press is driven explicitly so a missed release
            // can't strand the key looking pressed-down (see the
            // _visualPressed comment up top).
            keyRoot._visualPressed = true
            pressSafetyTimer.restart()

            // Trigger ripple from press point
            ripple.centerX = mouse.x - keyBackground.anchors.margins
            ripple.centerY = mouse.y - keyBackground.anchors.margins
            rippleAnim.stop()
            ripple.width = 0
            ripple.opacity = 0
            rippleAnim.start()

            if (mouse.button === Qt.RightButton) {
                // Right-click is a one-shot — never auto-repeats, and
                // the caller decides what (if anything) to type.
                keyRoot.keyRightPressed()
                return
            }

            keyRoot.keyPressed()
            // Enable repeat based on enableRepeat property (not isSpecial)
            // Backspace, Delete, Arrow keys should repeat
            if (keyRoot.enableRepeat) {
                repeatTimer.interval = keyRoot.repeatDelay
                repeatTimer.repeat = false
                repeatTimer.start()
            }
        }

        onReleased: {
            keyRoot._visualPressed = false
            pressSafetyTimer.stop()
            repeatTimer.stop()
            repeatTimer.interval = keyRoot.repeatDelay
            repeatTimer.repeat = false
            repeatTimer.phase = 0
        }

        onCanceled: {
            keyRoot._visualPressed = false
            pressSafetyTimer.stop()
            repeatTimer.stop()
            repeatTimer.interval = keyRoot.repeatDelay
            repeatTimer.repeat = false
            repeatTimer.phase = 0
        }

        // Cursor leaving the key clears the visual press AND stops
        // repeat — covers two cases: (1) the user dragged off to abort
        // the keypress, (2) Qt never delivered the release because the
        // cursor went onto another app's window (WS_EX_NOACTIVATE
        // sometimes drops that event).  Either way, the key's no longer
        // being interacted with, so it shouldn't look held down.
        onContainsMouseChanged: {
            if (!containsMouse) {
                keyRoot._visualPressed = false
                repeatTimer.stop()
                repeatTimer.interval = keyRoot.repeatDelay
                repeatTimer.repeat = false
                repeatTimer.phase = 0
            }
        }
    }
}
