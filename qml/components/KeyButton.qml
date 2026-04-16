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
    property int repeatDelay: 400    // ms before repeat starts
    property int repeatInterval: 50  // ms between repeats

    // Debounce window (ms).  Consecutive MouseArea presses within this
    // window count as a single press — covers hardware button bounce
    // (cheap / worn mice emit two events per physical click) and
    // accidental double-clicks from slow motor control.  150 ms is
    // well below any deliberate rapid-click cadence but well above any
    // plausible bounce interval.
    property int debounceMs: 150
    property real _lastAcceptedPress: 0

    // Signals
    signal keyPressed()

    width: keyWidth
    height: keyHeight
    // implicitWidth/Height are what Qt Quick Layouts (RowLayout, GridLayout)
    // use for size allocation.  Without these, layouts see 0×0 and keys overflow.
    implicitWidth: keyWidth
    implicitHeight: keyHeight

    // Key repeat timer
    Timer {
        id: repeatTimer
        interval: keyRoot.repeatDelay
        repeat: false
        onTriggered: {
            keyRoot.keyPressed()
            interval = keyRoot.repeatInterval
            repeat = true
            start()
        }
    }

    Rectangle {
        id: keyBackground
        anchors.fill: parent
        anchors.margins: 2
        radius: keyRoot.radius
        clip: true
        color: mouseArea.pressed ? keyPressedColor
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
                GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, mouseArea.pressed ? 0.02 : 0.06) }
                GradientStop { position: 1.0; color: Qt.rgba(0, 0, 0, mouseArea.pressed ? 0.14 : 0.08) }
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
                var bg = mouseArea.pressed ? keyPressedColor : isActive ? accentColor : keyColor
                // Luminance approximation: bright backgrounds need dark text
                var lum = bg.r * 0.299 + bg.g * 0.587 + bg.b * 0.114
                if (mouseArea.pressed || isActive) {
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
            xScale: mouseArea.pressed ? 0.94 : 1.0
            yScale: mouseArea.pressed ? 0.94 : 1.0
            Behavior on xScale { NumberAnimation { duration: 100; easing.type: Easing.OutBack; easing.overshoot: 1.2 } }
            Behavior on yScale { NumberAnimation { duration: 100; easing.type: Easing.OutBack; easing.overshoot: 1.2 } }
        }
    }

    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true

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

            // Trigger ripple from press point
            ripple.centerX = mouse.x - keyBackground.anchors.margins
            ripple.centerY = mouse.y - keyBackground.anchors.margins
            rippleAnim.stop()
            ripple.width = 0
            ripple.opacity = 0
            rippleAnim.start()

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
            repeatTimer.stop()
            repeatTimer.interval = keyRoot.repeatDelay
            repeatTimer.repeat = false
        }

        onCanceled: {
            repeatTimer.stop()
            repeatTimer.interval = keyRoot.repeatDelay
            repeatTimer.repeat = false
        }

        // Stop key repeat the moment the cursor drags off the key — even
        // if the user is still holding the mouse button.  Prevents a
        // held key from continuing to fire while the pointer wanders.
        onContainsMouseChanged: {
            if (!containsMouse) {
                repeatTimer.stop()
                repeatTimer.interval = keyRoot.repeatDelay
                repeatTimer.repeat = false
            }
        }
    }
}
