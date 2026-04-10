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
    property real fontSize: 16
    property real radius: 8
    property bool isSpecial: false
    property bool isActive: false  // For modifier keys (shift, ctrl, etc.)
    property bool isWide: false

    // Key repeat settings
    property bool enableRepeat: true
    property int repeatDelay: 400    // ms before repeat starts
    property int repeatInterval: 50  // ms between repeats

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
        color: mouseArea.pressed ? keyPressedColor
             : isActive ? "#4a9eff"
             : mouseArea.containsMouse ? Qt.lighter(keyColor, 1.25)
             : keyColor

        border.color: isActive ? "#6ab4ff"
                    : mouseArea.containsMouse ? Qt.lighter("#505050", 1.4)
                    : "#505050"
        border.width: 1

        // Subtle gradient overlay
        Rectangle {
            anchors.fill: parent
            anchors.margins: 1
            radius: parent.radius - 1
            gradient: Gradient {
                GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0.06) }
                GradientStop { position: 1.0; color: Qt.rgba(0, 0, 0, 0.08) }
            }
        }

        // Key label
        Text {
            anchors.centerIn: parent
            text: keyRoot.displayText
            color: mouseArea.pressed ? "#ffffff" : keyTextColor
            font.pixelSize: keyRoot.fontSize
            font.family: "Segoe UI, Ubuntu, Noto Sans, sans-serif"
            font.weight: isSpecial ? Font.Medium : Font.Normal
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }

        // Press animation
        Behavior on color {
            ColorAnimation { duration: 80 }
        }

        // Scale animation on press
        transform: Scale {
            id: scaleTransform
            origin.x: keyBackground.width / 2
            origin.y: keyBackground.height / 2
            xScale: mouseArea.pressed ? 0.94 : 1.0
            yScale: mouseArea.pressed ? 0.94 : 1.0
            Behavior on xScale { NumberAnimation { duration: 60 } }
            Behavior on yScale { NumberAnimation { duration: 60 } }
        }
    }

    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true

        onPressed: {
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
    }
}
