import QtQuick

// Throwaway window for trying the pick-up analog stick against a real game.
// Talks only to the `stick` context property (scripts/prototypes/analog_stick.py).
// Never takes focus; has no drag, only Left / Centre / Right placement buttons.
Window {
    id: win

    readonly property color bg: "#1a1a1a"
    readonly property color key: "#3a3a3a"
    readonly property color ink: "#e0e0e0"
    readonly property color accent: "#4a9eff"
    readonly property color edge: "#505050"
    readonly property bool holding: stick.held !== ""

    // 0 left, 1 centre, 2 right
    property int placement: 1

    width: 620
    height: 360
    visible: true
    color: bg
    title: "Analog stick prototype"
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus | Qt.Tool
    x: {
        var left = Screen.virtualX + 20
        var right = Screen.virtualX + Screen.width - width - 20
        return placement === 0 ? left : (placement === 2 ? right : (left + right) / 2)
    }
    y: Screen.virtualY + Screen.desktopAvailableHeight - height - 20

    function fmt(v) {
        return (v < 0 ? "" : " ") + v.toFixed(2)
    }

    // A flat button: left click is `clicked(0)`, right click is `clicked(1)`.
    component Btn: Rectangle {
        id: btn
        property string label: ""
        property bool on: false
        property bool bar: false
        signal clicked(int which)

        color: btn.on ? Qt.darker(win.accent, 2.2) : win.key
        border.color: win.edge
        border.width: 1
        radius: 4

        Text {
            anchors.centerIn: parent
            text: btn.label
            color: win.ink
            font.pixelSize: 12
            textFormat: Text.PlainText
        }
        Rectangle {
            visible: btn.bar
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 3
            color: win.accent
        }
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            onClicked: function (mouse) {
                btn.clicked(mouse.button === Qt.RightButton ? 1 : 0)
            }
        }
    }

    // One stick: a circle with a knob at the reported position.
    component StickView: Rectangle {
        id: sv
        property string name: "ls"
        property string title: "LEFT"
        property real px: 0
        property real py: 0
        property string padState: "idle"
        readonly property real radius2: width / 2

        width: 200
        height: 200
        radius: width / 2
        color: "#242424"
        border.color: stick.held === sv.name ? win.accent : (sv.padState === "latched" ? win.accent : win.edge)
        border.width: stick.held === sv.name ? 4 : (sv.padState === "latched" ? 2 : 1)

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            y: 28
            text: sv.title + (sv.padState === "latched" ? " (held)" : "")
            color: win.ink
            font.pixelSize: 12
            textFormat: Text.PlainText
        }
        Rectangle {
            width: 26
            height: 26
            radius: 13
            color: win.accent
            x: sv.radius2 - 13 + sv.px * (sv.radius2 - 13)
            y: sv.radius2 - 13 - sv.py * (sv.radius2 - 13)
        }
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton
            enabled: !win.holding
            onPressed: function (mouse) {
                var dpr = Screen.devicePixelRatio
                // Pointer to the stick's drawn centre, in physical pixels.
                var dx = (sv.width / 2 - mouse.x) * dpr
                var dy = (sv.height / 2 - mouse.y) * dpr
                // Pointer to this window's corner, and the window's size: the
                // confinement is cut down to the window, the only place the
                // click that puts the stick down can be caught.
                var inWindow = mapToItem(null, mouse.x, mouse.y)
                stick.pickUp(sv.name, dx, dy, sv.radius2 * dpr,
                             -inWindow.x * dpr, -inWindow.y * dpr,
                             win.width * dpr, win.height * dpr)
            }
        }
    }

    Text {
        x: 12
        y: 8
        text: "Analog stick prototype"
        color: win.ink
        font.pixelSize: 14
        font.bold: true
        textFormat: Text.PlainText
    }
    Text {
        anchors.right: parent.right
        anchors.rightMargin: 12
        y: 10
        text: stick.note
        color: "#e0b060"
        font.pixelSize: 11
        textFormat: Text.PlainText
    }

    StickView {
        x: 10
        y: 34
        name: "ls"
        title: "LEFT"
        px: stick.lsX
        py: stick.lsY
        padState: stick.lsState
    }
    StickView {
        x: 410
        y: 34
        name: "rs"
        title: "RIGHT"
        px: stick.rsX
        py: stick.rsY
        padState: stick.rsState
    }

    // Centre column
    Grid {
        x: 222
        y: 34
        columns: 3
        spacing: 4
        Repeater {
            model: ["a", "b", "lb", "rb", "lt", "rt"]
            delegate: Btn {
                required property string modelData
                width: 56
                height: 30
                label: modelData.toUpperCase()
                bar: stick.latched.indexOf(modelData) >= 0
                onClicked: function (which) {
                    if (which === 0)
                        stick.tapButton(modelData)
                    else
                        stick.latchButton(modelData)
                }
            }
        }
    }
    Column {
        x: 222
        y: 108
        spacing: 4
        Btn {
            width: 176
            height: 28
            label: "Confine pointer: " + (stick.confine ? "on" : "off")
            on: stick.confine
            onClicked: stick.confine = !stick.confine
        }
        Btn {
            width: 176
            height: 28
            label: "Recentre on pick-up: " + (stick.recentre ? "on" : "off")
            on: stick.recentre
            onClicked: stick.recentre = !stick.recentre
        }
        Btn {
            width: 176
            height: 28
            label: "One-stick driving: " + (stick.drive ? "on" : "off")
            on: stick.drive
            onClicked: stick.drive = !stick.drive
        }
    }
    Row {
        x: 222
        y: 208
        spacing: 4
        Btn {
            width: 56
            height: 28
            label: "Connect"
            onClicked: stick.connectPad()
        }
        Btn {
            width: 56
            height: 28
            label: "Disconnect"
            onClicked: stick.disconnectPad()
        }
        Btn {
            width: 56
            height: 28
            label: "Quit"
            onClicked: stick.quit()
        }
    }
    Row {
        x: 222
        y: 242
        spacing: 4
        Repeater {
            model: ["Left", "Centre", "Right"]
            delegate: Btn {
                required property string modelData
                required property int index
                width: 56
                height: 26
                label: modelData
                on: win.placement === index
                onClicked: win.placement = index
            }
        }
    }

    Rectangle {
        x: 10
        y: 276
        width: 600
        height: 76
        color: "#141414"
        border.color: win.edge
        radius: 4
        Text {
            anchors.fill: parent
            anchors.margins: 6
            color: win.ink
            font.family: "Consolas"
            font.pixelSize: 12
            textFormat: Text.PlainText
            text: "status " + stick.status + "   guard " + (stick.guard ? "yes" : "no")
                  + "   held " + (stick.held === "" ? "-" : stick.held)
                  + "\nLS " + win.fmt(stick.lsX) + " " + win.fmt(stick.lsY)
                  + "   RS " + win.fmt(stick.rsX) + " " + win.fmt(stick.rsY)
                  + "   LT " + stick.lt.toFixed(2) + "   RT " + stick.rt.toFixed(2)
                  + "\nclip " + stick.clip + "   reapplied " + stick.reapplied
                  + "   tick " + stick.tickMs.toFixed(1) + " ms"
        }
    }

    // While a stick is held this covers the whole window, so the click that
    // puts it down can reach no other control (the idiom of Main.qml's
    // windowMoveOverlay). Left drops to centre, right leaves it held.
    MouseArea {
        anchors.fill: parent
        z: 100
        enabled: win.holding
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onClicked: function (mouse) {
            if (mouse.button === Qt.RightButton)
                stick.hold()
            else
                stick.drop()
        }
    }
}
