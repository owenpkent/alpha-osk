import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Window 2.15
import QtCore
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

    // Persistent settings — saved automatically on change, restored on launch
    Settings {
        id: appSettings
        category: "ui"
        property bool savedShowNavigation: true
        property bool savedShowNumpad: false
        property bool savedShowFunctionRow: false
        property string savedTheme: "dark"
        property bool savedSuggestionsEnabled: true
        property real savedWindowOpacity: 1.0
        property string savedLayout: "qwerty"
        property bool savedAudioEnabled: false
    }

    // Position once at startup — do NOT bind x/y to width/height or resize
    // will feel inverted (window re-centers on every pixel change)
    Component.onCompleted: {
        // Load saved preferences
        root.showNavigation = appSettings.savedShowNavigation
        root.showNumpad = appSettings.savedShowNumpad
        root.showFunctionRow = appSettings.savedShowFunctionRow
        root.currentTheme = appSettings.savedTheme
        root.suggestionsEnabled = appSettings.savedSuggestionsEnabled

        // Load audio setting
        if (keyboard && appSettings.savedAudioEnabled) {
            keyboard.setAudioEnabled(true)
        }

        // Load saved keyboard layout
        if (keyboard && appSettings.savedLayout !== "qwerty") {
            keyboard.setLayout(appSettings.savedLayout)
            root.currentLayout = appSettings.savedLayout
        }
        root.layoutRows = keyboard ? keyboard.getLayoutRows() : []

        // Set initial width accounting for saved panel state
        var w = 880
        if (root.showNavigation) w += 200
        if (root.showNumpad) w += 220
        root.width = w

        root.x = (Screen.width - root.width) / 2
        root.y = Screen.height - root.height - 40
        root._loaded = true
    }

    // When side panels toggle, grow/shrink from the right edge (left stays put)
    // Deltas sized to keep main keys ~same size (≈ 2.7*keyW+17 for nav, 3.6*keyW+19 for numpad at default scale)
    onShowNavigationChanged: {
        if (_loaded) root.width += showNavigation ? 200 : -200
        appSettings.savedShowNavigation = showNavigation
    }
    onShowNumpadChanged: {
        if (_loaded) root.width += showNumpad ? 220 : -220
        appSettings.savedShowNumpad = showNumpad
    }

    // Clear suggestions when the window loses activation (user clicked away)
    onActiveChanged: {
        if (!active && keyboard) keyboard.clearPredictions()
    }

    // Window transparency (0.3 = very transparent, 1.0 = fully opaque)
    property real windowOpacity: appSettings.savedWindowOpacity

    // Audio feedback
    property bool audioEnabled: appSettings.savedAudioEnabled

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

    // Keyboard layout (data-driven from JSON)
    property var layoutRows: keyboard ? keyboard.getLayoutRows() : []
    property string currentLayout: appSettings.savedLayout

    // Layout toggles (modular panels)
    property bool showFunctionRow: false
    property bool showNavigation: false
    property bool showNumpad: false
    property bool showSettings: false
    property bool suggestionsEnabled: true

    // Debug
    property bool showDebugPanel: false

    // Guard to prevent double width adjustments during startup
    property bool _loaded: false
    property var debugLog: []
    property string debugContext: ""

    // Sizing — keys scale dynamically with window width using closed-form calculation.
    // All visible panels share the window width proportionally, avoiding static estimates.
    property real keySpacing: 3

    // Total key-width units across all visible sections:
    // Widest row is the number row: Esc(1) + `(1) + 10 nums + -(1) + Backspace(1.5) = 14.5 units
    // Nav panel: 3 keys × 0.9 = 2.7 units;  Numpad: 4 keys × 0.9 = 3.6 units
    property real totalKeyUnits: 14.5
        + (showNavigation ? 2.7 : 0)
        + (showNumpad ? 3.6 : 0)

    // Fixed-pixel overhead: margins(8×2=16) + number-row gaps(14×3=42)
    // + per-panel: separator(1) + panel gaps + 2×RowLayout spacing(6)
    // Nav: 1 + 2×2 + 2×6 = 17;  Numpad: 1 + 3×2 + 2×6 = 19
    property real layoutFixedPixels: 58
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
    property string currentTheme: "dark"

    // Theme definitions — add new themes here, everything else updates automatically
    property var themeData: ({
        "dark":   { background: "#1a1a1a", keyColor: "#3a3a3a", keyPressed: "#5a5a5a", textColor: "#e0e0e0", accent: "#4a9eff", border: "#505050" },
        "light":  { background: "#e8e8e8", keyColor: "#ffffff", keyPressed: "#d0d0d0", textColor: "#1a1a1a", accent: "#0078d4", border: "#c0c0c0" },
        "blue":   { background: "#1a2a3a", keyColor: "#2a4a6a", keyPressed: "#3a6a9a", textColor: "#e0e0e0", accent: "#4a9eff", border: "#505050" },
        "green":  { background: "#1a2a1a", keyColor: "#2a4a2a", keyPressed: "#3a6a3a", textColor: "#e0e0e0", accent: "#4aff4a", border: "#505050" },
        "purple": { background: "#2a1a3a", keyColor: "#4a2a5a", keyPressed: "#6a3a7a", textColor: "#e0e0e0", accent: "#bb66ff", border: "#505050" }
    })

    property var activeTheme: themeData[currentTheme] || themeData["dark"]

    // Public theme color properties — used by all components
    property color themeBackground: activeTheme.background
    property color themeKeyColor: activeTheme.keyColor
    property color themeKeyPressed: activeTheme.keyPressed
    property color themeTextColor: activeTheme.textColor
    property color themeAccent: activeTheme.accent
    property color themeBorder: activeTheme.border

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
        
        // Layout updates
        function onLayoutDataChanged(rows) { root.layoutRows = rows }

        // Debug updates
        function onDebugLogChanged(log) { root.debugLog = log }
    }

    // Main background — uses Qt.rgba so only the background becomes transparent
    // while keys and text remain fully opaque
    Rectangle {
        id: background
        anchors.fill: parent
        radius: 10
        color: Qt.rgba(root.themeBackground.r, root.themeBackground.g, root.themeBackground.b, root.windowOpacity)
        border.color: Qt.rgba(root.themeBorder.r, root.themeBorder.g, root.themeBorder.b, root.windowOpacity)
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
            property color baseColor: Qt.darker(root.themeBackground, 1.1)
            color: Qt.rgba(baseColor.r, baseColor.g, baseColor.b, root.windowOpacity)
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
                anchors.rightMargin: 155  // Leave space for buttons
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
                
                // Suggestions toggle button
                Rectangle {
                    width: 28
                    height: 24
                    radius: 4
                    color: sugToggle.containsMouse ? "#444" : "transparent"

                    Text {
                        anchors.centerIn: parent
                        text: "Aa"
                        font.pixelSize: 12
                        font.weight: Font.Medium
                        color: root.suggestionsEnabled ? "#4a9eff" : "#555"
                        font.strikeout: !root.suggestionsEnabled
                    }

                    MouseArea {
                        id: sugToggle
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            root.suggestionsEnabled = !root.suggestionsEnabled
                            appSettings.savedSuggestionsEnabled = root.suggestionsEnabled
                            if (!root.suggestionsEnabled && keyboard) keyboard.clearPredictions()
                        }
                    }
                }

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

                // ===== Prediction Bar (fixed height to prevent window resizing) =====
                Item {
                    Layout.fillWidth: true
                    Layout.preferredHeight: root.suggestionsEnabled ? 40 : 0
                    Layout.bottomMargin: root.suggestionsEnabled ? 4 : 0
                    clip: true

                    Behavior on Layout.preferredHeight { NumberAnimation { duration: 150 } }

                    Row {
                        anchors.centerIn: parent
                        spacing: 8
                        visible: root.suggestionsEnabled

                        Repeater {
                            model: root.suggestionsEnabled && root.predictions.length > 0 ? root.predictions : []
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
                }

                // ===== Data-Driven Keyboard Rows =====
                Repeater {
                    model: root.layoutRows

                    Row {
                        Layout.alignment: Qt.AlignHCenter
                        spacing: root.keySpacing
                        property var rowData: modelData
                        property real rowKeyH: rowData.id === "number" ? root.keyH - 4 : root.keyH

                        Repeater {
                            model: rowData.keys

                            Comp.KeyButton {
                                property var kd: modelData
                                keyText: kd.key || kd.action || ""
                                displayText: {
                                    if (kd.type === "char") {
                                        if (kd.shifted && root.shiftOn) return kd.shifted
                                        if (kd.key && kd.key.length === 1 && /[a-z]/.test(kd.key))
                                            return root.shiftOn ? kd.key.toUpperCase() : kd.key
                                        return kd.display || kd.key
                                    }
                                    return kd.display || ""
                                }
                                keyWidth: root.keyW * (kd.width || 1.0)
                                keyHeight: rowKeyH
                                fontSize: kd.fontSize || 16
                                isSpecial: kd.type !== "char"
                                isActive: {
                                    if (!kd.stateKey) return false
                                    switch(kd.stateKey) {
                                        case "shiftOn": return root.shiftOn
                                        case "capsOn": return root.capsOn
                                        case "ctrlOn": return root.ctrlOn
                                        case "altOn": return root.altOn
                                        case "winOn": return root.winOn
                                        default: return false
                                    }
                                }
                                keyColor: {
                                    switch(kd.style || "default") {
                                        case "secondary": return Qt.darker(root.themeKeyColor, 1.3)
                                        case "special": return Qt.darker(root.themeKeyColor, 1.15)
                                        case "enter": return "#2a5a2a"
                                        default: return root.themeKeyColor
                                    }
                                }
                                keyPressedColor: root.themeKeyPressed
                                keyTextColor: root.themeTextColor

                                onKeyPressed: {
                                    if (kd.type === "char") {
                                        var ch = root.shiftOn && kd.shifted ? kd.shifted : kd.key
                                        keyboard.pressKey(ch)
                                    } else if (kd.type === "modifier") {
                                        switch(kd.action) {
                                            case "shift": keyboard.toggleShift(); break
                                            case "caps": keyboard.toggleCapsLock(); break
                                            case "ctrl": keyboard.toggleCtrl(); break
                                            case "alt": keyboard.toggleAlt(); break
                                            case "win": keyboard.toggleWin(); break
                                        }
                                    } else {
                                        keyboard.pressSpecialKey(kd.action)
                                    }
                                }
                            }
                        }
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
            themeData: root.themeData
            windowOpacity: root.windowOpacity
            currentLayout: root.currentLayout
            audioEnabled: root.audioEnabled
            suggestionsEnabled: root.suggestionsEnabled
            predictionCount: keyboard ? keyboard.predictionCount : 8
            debugMode: root.showDebugPanel

            onSettingChanged: function(setting, value) {
                if (setting === "functionRow") {
                    root.showFunctionRow = value
                    appSettings.savedShowFunctionRow = value
                } else if (setting === "navigation") {
                    root.showNavigation = value
                } else if (setting === "numpad") {
                    root.showNumpad = value
                } else if (setting === "theme") {
                    root.currentTheme = value
                    appSettings.savedTheme = value
                } else if (setting === "windowOpacity") {
                    root.windowOpacity = value
                    appSettings.savedWindowOpacity = value
                } else if (setting === "layout") {
                    if (keyboard) keyboard.setLayout(value)
                    root.currentLayout = value
                    appSettings.savedLayout = value
                } else if (setting === "audio") {
                    if (keyboard) keyboard.setAudioEnabled(value)
                    root.audioEnabled = value
                    appSettings.savedAudioEnabled = value
                } else if (setting === "suggestions") {
                    root.suggestionsEnabled = value
                    appSettings.savedSuggestionsEnabled = value
                    if (!value && keyboard) keyboard.clearPredictions()
                } else if (setting === "predictionCount") {
                    if (keyboard) keyboard.setPredictionCount(value)
                } else if (setting === "debugMode") {
                    root.showDebugPanel = value
                    if (keyboard) keyboard.setDebugMode(value)
                }
            }

            onCloseRequested: root.showSettings = false
        }
    }
}
