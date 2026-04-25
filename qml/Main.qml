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
    width: 940
    height: outerLayout.implicitHeight + 60  // Extra height for title bar + bottom padding
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
        property bool savedAutoSpaceAfterPunctuation: true
        property bool savedAutoCapitalizeAfterPunctuation: false
        property bool savedAutoSaveOnExit: true
        property bool savedSwipeEnabled: false
        property bool savedAutoCheckUpdates: true
        // Window geometry — restored on launch, saved (debounced) on
        // resize.  0 means "no saved value yet, use the binding-driven
        // default" — that path runs on a fresh install.
        property int savedWindowWidth: 0
        property int savedWindowHeight: 0
    }

    // Set when Component.onCompleted finishes restoring the saved
    // geometry.  Width/height-changed handlers gate on this so the
    // restore itself doesn't fire a no-op save.
    property bool _geometryRestored: false

    // Debounce window-resize writes — onWidthChanged / onHeightChanged
    // fire on every pixel during a drag, and Settings.write hits the
    // OS registry/config synchronously.  Wait 300 ms after the last
    // change before persisting.
    Timer {
        id: saveGeometryTimer
        interval: 300
        repeat: false
        onTriggered: {
            if (root._geometryRestored) {
                appSettings.savedWindowWidth = root.width
                appSettings.savedWindowHeight = root.height
            }
        }
    }
    onWidthChanged: if (_geometryRestored) saveGeometryTimer.restart()
    onHeightChanged: if (_geometryRestored) saveGeometryTimer.restart()

    // Auto-update — bridge fills these in when checkForUpdate() finds
    // a signed newer release.  See src/updater.py for the security model.
    property bool autoCheckUpdates: true
    property bool updateAvailable: false
    property string updateVersion: ""
    property string updateNotes: ""
    property bool updateInstalling: false
    property string updateError: ""
    // "" / "checking" / "uptodate" / "available" / "failed" — drives the
    // settings-panel status text after a manual "Check now".  Auto-checks
    // also update this so the panel reflects reality if the user opens
    // it after a silent background check.
    property string _lastCheckStatus: ""

    // Single-shot timer that delays the startup update check so it
    // doesn't compete with QML/QQmlApplicationEngine init for CPU.
    Timer {
        id: updateCheckTimer
        interval: 3000
        repeat: false
        onTriggered: if (root.autoCheckUpdates && keyboard) keyboard.checkForUpdate()
    }

    // Position once at startup — do NOT bind x/y to width/height or resize
    // will feel inverted (window re-centers on every pixel change)
    Component.onCompleted: {
        // Restore saved window geometry first so the user gets the
        // size they had last time, not a flash of the default size
        // followed by a resize.  0 means "no value persisted yet".
        if (appSettings.savedWindowWidth > 0)
            root.width = Math.max(root.minimumWidth, appSettings.savedWindowWidth)
        if (appSettings.savedWindowHeight > 0)
            root.height = Math.max(root.minimumHeight, appSettings.savedWindowHeight)
        root._geometryRestored = true

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

        // Load punctuation and auto-save settings
        if (keyboard) {
            keyboard.setAutoSpaceAfterPunctuation(appSettings.savedAutoSpaceAfterPunctuation)
            keyboard.setAutoCapitalizeAfterPunctuation(appSettings.savedAutoCapitalizeAfterPunctuation)
            keyboard.setAutoSaveOnExit(appSettings.savedAutoSaveOnExit)
            keyboard.setSwipeEnabled(appSettings.savedSwipeEnabled)
        }

        // Auto-update setting — kicks off the background check after a
        // 3-second delay (see updateCheckTimer) so startup isn't blocked
        // on a network round-trip.
        root.autoCheckUpdates = appSettings.savedAutoCheckUpdates
        if (root.autoCheckUpdates) updateCheckTimer.start()

        // Load saved keyboard layout
        if (keyboard && appSettings.savedLayout !== "qwerty") {
            keyboard.setLayout(appSettings.savedLayout)
            root.currentLayout = appSettings.savedLayout
        }
        root.layoutRows = keyboard ? keyboard.getLayoutRows() : []

        // Set initial width accounting for saved panel state
        var w = 940
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

    // Refresh the swipe-recognizer layout whenever the window is resized —
    // key positions move with the layout.  (See the merged onWidthChanged
    // handler further down which also handles minimumWidth clamping.)
    onHeightChanged: swipeLayoutPushTimer.restart()

    onSwipeEnabledChanged: {
        appSettings.savedSwipeEnabled = swipeEnabled
        if (keyboard) keyboard.setSwipeEnabled(swipeEnabled)
        if (swipeEnabled) pushSwipeLayout()
    }

    // Window transparency (0.3 = very transparent, 1.0 = fully opaque)
    property real windowOpacity: appSettings.savedWindowOpacity

    // Audio feedback
    property bool audioEnabled: appSettings.savedAudioEnabled

    // Auto-space and auto-capitalize after punctuation
    property bool autoSpaceAfterPunctuation: appSettings.savedAutoSpaceAfterPunctuation
    property bool autoCapitalizeAfterPunctuation: appSettings.savedAutoCapitalizeAfterPunctuation

    // Auto-save prediction model on exit
    property bool autoSaveOnExit: appSettings.savedAutoSaveOnExit

    // Swipe / glide typing — when on, dragging across keys decodes a word.
    property bool swipeEnabled: appSettings.savedSwipeEnabled

    // Char-key registry — populated by each KeyButton on creation; consumed
    // by SwipeOverlay for hit testing and by buildSwipeLayout() for the
    // recogniser's key-centre map.
    property var charKeyRegistry: []

    function registerCharKey(item, kd) {
        if (!kd || kd.type !== "char" || !kd.key || kd.key.length !== 1) return
        charKeyRegistry.push({ item: item, kd: kd })
        swipeLayoutPushTimer.restart()
    }

    function unregisterCharKey(item) {
        for (var i = 0; i < charKeyRegistry.length; i++) {
            if (charKeyRegistry[i].item === item) {
                charKeyRegistry.splice(i, 1)
                break
            }
        }
        swipeLayoutPushTimer.restart()
    }

    // Coalesce many register/unregister calls during a layout swap into one
    // setSwipeLayout push to Python.
    Timer {
        id: swipeLayoutPushTimer
        interval: 100
        repeat: false
        onTriggered: root.pushSwipeLayout()
    }

    function pushSwipeLayout() {
        if (!keyboard) return
        // Push key centres in the same coordinate frame the SwipeOverlay
        // uses for its trace (overlay-local), so the recogniser sees both
        // in matching units.
        var overlay = (typeof swipeOverlay !== "undefined") ? swipeOverlay : null
        if (!overlay) return
        var centers = ({})
        for (var i = 0; i < charKeyRegistry.length; i++) {
            var entry = charKeyRegistry[i]
            if (!entry.item || !entry.kd || !entry.kd.key) continue
            var p = overlay.mapFromItem(entry.item,
                                        entry.item.width / 2,
                                        entry.item.height / 2)
            centers[entry.kd.key.toLowerCase()] = [p.x, p.y]
        }
        keyboard.setSwipeLayout(centers)
    }

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
    property bool showHelp: false
    property bool suggestionsEnabled: true

    // Privacy
    property bool privacyMode: keyboard ? keyboard.privacyMode : false

    // Visualization
    property bool showVisualization: false

    // Debug
    property bool showDebugPanel: false

    // Guard to prevent double width adjustments during startup
    property bool _loaded: false
    property var debugLog: []
    property string debugContext: ""

    // Sizing — keys scale dynamically with window width using closed-form calculation.
    // All visible panels share the window width proportionally, avoiding static estimates.
    property real keySpacing: Math.max(1, Math.floor(root.width * 0.0025))

    // Total key-width units across all visible sections:
    // Widest row is the number row (15 keys): Esc(1) + `(1) + 10 nums + -(1) + =(1) + Backspace(1.5) = 15.5 units
    // Nav panel: 3 keys × 0.9 = 2.7 units;  Numpad: 4 keys × 0.9 = 3.6 units
    property real totalKeyUnits: 15.5
        + (showNavigation ? 2.7 : 0)
        + (showNumpad ? 3.6 : 0)

    // Fixed-pixel overhead: margins(8×2=16) + number-row gaps(15 keys → 14×keySpacing)
    // + per-panel: separator(1) + 2 inner grid gaps + 2×RowLayout spacing(6)
    property real layoutFixedPixels: 16 + 14 * keySpacing
        + (showNavigation ? 1 + 2 * keySpacing + 12 : 0)
        + (showNumpad ? 1 + 3 * keySpacing + 12 : 0)

    property real keyW: Math.max(30, (root.width - layoutFixedPixels) / totalKeyUnits)
    property real keyH: Math.max(34, keyW * 0.89)

    // Safety net: if the window width ever drops below minimumWidth (e.g. via
    // OS window-snap, DPI change, or panel toggle), clamp it back up.  Also
    // refreshes the swipe-recognizer's key-centre map since the keys move
    // when the window resizes.
    onWidthChanged: {
        if (width < minimumWidth) width = minimumWidth
        swipeLayoutPushTimer.restart()
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
        "dark":       { name: "Dark",       background: "#1a1a1a", keyColor: "#3a3a3a", keyPressed: "#5a5a5a", textColor: "#e0e0e0", accent: "#4a9eff", border: "#505050", animation: "" },
        "light":      { name: "Light",      background: "#e8e8e8", keyColor: "#ffffff", keyPressed: "#d0d0d0", textColor: "#1a1a1a", accent: "#0078d4", border: "#c0c0c0", animation: "" },
        "blue":       { name: "Ocean",      background: "#1a2a3a", keyColor: "#2a4a6a", keyPressed: "#3a6a9a", textColor: "#e0e0e0", accent: "#4a9eff", border: "#505050", animation: "" },
        "green":      { name: "Forest",     background: "#1a2a1a", keyColor: "#2a4a2a", keyPressed: "#3a6a3a", textColor: "#e0e0e0", accent: "#4aff4a", border: "#505050", animation: "" },
        "purple":     { name: "Amethyst",   background: "#2a1a3a", keyColor: "#4a2a5a", keyPressed: "#6a3a7a", textColor: "#e0e0e0", accent: "#bb66ff", border: "#505050", animation: "" },
        "vaporwave":  { name: "Vaporwave",  background: "#1a0a2e", keyColor: "#2d1b4e", keyPressed: "#4a2d7a", textColor: "#ff71ce", accent: "#01cdfe", border: "#b967ff", animation: "gradient" },
        "blackboard": { name: "Blackboard", background: "#2c3e2c", keyColor: "#3d5a3d", keyPressed: "#4e6e4e", textColor: "#e8e8d0", accent: "#ffffaa", border: "#4a6a4a", animation: "" },
        "typewriter": { name: "Typewriter", background: "#f5f0e8", keyColor: "#d4c9b0", keyPressed: "#c0b090", textColor: "#2c2416", accent: "#8b4513", border: "#a08060", animation: "" },
        "spaceship":  { name: "Spaceship",  background: "#040d04", keyColor: "#0a1f0a", keyPressed: "#153015", textColor: "#00e676", accent: "#00ff9f", border: "#0d3b0d", animation: "stars" }
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

        // Auto-update — see src/updater.py.  We never receive the
        // download URL here (Python keeps it); we just toggle the
        // banner and forward the user's Install/Later click back to
        // the bridge.
        function onUpdateAvailable(version, assetName, notes) {
            root.updateVersion = version
            root.updateNotes = notes
            root.updateError = ""
            root.updateAvailable = true
            root._lastCheckStatus = "available"
        }
        function onUpdateUnavailable() {
            // Quiet — no banner when there's nothing new.  The settings
            // panel reads _lastCheckStatus to show "Up to date." after
            // a manual "Check now".
            root._lastCheckStatus = "uptodate"
        }
        function onUpdateInstallStarted() {
            root.updateInstalling = true
            root.updateError = ""
        }
        function onUpdateInstallFailed(msg) {
            root.updateInstalling = false
            root.updateError = msg
            root._lastCheckStatus = "failed"
        }
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

        // ===== Theme Animation Overlay =====
        Canvas {
            id: themeAnimCanvas
            anchors.fill: parent
            visible: root.activeTheme.animation !== ""
            opacity: 0.15
            z: 0

            property real tick: 0
            property var starField: []

            NumberAnimation on tick {
                from: 0; to: 10000
                duration: 1000000
                loops: Animation.Infinite
                running: themeAnimCanvas.visible
            }

            onTickChanged: if (visible) requestPaint()

            Component.onCompleted: {
                // Generate star field for spaceship theme
                var stars = []
                for (var i = 0; i < 40; i++) {
                    stars.push({
                        x: Math.random(),
                        y: Math.random(),
                        r: Math.random() * 1.5 + 0.5,
                        speed: Math.random() * 0.3 + 0.1,
                        phase: Math.random() * Math.PI * 2
                    })
                }
                starField = stars
            }

            onPaint: {
                var ctx = getContext("2d")
                ctx.clearRect(0, 0, width, height)
                var anim = root.activeTheme.animation

                if (anim === "gradient") {
                    // Vaporwave: slow horizontal gradient shift
                    var shift = (tick * 0.5) % 360
                    var grad = ctx.createLinearGradient(0, 0, width, height)
                    var hue1 = (shift) % 360
                    var hue2 = (shift + 60) % 360
                    grad.addColorStop(0, Qt.hsla(hue1 / 360, 0.6, 0.3, 1))
                    grad.addColorStop(1, Qt.hsla(hue2 / 360, 0.6, 0.3, 1))
                    ctx.fillStyle = grad
                    // Clip to rounded rect
                    ctx.beginPath()
                    ctx.roundedRect(0, 0, width, height, 10, 10)
                    ctx.fill()

                } else if (anim === "pulse") {
                    // Neon: border glow pulse
                    var pulse = Math.sin(tick * 0.15) * 0.5 + 0.5
                    ctx.strokeStyle = Qt.rgba(0.22, 1.0, 0.08, pulse)
                    ctx.lineWidth = 2 + pulse * 2
                    ctx.beginPath()
                    ctx.roundedRect(1, 1, width - 2, height - 2, 10, 10)
                    ctx.stroke()

                } else if (anim === "stars") {
                    // Spaceship: twinkling star field
                    for (var i = 0; i < starField.length; i++) {
                        var s = starField[i]
                        var twinkle = Math.sin(tick * s.speed + s.phase) * 0.5 + 0.5
                        ctx.beginPath()
                        ctx.arc(s.x * width, s.y * height, s.r * twinkle + 0.3, 0, Math.PI * 2)
                        ctx.fillStyle = Qt.rgba(0, 0.9, 0.47, twinkle * 0.8)
                        ctx.fill()
                    }
                }
            }
        }

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

                // Update-available indicator (only visible when an update
                // is pending). Click opens a small popup with version
                // info and Install / Later buttons. Replaces the older
                // full-width banner — taking up an OSK row for a passive
                // notification was too much screen real estate.
                Rectangle {
                    id: updateIcon
                    width: 28
                    height: 24
                    radius: 4
                    visible: root.updateAvailable
                    color: updateBtnArea.containsMouse ? "#444" : "transparent"
                    border.color: root.updateError !== "" ? "#c33" : root.themeAccent
                    border.width: 1

                    Text {
                        anchors.centerIn: parent
                        text: root.updateInstalling ? "…" : "↓"
                        font.pixelSize: 14
                        font.bold: true
                        color: root.updateError !== "" ? "#c33" : root.themeAccent
                    }

                    MouseArea {
                        id: updateBtnArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: updatePopup.open()
                    }

                    Popup {
                        id: updatePopup
                        // Anchor the popup's right edge to the icon's
                        // right edge so it hangs down-and-to-the-left
                        // and never overflows the window's right side.
                        x: parent.width - width
                        y: parent.height + 4
                        width: 260
                        // Bumps with content so we don't need explicit height math.
                        padding: 12
                        modal: false
                        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent

                        background: Rectangle {
                            color: root.themeBackground
                            border.color: root.themeAccent
                            border.width: 1
                            radius: 6
                        }

                        contentItem: ColumnLayout {
                            spacing: 8

                            Text {
                                Layout.fillWidth: true
                                text: root.updateError !== ""
                                      ? qsTr("Update failed")
                                      : (root.updateInstalling
                                         ? qsTr("Installing v%1…").arg(root.updateVersion)
                                         : qsTr("Alpha-OSK v%1 available").arg(root.updateVersion))
                                color: root.themeTextColor
                                font.pixelSize: 14
                                font.bold: true
                                wrapMode: Text.WordWrap
                            }

                            Text {
                                Layout.fillWidth: true
                                text: root.updateError !== ""
                                      ? root.updateError
                                      : qsTr("Installing will close and relaunch the app.")
                                color: Qt.darker(root.themeTextColor, 1.4)
                                font.pixelSize: 11
                                wrapMode: Text.WordWrap
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                Layout.topMargin: 4
                                spacing: 6

                                Item { Layout.fillWidth: true }

                                Button {
                                    text: root.updateError !== "" ? qsTr("Retry") : qsTr("Install")
                                    enabled: !root.updateInstalling
                                    onClicked: {
                                        root.updateError = ""
                                        keyboard.installUpdate()
                                        updatePopup.close()
                                    }
                                }
                                Button {
                                    text: qsTr("Later")
                                    enabled: !root.updateInstalling
                                    onClicked: {
                                        keyboard.dismissUpdate()
                                        root.updateAvailable = false
                                        root.updateError = ""
                                        updatePopup.close()
                                    }
                                }
                            }
                        }
                    }
                }

                // Privacy mode toggle (play/pause learning)
                Rectangle {
                    width: 28
                    height: 24
                    radius: 4
                    color: root.privacyMode ? "#4a2a2a" : privacyBtn.containsMouse ? "#444" : "transparent"
                    border.color: root.privacyMode ? "#ff6b6b" : "transparent"
                    border.width: root.privacyMode ? 1 : 0

                    Canvas {
                        id: privacyIcon
                        anchors.centerIn: parent
                        width: 14; height: 14
                        onPaint: {
                            var ctx = getContext("2d")
                            ctx.clearRect(0, 0, width, height)
                            ctx.fillStyle = root.privacyMode ? "#ff6b6b" : "#999"
                            if (root.privacyMode) {
                                // Pause icon: two vertical bars
                                ctx.fillRect(2, 1, 3.5, 12)
                                ctx.fillRect(8.5, 1, 3.5, 12)
                            } else {
                                // Play icon: right-pointing triangle
                                ctx.beginPath()
                                ctx.moveTo(2, 1)
                                ctx.lineTo(13, 7)
                                ctx.lineTo(2, 13)
                                ctx.closePath()
                                ctx.fill()
                            }
                        }
                        Connections {
                            target: root
                            // Inside Connections, `parent` doesn't
                            // resolve to the enclosing Canvas — Qt
                            // logs "ReferenceError: parent is not
                            // defined" on every privacy-mode toggle
                            // and the icon glyph silently doesn't
                            // repaint.  Reference the Canvas by id.
                            function onPrivacyModeChanged() { privacyIcon.requestPaint() }
                        }
                    }

                    MouseArea {
                        id: privacyBtn
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (keyboard) keyboard.setPrivacyMode(!root.privacyMode)
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
                        color: root.showSettings ? root.themeAccent : "#999"
                    }
                    
                    MouseArea {
                        id: settingsBtn
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.showSettings = !root.showSettings
                    }
                }
                
                // Standard Windows minimize. Drops the OSK to the
                // taskbar; click the taskbar entry to restore. Works
                // because we no longer apply Qt.Tool / WS_EX_TOOLWINDOW
                // (see _apply_window_flags), which were keeping us
                // out of the taskbar entirely.
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
                        onClicked: root.showMinimized()
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

        ColumnLayout {
            id: outerLayout
            anchors.fill: parent
            anchors.margins: 8
            anchors.topMargin: 32  // Account for title bar
            spacing: 0

            // ===== Update Banner =====
            // The update notification used to live here as a full-width
            // banner. It's now a small ↓ icon in the title bar (next to
            // the privacy toggle) that opens a popup; see updateIcon
            // around line 521. Comment retained as a breadcrumb for
            // future "where did the banner go" debugging.

            // ===== Prediction Bar (spans full width including nav/numpad) =====
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: root.suggestionsEnabled ? 40 : 0
                Layout.bottomMargin: root.suggestionsEnabled ? 4 : 0
                clip: true

                Behavior on Layout.preferredHeight { NumberAnimation { duration: 150 } }

                // Privacy mode indicator (replaces predictions)
                Row {
                    anchors.centerIn: parent
                    spacing: 6
                    visible: root.suggestionsEnabled && root.privacyMode

                    Canvas {
                        width: 12; height: 12
                        anchors.verticalCenter: parent.verticalCenter
                        onPaint: {
                            var ctx = getContext("2d")
                            ctx.fillStyle = "#ff6b6b"
                            ctx.fillRect(1, 0, 3.5, 12)
                            ctx.fillRect(7.5, 0, 3.5, 12)
                        }
                    }
                    Text {
                        text: "Learning paused"
                        color: "#ff8888"
                        font.pixelSize: 13
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }

                Row {
                    id: predRow
                    anchors.centerIn: parent
                    spacing: 8
                    visible: root.suggestionsEnabled && !root.privacyMode

                    Repeater {
                        model: root.suggestionsEnabled && !root.privacyMode && root.predictions.length > 0 ? root.predictions : []
                        delegate: Rectangle {
                            property real naturalWidth: Math.max(60, predText.implicitWidth + 28)
                            property real maxPillWidth: {
                                var count = root.predictions.length
                                if (count <= 0) return naturalWidth
                                var avail = root.width - 32 - (count - 1) * predRow.spacing
                                return Math.max(50, avail / count)
                            }
                            width: Math.min(naturalWidth, maxPillWidth)
                            height: 36
                            radius: 8
                            color: predMouse.containsMouse ? Qt.lighter(root.themeKeyColor, 1.3) : root.themeKeyColor
                            border.color: predMouse.containsMouse ? Qt.lighter(root.themeAccent, 1.2) : root.themeAccent
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
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.leftMargin: 8
                                anchors.rightMargin: 8
                                horizontalAlignment: Text.AlignHCenter
                                text: modelData
                                color: predMouse.containsMouse ? Qt.lighter(root.themeTextColor, 1.3) : root.themeTextColor
                                font.pixelSize: 15
                                font.weight: Font.Medium
                                font.family: "Ubuntu, Noto Sans, sans-serif"
                                elide: Text.ElideRight
                            }

                            MouseArea {
                                id: predMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                acceptedButtons: Qt.LeftButton | Qt.RightButton
                                cursorShape: Qt.PointingHandCursor
                                onClicked: function(mouse) {
                                    if (mouse.button === Qt.RightButton) {
                                        var pos = mapToItem(root.contentItem, mouse.x, mouse.y)
                                        predContextMenu.showAt(modelData, pos.x, pos.y)
                                    } else {
                                        keyboard.pressPrediction(modelData)
                                    }
                                }
                            }

                            // Reveal the full word on hover when the pill
                            // clipped it — predText.truncated is true only
                            // when ElideRight actually had to chop.
                            ToolTip.visible: predMouse.containsMouse && predText.truncated
                            ToolTip.text: modelData
                            ToolTip.delay: 400

                            // Smooth hover animation
                            Behavior on color { ColorAnimation { duration: 100 } }
                            Behavior on border.color { ColorAnimation { duration: 100 } }
                        }
                    }

                }

            }

            RowLayout {
                id: mainLayout
                Layout.fillWidth: true
                Layout.fillHeight: true
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
                        keySpacing: root.keySpacing
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
                                id: keyBtn
                                property var kd: modelData
                                Component.onCompleted: root.registerCharKey(keyBtn, kd)
                                Component.onDestruction: root.unregisterCharKey(keyBtn)
                                keyText: kd.key || kd.action || ""
                                displayText: {
                                    if (kd.type === "char") {
                                        // Shift shows the shifted glyph (e.g. "!" on "1")
                                        if (kd.shifted && root.shiftOn) return kd.shifted
                                        // Letters uppercase under shift OR caps lock
                                        if (kd.key && kd.key.length === 1 && /[a-z]/.test(kd.key))
                                            return (root.shiftOn || root.capsOn) ? kd.key.toUpperCase() : kd.key
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
                                accentColor: root.themeAccent
                                borderColor: root.themeBorder

                                // Main keyboard's only repeat-worthy key is
                                // backspace.  Character keys must not repeat
                                // (see KeyButton.qml for the rationale).
                                enableRepeat: kd.type === "special" && kd.action === "backspace"

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
                keySpacing: root.keySpacing
                keyColor: Qt.darker(root.themeKeyColor, 1.15)
                keyPressedColor: root.themeKeyPressed
                keyTextColor: root.themeTextColor
                accentColor: root.themeAccent
                borderColor: root.themeBorder
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
                keySpacing: root.keySpacing
                keyColor: root.themeKeyColor
                specialKeyColor: Qt.darker(root.themeKeyColor, 1.15)
                keyPressedColor: root.themeKeyPressed
                keyTextColor: root.themeTextColor
                enterKeyColor: "#2a5a2a"
                accentColor: root.themeAccent
                borderColor: root.themeBorder
            }
            }
        }

        // Swipe overlay — covers the main keyboard area when swipe typing
        // is on.  Sibling to mainLayout (NOT a child of mainKeyboard),
        // because re-parenting into a QtQuick.Layouts ColumnLayout makes
        // Qt warn about anchors-on-layout-managed-items even when we set
        // the parent imperatively.  Geometry is bound to mainKeyboard's
        // position/size through coordinate bindings instead.
        Comp.SwipeOverlay {
            id: swipeOverlay
            x: mainLayout.x + mainKeyboard.x
            y: mainLayout.y + mainKeyboard.y
            width: mainKeyboard.width
            height: mainKeyboard.height
            z: 50
            enabled: root.swipeEnabled
            keyboardBridge: keyboard
            keyRegistry: root.charKeyRegistry
        }

        // Custom styled context menu for prediction pills
        Popup {
            id: predContextMenu
            property string targetWord: ""
            property real popupX: 0
            property real popupY: 0

            parent: Overlay.overlay
            x: popupX
            y: popupY
            width: 200
            height: menuCol.implicitHeight + 16
            modal: true
            dim: false
            closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

            function showAt(word, globalX, globalY) {
                targetWord = word
                // Position below the click point, clamped to window
                popupX = Math.max(4, Math.min(globalX, root.width - width - 4))
                popupY = Math.max(4, Math.min(globalY + 4, root.height - height - 4))
                open()
            }

            background: Rectangle {
                color: "#252535"
                border.color: "#555"
                border.width: 1
                radius: 10
            }

            contentItem: Column {
                id: menuCol
                width: parent ? parent.width : 200
                padding: 4
                spacing: 2

                // Word label
                Text {
                    width: parent.width - 8
                    leftPadding: 12
                    topPadding: 6
                    bottomPadding: 4
                    text: predContextMenu.targetWord
                    color: "#888"
                    font.pixelSize: 11
                    elide: Text.ElideRight
                }

                // Edit
                Rectangle {
                    width: parent.width - 8
                    height: 34
                    x: 4
                    radius: 6
                    color: editMa.containsMouse ? "#3a3a5a" : "transparent"

                    Row {
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                        anchors.leftMargin: 12
                        spacing: 10
                        Text { text: "\u270E"; font.pixelSize: 14; color: "#8cf"; anchors.verticalCenter: parent.verticalCenter }
                        Text { text: "Edit"; font.pixelSize: 13; color: "#ddd"; anchors.verticalCenter: parent.verticalCenter }
                    }

                    MouseArea {
                        id: editMa
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            predContextMenu.close()
                            predEditField.originalWord = predContextMenu.targetWord
                            predEditField.text = predContextMenu.targetWord
                            predEditPopup.open()
                            predEditField.selectAll()
                            predEditField.forceActiveFocus()
                        }
                    }
                }

                // Divider
                Rectangle { width: parent.width - 24; height: 1; color: "#3a3a4a"; anchors.horizontalCenter: parent.horizontalCenter }

                // Downweight
                Rectangle {
                    width: parent.width - 8
                    height: 34
                    x: 4
                    radius: 6
                    color: badMa.containsMouse ? "#3a3a5a" : "transparent"

                    Row {
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                        anchors.leftMargin: 12
                        spacing: 10
                        Text { text: "\u25BC"; font.pixelSize: 11; color: "#fb4"; anchors.verticalCenter: parent.verticalCenter }
                        Text { text: "Show less"; font.pixelSize: 13; color: "#ddd"; anchors.verticalCenter: parent.verticalCenter }
                    }

                    MouseArea {
                        id: badMa
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (keyboard) keyboard.markBadSuggestion(predContextMenu.targetWord)
                            predContextMenu.close()
                        }
                    }
                }

                // Remove
                Rectangle {
                    width: parent.width - 8
                    height: 34
                    x: 4
                    radius: 6
                    color: removeMa.containsMouse ? "#4a2a2a" : "transparent"

                    Row {
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                        anchors.leftMargin: 12
                        spacing: 10
                        Text { text: "\u2715"; font.pixelSize: 12; color: "#f66"; anchors.verticalCenter: parent.verticalCenter }
                        Text { text: "Remove"; font.pixelSize: 13; color: "#f88"; anchors.verticalCenter: parent.verticalCenter }
                    }

                    MouseArea {
                        id: removeMa
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (keyboard) keyboard.blacklistWord(predContextMenu.targetWord)
                            predContextMenu.close()
                        }
                    }
                }

                Item { height: 2; width: 1 }
            }
        }

        // Edit prediction popup
        Popup {
            id: predEditPopup
            parent: Overlay.overlay
            x: (root.width - width) / 2
            y: 36
            width: 290
            height: 46
            // Non-modal: the popup must NOT install an event-blocking
            // overlay. If it did, OSK key MouseAreas below would never
            // fire, and the user couldn't type into the field using the
            // very keyboard that's visible. Escape or the ✕ button
            // dismisses; the bridge-level edit-mode intercept (see
            // onOpened/onClosed) routes OSK keystrokes here instead of
            // synthesising them to the OS.
            modal: false
            dim: false
            closePolicy: Popup.CloseOnEscape

            onOpened: if (keyboard) keyboard.setEditMode(true)
            onClosed: if (keyboard) keyboard.setEditMode(false)

            // While the popup is open, OSK key presses are short-
            // circuited in the bridge and routed here via these signals
            // instead of synthesising to the OS.
            Connections {
                target: keyboard
                enabled: predEditPopup.opened

                function onEditKeyTyped(ch) {
                    if (predEditField.selectedText)
                        predEditField.remove(predEditField.selectionStart, predEditField.selectionEnd)
                    predEditField.insert(predEditField.cursorPosition, ch)
                }

                function onEditSpecialPressed(name) {
                    var pos = predEditField.cursorPosition
                    var len = predEditField.length
                    if (name === "backspace") {
                        if (predEditField.selectedText)
                            predEditField.remove(predEditField.selectionStart, predEditField.selectionEnd)
                        else if (pos > 0)
                            predEditField.remove(pos - 1, pos)
                    } else if (name === "delete") {
                        if (predEditField.selectedText)
                            predEditField.remove(predEditField.selectionStart, predEditField.selectionEnd)
                        else if (pos < len)
                            predEditField.remove(pos, pos + 1)
                    } else if (name === "left") {
                        predEditField.cursorPosition = Math.max(0, pos - 1)
                    } else if (name === "right") {
                        predEditField.cursorPosition = Math.min(len, pos + 1)
                    } else if (name === "home") {
                        predEditField.cursorPosition = 0
                    } else if (name === "end") {
                        predEditField.cursorPosition = len
                    } else if (name === "space") {
                        if (predEditField.selectedText)
                            predEditField.remove(predEditField.selectionStart, predEditField.selectionEnd)
                        predEditField.insert(predEditField.cursorPosition, " ")
                    } else if (name === "return" || name === "enter") {
                        // Accept the edit
                        if (predEditField.text.trim() && keyboard)
                            keyboard.editPrediction(predEditField.originalWord, predEditField.text.trim())
                        predEditPopup.close()
                    } else if (name === "escape") {
                        predEditPopup.close()
                    }
                    // Tab, function keys, insert, etc. are ignored in edit mode.
                }
            }

            background: Rectangle {
                color: "#252535"
                border.color: "#4a9eff"
                border.width: 1.5
                radius: 10
            }

            contentItem: RowLayout {
                spacing: 6

                TextField {
                    id: predEditField
                    property string originalWord: ""
                    Layout.fillWidth: true
                    Layout.preferredHeight: 32
                    color: "#f0f0f0"
                    font.pixelSize: 15
                    font.weight: Font.Medium
                    selectionColor: "#4a9eff"
                    selectedTextColor: "#fff"
                    leftPadding: 10
                    rightPadding: 10
                    verticalAlignment: Text.AlignVCenter

                    background: Rectangle {
                        color: "#1a1a2a"
                        radius: 6
                        border.color: predEditField.activeFocus ? "#4a9eff" : "#444"
                        border.width: 1
                    }

                    onAccepted: {
                        if (text.trim() && keyboard) {
                            keyboard.editPrediction(originalWord, text.trim())
                        }
                        predEditPopup.close()
                    }

                    Keys.onEscapePressed: predEditPopup.close()
                }

                // Confirm button
                Rectangle {
                    width: 32
                    height: 32
                    radius: 6
                    color: confirmMa.containsMouse ? "#2a6a2a" : "#1e3e1e"
                    border.color: "#4a4"
                    border.width: 1

                    Text {
                        anchors.centerIn: parent
                        text: "\u2713"
                        font.pixelSize: 16
                        font.weight: Font.Bold
                        color: "#6f6"
                    }

                    MouseArea {
                        id: confirmMa
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (predEditField.text.trim() && keyboard) {
                                keyboard.editPrediction(predEditField.originalWord, predEditField.text.trim())
                            }
                            predEditPopup.close()
                        }
                    }
                }

                // Cancel button (dismiss without saving)
                Rectangle {
                    width: 32
                    height: 32
                    radius: 6
                    color: cancelMa.containsMouse ? "#6a2a2a" : "#3e1e1e"
                    border.color: "#a44"
                    border.width: 1

                    Text {
                        anchors.centerIn: parent
                        text: "\u2715"
                        font.pixelSize: 14
                        font.weight: Font.Bold
                        color: "#f88"
                    }

                    MouseArea {
                        id: cancelMa
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: predEditPopup.close()
                    }
                }
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

        // Center on screen when first shown
        onVisibleChanged: {
            if (visible) {
                settingsWindow.x = Screen.width / 2 - settingsWindow.width / 2
                settingsWindow.y = Screen.height / 2 - settingsWindow.height / 2
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
            autoSpaceAfterPunctuation: root.autoSpaceAfterPunctuation
            autoCapitalizeAfterPunctuation: root.autoCapitalizeAfterPunctuation
            autoSaveOnExit: root.autoSaveOnExit
            swipeEnabled: root.swipeEnabled
            debugMode: root.showDebugPanel
            autoCheckUpdates: root.autoCheckUpdates
            updateStatus: root.updateInstalling
                          ? "checking"
                          : (root.updateAvailable ? "available" : root._lastCheckStatus)
            appVersion: keyboard ? keyboard.appVersion : ""

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
                } else if (setting === "autoSpaceAfterPunctuation") {
                    root.autoSpaceAfterPunctuation = value
                    appSettings.savedAutoSpaceAfterPunctuation = value
                    if (keyboard) keyboard.setAutoSpaceAfterPunctuation(value)
                } else if (setting === "autoCapitalizeAfterPunctuation") {
                    root.autoCapitalizeAfterPunctuation = value
                    appSettings.savedAutoCapitalizeAfterPunctuation = value
                    if (keyboard) keyboard.setAutoCapitalizeAfterPunctuation(value)
                } else if (setting === "autoSaveOnExit") {
                    root.autoSaveOnExit = value
                    appSettings.savedAutoSaveOnExit = value
                    if (keyboard) keyboard.setAutoSaveOnExit(value)
                } else if (setting === "swipeEnabled") {
                    root.swipeEnabled = value
                } else if (setting === "debugMode") {
                    root.showDebugPanel = value
                    if (keyboard) keyboard.setDebugMode(value)
                } else if (setting === "autoCheckUpdates") {
                    root.autoCheckUpdates = value
                    appSettings.savedAutoCheckUpdates = value
                }
            }

            onCloseRequested: root.showSettings = false
            onShowHelpRequested: root.showHelp = true
            onShowVisualizationRequested: root.showVisualization = true
            onCheckForUpdatesNowRequested: {
                if (keyboard) {
                    root._lastCheckStatus = "checking"
                    keyboard.checkForUpdate()
                }
            }
        }
    }

    // ===== Help Popup Window =====
    Window {
        id: helpWindow
        title: "Alpha-OSK Help"
        visible: root.showHelp
        width: 400
        minimumWidth: 340
        height: 520
        minimumHeight: 300
        flags: Qt.Window | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool

        onVisibleChanged: {
            if (visible) {
                helpWindow.x = Screen.width / 2 - helpWindow.width / 2
                helpWindow.y = Screen.height / 2 - helpWindow.height / 2
            }
        }

        onClosing: root.showHelp = false

        color: "#1e1e1e"

        Comp.HelpPanel {
            anchors.fill: parent
            onCloseRequested: root.showHelp = false
        }
    }

    // ===== Model Visualization Window =====
    Window {
        id: vizWindow
        title: "Language Model"
        visible: root.showVisualization
        width: 720
        minimumWidth: 520
        height: 600
        minimumHeight: 400
        flags: Qt.Window | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool

        onVisibleChanged: {
            if (visible) {
                vizWindow.x = Screen.width / 2 - vizWindow.width / 2
                vizWindow.y = Screen.height / 2 - vizWindow.height / 2
                vizContent.refresh()
            }
        }

        onClosing: root.showVisualization = false

        color: "transparent"

        Comp.ModelVisualization {
            id: vizContent
            anchors.fill: parent
            onCloseRequested: root.showVisualization = false
        }
    }
}
