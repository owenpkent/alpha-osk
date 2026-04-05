import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Window 2.15
import "components" as Comp

Window {
    id: root
    visible: true
    // Default size gives keyW ≈ 56px; user can freely resize and keys scale
    width: 880
    height: mainLayout.implicitHeight + 60  // Extra height for title bar + bottom padding
    minimumWidth: Math.round(30 * totalKeyUnits + layoutFixedPixels)  // keyW ≈ 30px — smallest usable touch target
    minimumHeight: 200
    color: "transparent"
    title: "Alpha-OSK"

    // Position once at startup — do NOT bind x/y to width/height or resize
    // will feel inverted (window re-centers on every pixel change)
    Component.onCompleted: {
        root.x = (Screen.width - root.width) / 2
        root.y = Screen.height - root.height - 40
    }

    // When side panels toggle, grow/shrink from the right edge (left stays put)
    // Deltas sized to keep main keys ~same size (≈ 2.7*keyW+17 for nav, 3.6*keyW+19 for numpad at default scale)
    onShowNavigationChanged: root.width += showNavigation ? 175 : -175
    onShowNumpadChanged: root.width += showNumpad ? 220 : -220

    // Keyboard state from Python bridge
    property bool shiftOn: keyboard ? keyboard.shiftActive : false
    property bool capsOn: keyboard ? keyboard.capsLockActive : false
    property bool ctrlOn: keyboard ? keyboard.ctrlActive : false
    property bool altOn: keyboard ? keyboard.altActive : false
    property bool winOn: keyboard ? keyboard.winActive : false
    property string layer: keyboard ? keyboard.currentLayer : "lower"
    property bool showNumbers: layer === "numbers"
    property bool showSymbols: layer === "symbols"
    
    // Predictions from hybrid engine
    property var predictions: []
    property bool predictionsLoading: false
    
    // Layout toggles (modular panels)
    property bool showFunctionRow: false
    property bool showNavigation: false
    property bool showNumpad: false
    property bool showSettings: false
    
    // Debug
    property bool showDebugPanel: false
    property var debugLog: []
    property string debugContext: ""

    // Sizing — keys scale dynamically with window width using closed-form calculation.
    // All visible panels share the window width proportionally, avoiding static estimates.
    property real keySpacing: 3

    // Total key-width units across all visible sections:
    // Main keyboard home row: Caps(1.6) + 9 alpha + ; + ' + Enter(1.8) = 14.4 units
    // Nav panel: 3 keys × 0.9 = 2.7 units;  Numpad: 4 keys × 0.9 = 3.6 units
    property real totalKeyUnits: 14.4
        + (showNavigation ? 2.7 : 0)
        + (showNumpad ? 3.6 : 0)

    // Fixed-pixel overhead: margins(8×2=16) + main gaps(12×3=36)
    // + per-panel: separator(1) + panel gaps + 2×RowLayout spacing(6)
    // Nav: 1 + 2×2 + 2×6 = 17;  Numpad: 1 + 3×2 + 2×6 = 19
    property real layoutFixedPixels: 52
        + (showNavigation ? 17 : 0)
        + (showNumpad ? 19 : 0)

    property real keyW: Math.max(30, (root.width - layoutFixedPixels) / totalKeyUnits)
    property real keyH: Math.max(34, keyW * 0.89)

    // Safety net: if the window width ever drops below minimumWidth (e.g. via
    // OS window-snap, DPI change, or panel toggle), clamp it back up.
    onWidthChanged: {
        if (width < minimumWidth) width = minimumWidth
    }

    // Multi-monitor DPI fix: when Qt moves the window to a screen with a
    // different scale factor it can mis-size the window.  Clamp to the new
    // screen's available width so the keyboard never bloats off-screen.
    onScreenChanged: {
        var maxW = Screen.desktopAvailableWidth - 40
        if (root.width > maxW) root.width = maxW
        if (root.width < root.minimumWidth) root.width = root.minimumWidth
    }
    
    // ===== Color Theme System =====
    property string currentTheme: "dark"  // "dark", "light", "blue", "green", "purple"
    
    // Theme colors (computed based on currentTheme)
    property color themeBackground: {
        switch(currentTheme) {
            case "light": return "#e8e8e8"
            case "blue": return "#1a2a3a"
            case "green": return "#1a2a1a"
            case "purple": return "#2a1a3a"
            default: return "#1a1a1a"  // dark
        }
    }
    property color themeKeyColor: {
        switch(currentTheme) {
            case "light": return "#ffffff"
            case "blue": return "#2a4a6a"
            case "green": return "#2a4a2a"
            case "purple": return "#4a2a5a"
            default: return "#3a3a3a"
        }
    }
    property color themeKeyPressed: {
        switch(currentTheme) {
            case "light": return "#d0d0d0"
            case "blue": return "#3a6a9a"
            case "green": return "#3a6a3a"
            case "purple": return "#6a3a7a"
            default: return "#5a5a5a"
        }
    }
    property color themeTextColor: {
        switch(currentTheme) {
            case "light": return "#1a1a1a"
            default: return "#e0e0e0"
        }
    }
    property color themeAccent: {
        switch(currentTheme) {
            case "light": return "#0078d4"
            case "blue": return "#4a9eff"
            case "green": return "#4aff4a"
            case "purple": return "#bb66ff"
            default: return "#4a9eff"
        }
    }
    property color themeBorder: {
        switch(currentTheme) {
            case "light": return "#c0c0c0"
            default: return "#505050"
        }
    }

    // Update state when bridge emits signals
    Connections {
        target: keyboard
        function onShiftActiveChanged(active) { root.shiftOn = active }
        function onCapsLockActiveChanged(active) { root.capsOn = active }
        function onCtrlActiveChanged(active) { root.ctrlOn = active }
        function onAltActiveChanged(active) { root.altOn = active }
        function onWinActiveChanged(active) { root.winOn = active }
        function onCurrentLayerChanged(newLayer) { root.layer = newLayer }
        
        // Prediction updates
        function onPredictionsChanged(preds) { root.predictions = preds }
        function onPredictionsRefined(preds) { root.predictions = preds }
        function onPredictionLoading(loading) { root.predictionsLoading = loading }
        
        // Debug updates
        function onDebugLogChanged(log) { root.debugLog = log }
    }

    // Main background
    Rectangle {
        id: background
        anchors.fill: parent
        radius: 10
        color: root.themeBackground
        border.color: root.themeBorder
        border.width: 1
        
        Behavior on color { ColorAnimation { duration: 200 } }

        // Shadow
        Rectangle {
            anchors.fill: parent
            anchors.margins: -1
            radius: 11
            color: "transparent"
            border.color: Qt.rgba(0, 0, 0, 0.5)
            border.width: 1
            z: -1
        }

        // Title bar with drag, settings, minimize, close
        Rectangle {
            id: titleBar
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            height: 28
            color: Qt.darker(root.themeBackground, 1.1)
            radius: 10
            
            // Only round top corners
            Rectangle {
                anchors.bottom: parent.bottom
                anchors.left: parent.left
                anchors.right: parent.right
                height: 14
                color: parent.color
            }
            
            // Drag area (most of title bar)
            MouseArea {
                id: dragArea
                anchors.fill: parent
                anchors.rightMargin: 120  // Leave space for buttons
                cursorShape: Qt.SizeAllCursor
                
                property real startMouseX
                property real startMouseY
                property real startWinX
                property real startWinY
                
                onPressed: function(mouse) {
                    var global = mapToGlobal(mouse.x, mouse.y)
                    startMouseX = global.x
                    startMouseY = global.y
                    startWinX = root.x
                    startWinY = root.y
                }
                
                onPositionChanged: function(mouse) {
                    if (pressed) {
                        var global = mapToGlobal(mouse.x, mouse.y)
                        root.x = startWinX + (global.x - startMouseX)
                        root.y = startWinY + (global.y - startMouseY)
                    }
                }
            }
            
            // Drag indicator dots
            Row {
                anchors.left: parent.left
                anchors.leftMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                spacing: 3
                Repeater {
                    model: 5
                    Rectangle { width: 3; height: 3; radius: 1.5; color: "#555" }
                }
            }
            
            // Title bar buttons (right side)
            Row {
                anchors.right: parent.right
                anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                spacing: 6
                
                // Settings button (gear icon) - opens unified settings
                Rectangle {
                    width: 28
                    height: 24
                    radius: 4
                    color: settingsBtn.containsMouse ? "#444" : "transparent"
                    
                    Text {
                        anchors.centerIn: parent
                        text: "⚙"
                        font.pixelSize: 16
                        color: root.showSettings ? "#4a9eff" : "#999"
                    }
                    
                    MouseArea {
                        id: settingsBtn
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.showSettings = !root.showSettings
                    }
                }
                
                // Minimize button
                Rectangle {
                    width: 28
                    height: 24
                    radius: 4
                    color: minBtn.containsMouse ? "#444" : "transparent"
                    
                    Rectangle {
                        anchors.centerIn: parent
                        width: 12
                        height: 2
                        radius: 1
                        color: "#999"
                    }
                    
                    MouseArea {
                        id: minBtn
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.visibility = Window.Minimized
                    }
                }
                
                // Close button
                Rectangle {
                    width: 28
                    height: 24
                    radius: 4
                    color: closeBtn.containsMouse ? "#c33" : "transparent"
                    
                    Text {
                        anchors.centerIn: parent
                        text: "✕"
                        font.pixelSize: 13
                        color: closeBtn.containsMouse ? "#fff" : "#999"
                    }
                    
                    MouseArea {
                        id: closeBtn
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: Qt.quit()
                    }
                }
            }
        }

        RowLayout {
            id: mainLayout
            anchors.fill: parent
            anchors.margins: 8
            anchors.topMargin: 32  // Account for title bar
            spacing: 6

            // ===== Main Keyboard Section =====
            ColumnLayout {
                id: mainKeyboard
                Layout.fillWidth: true
                spacing: 2

                // ===== Function Row (F1-F12) =====
                Comp.FunctionRow {
                    visible: root.showFunctionRow
                    Layout.alignment: Qt.AlignHCenter
                    keyW: root.keyW * 0.85
                    keyH: root.keyH * 0.7
                }

                // ===== Prediction Bar =====
                Row {
                    Layout.alignment: Qt.AlignHCenter
                    Layout.bottomMargin: 4
                    spacing: 8

                    Repeater {
                        model: root.predictions.length > 0 ? root.predictions.slice(0, 5) : []
                        delegate: Rectangle {
                            width: Math.max(80, predText.implicitWidth + 28)
                            height: 36
                            radius: 8
                            color: predMouse.containsMouse ? "#3d4d5d" : "#2a3a4a"
                            border.color: predMouse.containsMouse ? "#6ab4ff" : "#4a9eff"
                            border.width: predMouse.containsMouse ? 2 : 1
                            
                            // Subtle gradient for depth
                            Rectangle {
                                anchors.fill: parent
                                anchors.margins: 1
                                radius: parent.radius - 1
                                gradient: Gradient {
                                    GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0.08) }
                                    GradientStop { position: 1.0; color: Qt.rgba(0, 0, 0, 0.05) }
                                }
                            }

                            Text {
                                id: predText
                                anchors.centerIn: parent
                                text: modelData
                                color: predMouse.containsMouse ? "#ffffff" : "#f0f0f0"
                                font.pixelSize: 15
                                font.weight: Font.Medium
                                font.family: "Ubuntu, Noto Sans, sans-serif"
                            }

                            MouseArea {
                                id: predMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: keyboard.pressPrediction(modelData)
                            }
                            
                            // Smooth hover animation
                            Behavior on color { ColorAnimation { duration: 100 } }
                            Behavior on border.color { ColorAnimation { duration: 100 } }
                        }
                    }
                    
                }

                // ===== Number Row =====
                Row {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: root.keySpacing

                    // Backtick/Tilde
                    Comp.KeyButton {
                        keyText: "`"
                        displayText: root.shiftOn ? "~" : "`"
                        keyWidth: root.keyW
                        keyHeight: root.keyH - 4
                        fontSize: 14
                        keyColor: "#2a2a2a"
                        onKeyPressed: keyboard.pressKey(root.shiftOn ? "~" : "`")
                    }

                    Repeater {
                        model: root.shiftOn
                            ? ["!", "@", "#", "$", "%", "^", "&", "*", "(", ")"]
                            : ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]
                        Comp.KeyButton {
                            keyText: modelData
                            displayText: modelData
                            keyWidth: root.keyW
                            keyHeight: root.keyH - 4
                            fontSize: 13
                            keyColor: "#2a2a2a"
                            onKeyPressed: keyboard.pressKey(modelData)
                        }
                    }

                    // Minus/Underscore
                    Comp.KeyButton {
                        keyText: "-"
                        displayText: root.shiftOn ? "_" : "-"
                        keyWidth: root.keyW
                        keyHeight: root.keyH - 4
                        fontSize: 14
                        keyColor: "#2a2a2a"
                        onKeyPressed: keyboard.pressKey(root.shiftOn ? "_" : "-")
                    }

                    // Backspace
                    Comp.KeyButton {
                        keyText: "backspace"
                        displayText: "⌫"
                        keyWidth: root.keyW * 1.5
                        keyHeight: root.keyH - 4
                        fontSize: 16
                        isSpecial: true
                        keyColor: "#333"
                        onKeyPressed: keyboard.pressSpecialKey("backspace")
                    }
                }

                // ===== QWERTY Row =====
                Row {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: root.keySpacing

                    Comp.KeyButton {
                        keyText: "tab"
                        displayText: "Tab"
                        keyWidth: root.keyW * 1.3
                        keyHeight: root.keyH
                        fontSize: 11
                        isSpecial: true
                        keyColor: "#333"
                        onKeyPressed: keyboard.pressSpecialKey("tab")
                    }

                    Repeater {
                        model: ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"]
                        Comp.KeyButton {
                            keyText: modelData
                            displayText: root.shiftOn ? modelData.toUpperCase() : modelData
                            keyWidth: root.keyW
                            keyHeight: root.keyH
                            onKeyPressed: keyboard.pressKey(modelData)
                        }
                    }

                    // Brackets
                    Comp.KeyButton {
                        keyText: "["
                        displayText: root.shiftOn ? "{" : "["
                        keyWidth: root.keyW
                        keyHeight: root.keyH
                        fontSize: 14
                        keyColor: "#2a2a2a"
                        onKeyPressed: keyboard.pressKey(root.shiftOn ? "{" : "[")
                    }
                    Comp.KeyButton {
                        keyText: "]"
                        displayText: root.shiftOn ? "}" : "]"
                        keyWidth: root.keyW
                        keyHeight: root.keyH
                        fontSize: 14
                        keyColor: "#2a2a2a"
                        onKeyPressed: keyboard.pressKey(root.shiftOn ? "}" : "]")
                    }
                    Comp.KeyButton {
                        keyText: "\\"
                        displayText: root.shiftOn ? "|" : "\\"
                        keyWidth: root.keyW
                        keyHeight: root.keyH
                        fontSize: 14
                        keyColor: "#2a2a2a"
                        onKeyPressed: keyboard.pressKey(root.shiftOn ? "|" : "\\")
                    }
                }

                // ===== Home Row =====
                Row {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: root.keySpacing

                    Comp.KeyButton {
                        keyText: "caps"
                        displayText: "Caps"
                        keyWidth: root.keyW * 1.6
                        keyHeight: root.keyH
                        fontSize: 11
                        isSpecial: true
                        isActive: root.capsOn
                        keyColor: "#333"
                        onKeyPressed: keyboard.toggleCapsLock()
                    }

                    Repeater {
                        model: ["a", "s", "d", "f", "g", "h", "j", "k", "l"]
                        Comp.KeyButton {
                            keyText: modelData
                            displayText: root.shiftOn ? modelData.toUpperCase() : modelData
                            keyWidth: root.keyW
                            keyHeight: root.keyH
                            onKeyPressed: keyboard.pressKey(modelData)
                        }
                    }

                    // Semicolon, Quote
                    Comp.KeyButton {
                        keyText: ";"
                        displayText: root.shiftOn ? ":" : ";"
                        keyWidth: root.keyW
                        keyHeight: root.keyH
                        fontSize: 14
                        keyColor: "#2a2a2a"
                        onKeyPressed: keyboard.pressKey(root.shiftOn ? ":" : ";")
                    }
                    Comp.KeyButton {
                        keyText: "'"
                        displayText: root.shiftOn ? "\"" : "'"
                        keyWidth: root.keyW
                        keyHeight: root.keyH
                        fontSize: 14
                        keyColor: "#2a2a2a"
                        onKeyPressed: keyboard.pressKey(root.shiftOn ? "\"" : "'")
                    }

                    Comp.KeyButton {
                        keyText: "return"
                        displayText: "Enter"
                        keyWidth: root.keyW * 1.8
                        keyHeight: root.keyH
                        fontSize: 11
                        isSpecial: true
                        keyColor: "#2a5a2a"
                        onKeyPressed: keyboard.pressSpecialKey("return")
                    }
                }

                // ===== Bottom Alpha Row =====
                Row {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: root.keySpacing

                    Comp.KeyButton {
                        keyText: "shift"
                        displayText: "⇧ Shift"
                        keyWidth: root.keyW * 2
                        keyHeight: root.keyH
                        fontSize: 11
                        isSpecial: true
                        isActive: root.shiftOn
                        keyColor: "#333"
                        onKeyPressed: keyboard.toggleShift()
                    }

                    Repeater {
                        model: ["z", "x", "c", "v", "b", "n", "m"]
                        Comp.KeyButton {
                            keyText: modelData
                            displayText: root.shiftOn ? modelData.toUpperCase() : modelData
                            keyWidth: root.keyW
                            keyHeight: root.keyH
                            onKeyPressed: keyboard.pressKey(modelData)
                        }
                    }

                    // Comma, Period, Slash
                    Comp.KeyButton {
                        keyText: ","
                        displayText: root.shiftOn ? "<" : ","
                        keyWidth: root.keyW
                        keyHeight: root.keyH
                        fontSize: 14
                        keyColor: "#2a2a2a"
                        onKeyPressed: keyboard.pressKey(root.shiftOn ? "<" : ",")
                    }
                    Comp.KeyButton {
                        keyText: "."
                        displayText: root.shiftOn ? ">" : "."
                        keyWidth: root.keyW
                        keyHeight: root.keyH
                        fontSize: 14
                        keyColor: "#2a2a2a"
                        onKeyPressed: keyboard.pressKey(root.shiftOn ? ">" : ".")
                    }
                    Comp.KeyButton {
                        keyText: "/"
                        displayText: root.shiftOn ? "?" : "/"
                        keyWidth: root.keyW
                        keyHeight: root.keyH
                        fontSize: 14
                        keyColor: "#2a2a2a"
                        onKeyPressed: keyboard.pressKey(root.shiftOn ? "?" : "/")
                    }

                    Comp.KeyButton {
                        keyText: "shift"
                        displayText: "⇧ Shift"
                        keyWidth: root.keyW * 2.3
                        keyHeight: root.keyH
                        fontSize: 11
                        isSpecial: true
                        isActive: root.shiftOn
                        keyColor: "#333"
                        onKeyPressed: keyboard.toggleShift()
                    }
                }

                // ===== Space Bar Row =====
                Row {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: root.keySpacing

                    Comp.KeyButton {
                        keyText: "ctrl"
                        displayText: "Ctrl"
                        keyWidth: root.keyW * 1.2
                        keyHeight: root.keyH
                        fontSize: 11
                        isSpecial: true
                        isActive: root.ctrlOn
                        keyColor: "#333"
                        onKeyPressed: keyboard.toggleCtrl()
                    }

                    Comp.KeyButton {
                        keyText: "win"
                        displayText: "⊞"
                        keyWidth: root.keyW
                        keyHeight: root.keyH
                        fontSize: 16
                        isSpecial: true
                        isActive: root.winOn
                        keyColor: "#333"
                        onKeyPressed: keyboard.toggleWin()
                    }

                    Comp.KeyButton {
                        keyText: "alt"
                        displayText: "Alt"
                        keyWidth: root.keyW * 1.1
                        keyHeight: root.keyH
                        fontSize: 11
                        isSpecial: true
                        isActive: root.altOn
                        keyColor: "#333"
                        onKeyPressed: keyboard.toggleAlt()
                    }

                    // Space bar
                    Comp.KeyButton {
                        keyText: "space"
                        displayText: ""
                        keyWidth: root.keyW * 6
                        keyHeight: root.keyH
                        keyColor: "#3a3a3a"
                        onKeyPressed: keyboard.pressSpecialKey("space")
                    }

                    Comp.KeyButton {
                        keyText: "alt"
                        displayText: "Alt"
                        keyWidth: root.keyW * 1.1
                        keyHeight: root.keyH
                        fontSize: 11
                        isSpecial: true
                        isActive: root.altOn
                        keyColor: "#333"
                        onKeyPressed: keyboard.toggleAlt()
                    }

                    Comp.KeyButton {
                        keyText: "ctrl"
                        displayText: "Ctrl"
                        keyWidth: root.keyW * 1.2
                        keyHeight: root.keyH
                        fontSize: 11
                        isSpecial: true
                        isActive: root.ctrlOn
                        keyColor: "#333"
                        onKeyPressed: keyboard.toggleCtrl()
                    }
                }
            }

            // ===== Navigation Panel (toggleable) =====
            Rectangle {
                visible: root.showNavigation
                Layout.fillHeight: true
                Layout.preferredWidth: 1
                color: "#333"
            }
            
            Comp.NavigationPanel {
                visible: root.showNavigation
                keyW: root.keyW * 0.9
                keyH: root.keyH * 0.9
            }

            // ===== Numpad (toggleable) =====
            Rectangle {
                visible: root.showNumpad
                Layout.fillHeight: true
                Layout.preferredWidth: 1
                color: "#333"
            }
            
            Comp.NumpadPanel {
                visible: root.showNumpad
                keyW: root.keyW * 0.9
                keyH: root.keyH * 0.9
            }
        }

        // Debug Panel
        Comp.DebugPanel {
            id: debugPanelComp
            visible: root.showDebugPanel
            anchors.bottom: parent.bottom
            anchors.left: parent.left
            anchors.margins: 8
            
            logEntries: root.debugLog
            currentContext: root.debugContext
            currentPredictions: root.predictions
            
            onCloseRequested: {
                root.showDebugPanel = false
                if (keyboard) keyboard.setDebugMode(false)
            }
            
            onClearLog: {
                if (keyboard) keyboard.clearDebugLog()
            }
        }
        
        // No synth tool warning
        Rectangle {
            visible: keyboard ? !keyboard.synthAvailable : true
            anchors.bottom: parent.bottom
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottomMargin: 2
            width: warningText.width + 12
            height: 18
            radius: 3
            color: "#442200"
            border.color: "#664400"

            Text {
                id: warningText
                anchors.centerIn: parent
                text: "xdotool not found"
                color: "#ffaa44"
                font.pixelSize: 9
            }
        }
        
        // Resize handle — left edge (grows/shrinks from left, window slides)
        MouseArea {
            id: leftResize
            anchors.left: parent.left
            anchors.top: titleBar.bottom
            anchors.bottom: parent.bottom
            width: 8
            cursorShape: Qt.SizeHorCursor

            property real startX
            property real startW
            property real startWinX

            onPressed: function(mouse) {
                var global = mapToGlobal(mouse.x, mouse.y)
                startX = global.x
                startW = root.width
                startWinX = root.x
            }

            onPositionChanged: function(mouse) {
                if (pressed) {
                    var global = mapToGlobal(mouse.x, mouse.y)
                    var dw = global.x - startX
                    var newW = Math.max(root.minimumWidth, startW - dw)
                    root.x = startWinX + (startW - newW)
                    root.width = newW
                }
            }

            // Visual grip indicator
            Column {
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                anchors.margins: 2
                spacing: 3
                Repeater {
                    model: 4
                    Rectangle { width: 3; height: 3; radius: 1.5; color: "#555" }
                }
            }
        }

        // Resize handle — right edge (grows/shrinks from right)
        MouseArea {
            id: rightResize
            anchors.right: parent.right
            anchors.top: titleBar.bottom
            anchors.bottom: parent.bottom
            width: 8
            cursorShape: Qt.SizeHorCursor

            property real startX
            property real startW

            onPressed: function(mouse) {
                var global = mapToGlobal(mouse.x, mouse.y)
                startX = global.x
                startW = root.width
            }

            onPositionChanged: function(mouse) {
                if (pressed) {
                    var global = mapToGlobal(mouse.x, mouse.y)
                    var dw = global.x - startX
                    root.width = Math.max(root.minimumWidth, startW + dw)
                }
            }

            // Visual grip indicator
            Column {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.margins: 2
                spacing: 3
                Repeater {
                    model: 4
                    Rectangle { width: 3; height: 3; radius: 1.5; color: "#555" }
                }
            }
        }
    }

    // ===== Settings Popup Window =====
    Window {
        id: settingsWindow
        title: "Alpha-OSK Settings"
        visible: root.showSettings
        width: 360
        minimumWidth: 320
        height: 540
        minimumHeight: 300
        // Frameless so we can draw our own drag handle; stays on top
        flags: Qt.Window | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool

        // Position to the right of the main keyboard when first shown
        onVisibleChanged: {
            if (visible) {
                settingsWindow.x = root.x + root.width + 8
                settingsWindow.y = root.y
            }
        }

        // Sync close button with main showSettings flag
        onClosing: root.showSettings = false

        color: "#1e1e1e"

        Comp.UnifiedSettingsPanel {
            anchors.fill: parent

            showFunctionRow: root.showFunctionRow
            showNavigation: root.showNavigation
            showNumpad: root.showNumpad
            currentTheme: root.currentTheme
            debugMode: root.showDebugPanel

            onSettingChanged: function(setting, value) {
                if (setting === "functionRow") root.showFunctionRow = value
                else if (setting === "navigation") root.showNavigation = value
                else if (setting === "numpad") root.showNumpad = value
                else if (setting === "theme") root.currentTheme = value
                else if (setting === "debugMode") {
                    root.showDebugPanel = value
                    if (keyboard) keyboard.setDebugMode(value)
                }
            }

            onCloseRequested: root.showSettings = false
        }
    }
}
