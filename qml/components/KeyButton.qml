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
    // Right-click "lock": the modifier is held down until released.
    // Locked always implies isActive, so the key already carries the accent
    // fill; the lock adds a solid bar along the bottom edge on top of it
    // (see lockBar below) so a held modifier reads as *more than* a sticky
    // one-shot press rather than as a different thing entirely.
    property bool isLocked: false
    property bool isWide: false

    // How far the hit area reaches past the keycap's own slot, per
    // axis, in pixels.  The gap between two caps is otherwise dead: a
    // click landing in it types nothing and shows nothing, and unlike a
    // click that lands on the *wrong* key it is a miss nothing
    // downstream can recover, because no character is emitted for the
    // prediction engine to see.  Each key takes half of every gap around
    // it, so two neighbours meet in the middle: nothing is dead, and
    // nothing overlaps either, which matters because an overlap resolves
    // to whichever key was declared later rather than to the nearer one.
    // The alternatives were both worse - growing the caps changes what
    // the keyboard looks like, and one interceptor over the grid that
    // resolves each press to the nearest key is the swipe overlay's
    // design flaw (see the removal note in CLAUDE.md).  Both default to
    // 0, so a KeyButton with no gap around it - and every caller that
    // does not set them - is unchanged.
    property real hitMarginH: 0
    property real hitMarginV: 0

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
                                     // to ``repeatArmDelay`` so a
                                     // slightly-too-long tap on
                                     // backspace doesn't fire a second
                                     // emit.  Once the user is
                                     // genuinely holding past the
                                     // grace, auto-repeat kicks in at
                                     // ``repeatInterval`` cadence as
                                     // before.  See ``repeatArmFloorMs``
                                     // below for why this alone is not
                                     // a fixed ~800 ms at every setting.

    // Minimum total hold time (ms) before the FIRST auto-repeat
    // keystroke, for a caller that needs a hard floor rather than
    // trusting ``repeatDelay + warmUpGrace``.  0 means no floor: use
    // warmUpGrace as-is.  ``repeatDelay`` is a user setting the Settings
    // page clamps to 300-1500 ms while ``warmUpGrace`` above is a fixed
    // 300, so at the 300 ms minimum the arithmetic alone put the first
    // repeat at 600 ms and every ``repeatInterval`` after that - well
    // under the "roughly 800 ms" slow-motor-input guarantee three other
    // comments in this codebase used to state as fact.  Character keys
    // set this to 800 (see Main.qml's ``characterRepeat``), because a
    // repeating letter turns one intended keystroke into several, which
    // is exactly what holding a key down is supposed to avoid for slow,
    // imprecise motor input.  Backspace/Delete/the arrows leave it at 0:
    // a user who lowered ``repeatDelay`` to make Backspace snappier
    // should get a snappier Backspace, not a re-imposed floor.
    property int repeatArmFloorMs: 0

    // The true total arm time - how long a press must be held before
    // the first auto-repeat keystroke fires.  ``warmUpGrace`` unless
    // ``repeatArmFloorMs`` demands more once ``repeatDelay`` is already
    // subtracted from it, in which case the floor wins.  The repeat
    // Timer's phase-1 interval reads this, not the raw ``warmUpGrace``,
    // which is what makes the floor actually bite.
    readonly property int effectiveWarmUp: Math.max(keyRoot.warmUpGrace,
                                                      keyRoot.repeatArmFloorMs - keyRoot.repeatDelay)
    readonly property int repeatArmDelay: keyRoot.repeatDelay + keyRoot.effectiveWarmUp

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

    // Ink colour for anything drawn ON TOP of the active / pressed fill:
    // the key label and the lock bar.  Nine themes ship and several have a
    // pale accent (Blackboard, Spaceship) while Typewriter is a light theme
    // outright, so a fixed colour is unreadable on roughly half of them.
    // Picking by luminance means both marks stay legible on every theme
    // with no per-theme table, and it keeps the bar the same ink as the
    // label it underlines rather than a third colour competing with it.
    readonly property color _onFillColor: {
        var bg = keyRoot._visualPressed ? keyRoot.keyPressedColor : keyRoot.accentColor
        var lum = bg.r * 0.299 + bg.g * 0.587 + bg.b * 0.114
        return lum > 0.5 ? "#111111" : "#ffffff"
    }

    // Signals
    signal keyPressed()
    // Right-click — emitted on right mouse button.  Caller decides what
    // to do (typically: type the shifted variant of this key without
    // flipping the sticky shift state).  Press visuals + ripple still
    // fire so the user gets the same tactile feedback as a left-click,
    // but the auto-repeat timer never starts — right-click is a
    // deliberate one-shot, not a hold.
    signal keyRightPressed()
    // Emitted whenever a press ends — normal release, cancel, or the
    // cursor dragging off the key (which under WS_EX_NOACTIVATE can be
    // the only signal we get).  Callers use it to dismiss press-tied
    // transient UI such as the key-preview bubble.
    signal keyReleased()

    // Where inside the key the current press landed, as fractions of the
    // key's width and height from its centre (-0.5 to 0.5).  Set on press
    // and handed to the bridge with the character, so the fuzzy beam can
    // tell a click near a key's edge from one dead centre and learn the
    // user's systematic offset.  Auto-repeat re-reads the same values.
    property real pressDx: 0
    property real pressDy: 0

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
    //   phase 1 (grace):      scheduled at ``effectiveWarmUp`` (the
    //                         larger of ``warmUpGrace`` and whatever
    //                         ``repeatArmFloorMs`` still demands after
    //                         ``repeatDelay``).  When it fires, emit
    //                         the first auto-repeat keystroke and
    //                         transition to phase 2.
    //   phase 2 (repeating):  scheduled at ``repeatInterval`` cadence,
    //                         emit each tick.
    //
    // The grace phase exists because phase 0 alone left a 120 ms
    // boundary between "one tap" and "tap that fires twice".  Slow-
    // motor users systematically tipped past it on backspace and felt
    // it as "Backspace sometimes sends 2".  Adding the grace widens
    // the boundary from ``repeatDelay + repeatInterval`` to
    // ``repeatArmDelay`` (``repeatDelay + effectiveWarmUp``) without
    // slowing down bulk-delete once auto-repeat is genuinely engaged.
    // At the defaults that lands around 800 ms, but ``repeatDelay`` is
    // a user setting clamped down to 300 ms, where ``warmUpGrace``
    // alone would let the first repeat land at 600 ms - a caller that
    // sets ``repeatArmFloorMs`` is asking for a hard floor on the total
    // instead of trusting the arithmetic to stay above it.
    //
    // Any press shorter than ``repeatArmDelay`` gives exactly one
    // keystroke.  ``phase`` must be reset to 0 wherever the timer is
    // stopped (``onReleased``, ``onCanceled``,
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
                interval = keyRoot.effectiveWarmUp
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

    // ===== Press lifecycle =====
    //
    // Split out of the MouseArea because a press can end through more than
    // one path - release, cancel, or the cursor dragging off the key while
    // it is still held - and every one of them has to run the same
    // teardown.  Inlining it in each handler is how one of them ends up
    // forgetting to stop the repeat timer.

    // Debounce: drop any second press within debounceMs of the previous
    // accepted one.  Catches hardware bounce and accidental double-clicks
    // without affecting deliberate typing (150 ms is well under a human's
    // repeat cadence).
    function _acceptPress() {
        var now = Date.now()
        if (now - keyRoot._lastAcceptedPress < keyRoot.debounceMs)
            return false
        keyRoot._lastAcceptedPress = now
        return true
    }

    // Visual press is driven explicitly so a missed release can't strand the
    // key looking pressed-down (see the _visualPressed comment up top).
    function _pressVisual(localX, localY) {
        keyRoot._visualPressed = true
        pressSafetyTimer.restart()
        ripple.centerX = localX - keyBackground.anchors.margins
        ripple.centerY = localY - keyBackground.anchors.margins
        rippleAnim.stop()
        ripple.width = 0
        ripple.opacity = 0
        rippleAnim.start()
    }

    // Type once, then arm auto-repeat if this key opted in.  Character keys
    // never do (see the enableRepeat comment up top); Backspace, Delete and
    // the arrows do.
    function _activate() {
        keyRoot.keyPressed()
        if (keyRoot.enableRepeat) {
            repeatTimer.interval = keyRoot.repeatDelay
            repeatTimer.repeat = false
            repeatTimer.start()
        }
    }

    function _endPress() {
        keyRoot._visualPressed = false
        keyRoot.keyReleased()
        pressSafetyTimer.stop()
        repeatTimer.stop()
        repeatTimer.interval = keyRoot.repeatDelay
        repeatTimer.repeat = false
        repeatTimer.phase = 0
    }

    Rectangle {
        id: keyBackground
        anchors.fill: parent
        // Inset of the drawn keycap inside its allocated slot.  This is
        // doubled into every visible gap (my inset + my neighbour's), so
        // it dominates the apparent spacing far more than `keySpacing`
        // does.  1 px keeps the keycaps visually distinct without the
        // dead space reading as a grid of separated tiles.
        anchors.margins: 1
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
            // displayText is layout-JSON-driven; force plain rendering so a
            // custom/modular layout's key label can't auto-render as HTML.
            textFormat: Text.PlainText
            // Readable contrast on the active / pressed fill (dark ink on a
            // bright one, white on a dark one) via the shared luminance rule
            // up top, so the label and the lock bar can never disagree about
            // what is legible on a given theme.  The resting key keeps the
            // theme's own text colour.
            color: (keyRoot._visualPressed || isActive) ? keyRoot._onFillColor : keyTextColor
            font.pixelSize: keyRoot.fontSize
            font.family: "Segoe UI, Inter, Ubuntu, Noto Sans, sans-serif"
            font.weight: isSpecial ? Font.DemiBold : Font.DemiBold
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }

        // Right-click "lock" indicator: a solid bar along the bottom edge,
        // shown only while the modifier is held down.  Locked implies
        // isActive, so this sits on top of the accent fill a sticky
        // one-shot press already has, and the difference between "held for
        // one keystroke" and "held until you release it" is one unmissable
        // mark rather than a second colour scheme.
        //
        // This replaced a gold ring plus a 15x15 gold badge holding a 9 px
        // 🔒.  Two things were wrong with that: at 9 px the padlock is a
        // smudge, and Windows renders it through Segoe UI Emoji as a colour
        // glyph, which ignores the `color` property outright, so what
        // actually shipped was a yellow blob in the corner.  The gold was
        // hardcoded too, so it fought all nine themes.  No emoji here for
        // the same reason: any glyph small enough to fit on a keycap is
        // at the mercy of the host emoji font.
        //
        // Inset horizontally so the square ends clear the keycap's rounded
        // corners.  `clip: true` on the parent clips to its bounding rect,
        // not to the rounded shape, so a full-bleed bar would visibly poke
        // out past the curve.
        Rectangle {
            id: lockBar
            // Lets the headless tests reach the bar; a Repeater's delegates
            // are visual children, so this is the only handle on it.
            objectName: "keyLockBar"
            visible: keyRoot.isLocked
            height: 3
            radius: height / 2
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.leftMargin: Math.max(4, keyRoot.radius * 0.75)
            anchors.rightMargin: anchors.leftMargin
            anchors.bottomMargin: 3
            color: keyRoot._onFillColor
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
        // Negative margins grow the area past `keyRoot`, which is what
        // puts the gap between two caps under a key rather than under
        // nothing.  Qt Quick delivers a press to a child outside its
        // parent's bounds as long as no ancestor clips, and none does on
        // this path (`keyBackground` sets `clip` for its own rounded
        // corners, but it is this area's sibling, not its parent).
        anchors.fill: parent
        anchors.leftMargin: -keyRoot.hitMarginH
        anchors.rightMargin: -keyRoot.hitMarginH
        anchors.topMargin: -keyRoot.hitMarginV
        anchors.bottomMargin: -keyRoot.hitMarginV
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton

        onPressed: function(mouse) {
            if (!keyRoot._acceptPress()) {
                mouse.accepted = true
                return
            }
            // mouse.x/y are relative to the enlarged hit area, so take
            // the margin back out: the ripple's origin and the press
            // offset the pointer-bias model learns from both mean "where
            // inside the keycap".  A press in the gap therefore reads
            // just past +/-0.5, which is true, and is signal rather than
            // noise - a click landing off the cap is exactly what a
            // pointer bias looks like, and PointerModel clamps at 1.0.
            var capX = mouse.x - keyRoot.hitMarginH
            var capY = mouse.y - keyRoot.hitMarginV
            keyRoot._pressVisual(capX, capY)
            keyRoot.pressDx = keyRoot.width > 0 ? capX / keyRoot.width - 0.5 : 0
            keyRoot.pressDy = keyRoot.height > 0 ? capY / keyRoot.height - 0.5 : 0

            if (mouse.button === Qt.RightButton) {
                // Right-click is a one-shot: never auto-repeats, and
                // the caller decides what (if anything) to type.
                keyRoot.keyRightPressed()
                return
            }

            keyRoot._activate()
        }

        onReleased: keyRoot._endPress()

        onCanceled: keyRoot._endPress()

        // Cursor leaving the key clears the visual press AND stops
        // repeat — covers two cases: (1) the user dragged off to abort
        // the keypress, (2) Qt never delivered the release because the
        // cursor went onto another app's window (WS_EX_NOACTIVATE
        // sometimes drops that event).  Either way, the key's no longer
        // being interacted with, so it shouldn't look held down.
        onContainsMouseChanged: {
            if (!containsMouse) {
                if (keyRoot._visualPressed) keyRoot.keyReleased()
                keyRoot._visualPressed = false
                repeatTimer.stop()
                repeatTimer.interval = keyRoot.repeatDelay
                repeatTimer.repeat = false
                repeatTimer.phase = 0
            }
        }
    }
}
