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
        property bool savedRightClickShift: true
        // Flash a small bubble above a key showing the character it just
        // typed (left- or right-click).  Mobile-keyboard "key preview".
        property bool savedKeyPreview: true
        property bool savedAutoCheckUpdates: true
        // Hold-to-repeat timing (Backspace, arrow keys, Delete, PgUp/PgDn).
        // Defaults match KeyButton.qml's hardcoded values.  Exposed in
        // Settings → Smart Typing → Input so motor-impaired users can tune the threshold
        // (slow clicks systematically tipped past the 500 ms default and
        // produced "double" Backspace keystrokes).
        property int savedRepeatDelay: 500
        property int savedRepeatInterval: 120
        // Prediction merge strategy.  "rank" (default) is the
        // historical rank-based fusion; "rrf" / "linear" / "loglinear"
        // are alternatives surfaced via Settings → Smart Typing → Suggestion Engine.
        // See docs/architecture/HYBRID_MERGING.md for the trade-offs.  Default
        // MUST stay "rank" — every existing user's pill ranking
        // depends on it.
        property string savedMergeStrategy: "rank"
        // Compatibility mode — switches prediction-click insertion
        // and autocorrect from suffix-only / Shift+Left-replace (which
        // race over remote-desktop pipelines and inside IDE editors
        // that intercept keystrokes — VS Code + Monaco forks,
        // JetBrains family) to BackSpace × N + type-full-word.  Off
        // by default for the manual override; the auto-detect flag
        // (default ON) enables it dynamically when the foreground
        // window matches a known remote-desktop client or IDE.
        // (Legacy keys `savedRemoteCompatMode` / `savedRemoteCompatAuto`
        // from earlier releases are migrated to these on first launch
        // — see `_migrate_legacy_compat_settings` in keyboard_app.py.)
        property bool savedCompatMode: false
        property bool savedCompatAutoDetect: true
        // Window WIDTH — restored on launch, saved (debounced) on resize.
        // 0 means "no saved value yet, use the binding-driven default"
        // — that path runs on a fresh install.
        //
        // Height is deliberately NOT persisted: it's bound to the
        // keyboard's content (`height: outerLayout.implicitHeight + 60`),
        // so the only user-controllable dimension is width.  An earlier
        // version saved both, which broke the height binding the moment
        // it was imperatively restored on launch — the keyboard then
        // either grew empty bands or clipped the bottom row depending
        // on how the saved height compared to the content's needs.
        property int savedWindowWidth: 0
        // Window POSITION — restored on launch, saved (debounced) on
        // drag. Sentinel -1000000 = "never positioned", which routes to
        // the centered/bottom default on a fresh install. Unlike height,
        // position is safe to persist imperatively: x/y are plain
        // properties, not bound to content.
        property int savedWindowX: -1000000
        property int savedWindowY: -1000000
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
                appSettings.savedWindowX = Math.round(root.x)
                appSettings.savedWindowY = Math.round(root.y)
            }
        }
    }
    // NB: the width-changed handler lives further down in the file
    // (near line 301).  It calls saveGeometryTimer.restart() when
    // _geometryRestored is true, so width persistence flows through
    // that single seam.  Height isn't saved at all (see the Settings
    // block above for why) and onHeightChanged only refreshes the
    // swipe layout — no save call.

    // Auto-update — bridge fills these in when checkForUpdate() finds
    // a signed newer release.  See src/updater.py for the security model.
    property bool autoCheckUpdates: true
    property bool updateAvailable: false
    property string updateVersion: ""
    property string updateNotes: ""
    property bool updateInstalling: false
    property string updateError: ""
    // Download progress for the in-flight installer fetch. -1 total means
    // the server omitted Content-Length, in which case the popup shows
    // an indeterminate spinner instead of a percentage.
    property int updateDownloadBytes: 0
    property int updateDownloadTotal: 0
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
        // Restore saved window WIDTH first so the user gets the size
        // they had last time, not a flash of the default size followed
        // by a resize.  0 means "no value persisted yet".  Height is
        // intentionally NOT restored — it's bound to content height
        // and an imperative assignment here would break that binding,
        // which is exactly the bug that produced empty vertical bands
        // and bottom-row clipping in earlier versions.
        if (appSettings.savedWindowWidth > 0)
            root.width = Math.max(root.minimumWidth, appSettings.savedWindowWidth)
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
            keyboard.setCompatMode(appSettings.savedCompatMode)
            keyboard.setCompatAutoDetect(appSettings.savedCompatAutoDetect)
            keyboard.setMergeStrategy(appSettings.savedMergeStrategy)
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

        // Compute the panel-state-aware default width ONLY on a
        // fresh install (no persisted width). Otherwise the
        // savedWindowWidth restore above gets clobbered every
        // launch — the user's resized width never sticks.
        if (appSettings.savedWindowWidth <= 0) {
            var w = 940
            if (root.showNavigation) w += 220
            if (root.showNumpad) w += 250
            root.width = w
        }

        // Restore the saved position if we have one, else center
        // horizontally and anchor near the bottom of the screen.
        // Clamp back on-screen in case the display layout changed
        // (monitor unplugged, resolution drop) since the last run.
        if (appSettings.savedWindowX > -1000000
                && appSettings.savedWindowY > -1000000) {
            root.x = Math.max(0, Math.min(appSettings.savedWindowX,
                                          Screen.width - root.width))
            root.y = Math.max(0, Math.min(appSettings.savedWindowY,
                                          Screen.height - root.height))
        } else {
            root.x = (Screen.width - root.width) / 2
            root.y = Screen.height - root.height - 40
        }
        root._loaded = true

        // Surface the post-update toast if the auto-update relauncher
        // dropped a fresh handoff breadcrumb before we launched. The
        // bridge consumes the file (single-use) and returns the
        // version pair; an empty result means no pending update.
        if (keyboard) {
            var handoff = keyboard.consumeUpdateHandoff()
            if (handoff && handoff.version) {
                updateAppliedToast.flash(handoff.version,
                                         handoff.previousVersion || "")
            }
        }
    }

    // When side panels toggle, grow/shrink from the right edge (left stays put).
    // Deltas sized to keep main keys ~same size at the default scale:
    // nav = 3.0*keyW + per-panel fixed (≈ 220), numpad = 4.0*keyW + per-panel fixed (≈ 250).
    onShowNavigationChanged: {
        if (_loaded) root.width += showNavigation ? 220 : -220
        appSettings.savedShowNavigation = showNavigation
    }
    onShowNumpadChanged: {
        if (_loaded) root.width += showNumpad ? 250 : -250
        appSettings.savedShowNumpad = showNumpad
    }

    // Clear suggestions when the window loses activation (user clicked away)
    onActiveChanged: {
        if (!active && keyboard) keyboard.clearPredictions()
    }

    // Refresh the swipe-recognizer layout whenever the window is resized —
    // key positions move with the layout.  (See the merged onWidthChanged
    // handler further down which also handles minimumWidth clamping.)
    // Height is not persisted (it's bound to content) so there's no
    // save call here.
    onHeightChanged: {
        swipeLayoutPushTimer.restart()
    }

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

    // Hold-to-repeat timing for Backspace, arrows, Delete, PgUp/PgDn.
    // ``repeatDelay`` is the threshold below which a press counts as a
    // single click; ``repeatInterval`` is the cadence once auto-repeat
    // is firing.  Exposed in Settings → Smart Typing → Input.
    property int repeatDelay: appSettings.savedRepeatDelay
    property int repeatInterval: appSettings.savedRepeatInterval

    // Compatibility mode — see savedCompatMode comment and
    // KeyboardBridge.setCompatMode for the full rationale.
    property bool compatMode: appSettings.savedCompatMode
    property bool compatAutoDetect: appSettings.savedCompatAutoDetect

    // Prediction merge strategy — see savedMergeStrategy.
    property string mergeStrategy: appSettings.savedMergeStrategy

    // Swipe / glide typing — when on, dragging across keys decodes a word.
    property bool swipeEnabled: appSettings.savedSwipeEnabled

    // Right-click on a char key types its shifted variant (e.g. "1" → "!",
    // "a" → "A") without flipping the sticky shift state.  Purely additive
    // — left-click behaviour is unchanged whether this is on or off.
    property bool rightClickShift: appSettings.savedRightClickShift

    // When on, every key press (left- or right-click) flashes a brief
    // preview bubble above the key showing the character that was typed.
    property bool keyPreviewEnabled: appSettings.savedKeyPreview

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

    // Briefly float a preview bubble above a key showing the character it
    // just typed.  Right-click sends the shifted variant (e.g. "," → "<",
    // "a" → "A") without flipping sticky shift, and that glyph isn't
    // always the one drawn on the key, so the bubble confirms what
    // actually reached the OS.  Mirrors the mobile-keyboard "key preview"
    // pattern: shown on press, hidden on release.  ``item`` is the
    // KeyButton; coordinates are mapped into the overlay the bubble is
    // parented to.
    function showKeyPreview(item, ch) {
        if (!item || !ch) return
        var pt = item.mapToItem(Overlay.overlay, item.width / 2, 0)
        keyPreviewBubble.previewText = ch
        keyPreviewBubble.x = pt.x - keyPreviewBubble.width / 2
        keyPreviewBubble.y = pt.y - keyPreviewBubble.height - 6
        keyPreviewBubble.show()
    }

    function hideKeyPreview() {
        keyPreviewBubble.hide()
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
    // Nav panel: 3 keys × 1.0 = 3.0 units;  Numpad: 4 keys × 1.0 = 4.0 units
    // (The 0.9× multiplier on nav/numpad keys was bumped to 1.0× so
    // labels like "PrtSc"/"PgDn" don't clip — keep this in sync with
    // the keyW bindings on the panels themselves below.)
    property real totalKeyUnits: 15.5
        + (showNavigation ? 3.0 : 0)
        + (showNumpad ? 4.0 : 0)

    // Fixed-pixel overhead: margins(8×2=16) + number-row gaps(15 keys → 14×keySpacing)
    // + per-panel: separator(1) + 2 inner grid gaps + 2×RowLayout spacing(6)
    property real layoutFixedPixels: 16 + 14 * keySpacing
        + (showNavigation ? 1 + 2 * keySpacing + 12 : 0)
        + (showNumpad ? 1 + 3 * keySpacing + 12 : 0)

    property real keyW: Math.max(30, (root.width - layoutFixedPixels) / totalKeyUnits)
    // keyH simply tracks keyW at the keycap aspect ratio.  This works
    // because the window's `height` is bound to `outerLayout.implicitHeight + 60`
    // — i.e. the window auto-sizes to whatever the content needs.  The
    // user only resizes width (the resize handles are SizeHorCursor),
    // and height follows.  No height-budget arithmetic needed.
    property real keyH: Math.max(34, keyW * 0.89)

    // Safety net: if the window width ever drops below minimumWidth (e.g. via
    // OS window-snap, DPI change, or panel toggle), clamp it back up.  Also
    // refreshes the swipe-recognizer's key-centre map since the keys move
    // when the window resizes.
    onWidthChanged: {
        if (width < minimumWidth) width = minimumWidth
        swipeLayoutPushTimer.restart()
        if (_geometryRestored) saveGeometryTimer.restart()
    }

    // Persist window position when the user drags it (title-bar drag
    // updates root.x/root.y). Debounced through the same timer as width
    // so a drag doesn't hammer the registry. Gated on _geometryRestored
    // so Qt's construction-time x/y churn isn't persisted before the
    // saved value is restored.
    onXChanged: { if (_geometryRestored) saveGeometryTimer.restart() }
    onYChanged: { if (_geometryRestored) saveGeometryTimer.restart() }

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
        "dark":       { name: "Dark",       background: "#1a1a1a", keyColor: "#3a3a3a", keyPressed: "#5a5a5a", textColor: "#e0e0e0", accent: "#4a9eff", border: "#505050" },
        "light":      { name: "Light",      background: "#e8e8e8", keyColor: "#ffffff", keyPressed: "#d0d0d0", textColor: "#1a1a1a", accent: "#0078d4", border: "#c0c0c0" },
        "blue":       { name: "Ocean",      background: "#1a2a3a", keyColor: "#2a4a6a", keyPressed: "#3a6a9a", textColor: "#e0e0e0", accent: "#4a9eff", border: "#505050" },
        "green":      { name: "Forest",     background: "#1a2a1a", keyColor: "#2a4a2a", keyPressed: "#3a6a3a", textColor: "#e0e0e0", accent: "#4aff4a", border: "#505050" },
        "purple":     { name: "Amethyst",   background: "#2a1a3a", keyColor: "#4a2a5a", keyPressed: "#6a3a7a", textColor: "#e0e0e0", accent: "#bb66ff", border: "#505050" },
        "vaporwave":  { name: "Vaporwave",  background: "#1a0a2e", keyColor: "#2d1b4e", keyPressed: "#4a2d7a", textColor: "#ff71ce", accent: "#01cdfe", border: "#b967ff" },
        "blackboard": { name: "Blackboard", background: "#2c3e2c", keyColor: "#3d5a3d", keyPressed: "#4e6e4e", textColor: "#e8e8d0", accent: "#ffffaa", border: "#4a6a4a" },
        "typewriter": { name: "Typewriter", background: "#f5f0e8", keyColor: "#d4c9b0", keyPressed: "#c0b090", textColor: "#2c2416", accent: "#8b4513", border: "#a08060" },
        "spaceship":  { name: "Spaceship",  background: "#040d04", keyColor: "#0a1f0a", keyPressed: "#153015", textColor: "#00e676", accent: "#00ff9f", border: "#0d3b0d" }
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
            root.updateDownloadBytes = 0
            root.updateDownloadTotal = 0
        }
        function onUpdateDownloadProgress(bytes, total) {
            root.updateDownloadBytes = bytes
            root.updateDownloadTotal = total
        }
        function onUpdateInstallHandoffPending(version) {
            // Fired right before the installer's taskkill arrives. The
            // toast briefly tells the user the keyboard is about to
            // disappear and will come back on its own — without it the
            // ~30 s gap between keyboard-vanishes and relauncher-brings-
            // it-back reads as "the update broke the keyboard."
            updateStartingToast.flash(version)
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
                anchors.rightMargin: 264  // Leave space for buttons (privacy "Learning"/"Paused" text + Snippets icon)
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

                    ToolTip.visible: updateBtnArea.containsMouse
                    ToolTip.text: qsTr("Update available")
                    ToolTip.delay: 400

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
                                text: {
                                    if (root.updateError !== "")
                                        return root.updateError
                                    if (root.updateInstalling) {
                                        if (root.updateDownloadTotal > 0) {
                                            var mb = (root.updateDownloadBytes / 1048576).toFixed(1)
                                            var totalMb = (root.updateDownloadTotal / 1048576).toFixed(1)
                                            var pct = Math.floor(
                                                100 * root.updateDownloadBytes / root.updateDownloadTotal
                                            )
                                            return qsTr("Downloading %1 / %2 MB (%3%)").arg(mb).arg(totalMb).arg(pct)
                                        }
                                        if (root.updateDownloadBytes > 0) {
                                            var mb2 = (root.updateDownloadBytes / 1048576).toFixed(1)
                                            return qsTr("Downloading %1 MB…").arg(mb2)
                                        }
                                        return qsTr("Starting download…")
                                    }
                                    return qsTr("Installing will close and relaunch the app.")
                                }
                                color: Qt.darker(root.themeTextColor, 1.4)
                                font.pixelSize: 11
                                wrapMode: Text.WordWrap
                            }

                            // Download progress bar — only painted while
                            // an install is in flight. When the server
                            // gave us a Content-Length we show real %;
                            // otherwise we fall back to indeterminate
                            // motion (from: 0; to: 0) so the user sees
                            // the work is still happening.
                            ProgressBar {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 6
                                visible: root.updateInstalling && root.updateError === ""
                                from: 0
                                to: root.updateDownloadTotal > 0 ? root.updateDownloadTotal : 0
                                value: root.updateDownloadBytes
                                indeterminate: root.updateDownloadTotal <= 0
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

                // Privacy mode toggle (learning on/off).  Used to be
                // a play/pause icon, but a media-player metaphor
                // doesn't read as "is the keyboard learning from me"
                // — users misread it as "is something playing".
                // Now a fixed-width text label that just shows the
                // current state.  The hover tooltip says what
                // clicking will do, so the label-vs-action ambiguity
                // is resolved before the click.
                Rectangle {
                    // Width sized for the longer label "Learning"
                    // (8 chars at 11 px DemiBold) so toggling between
                    // "Learning" and "Pause" doesn't reflow the title
                    // bar's button row.  If you change the labels to
                    // longer words, bump both this width and the
                    // dragArea.rightMargin further up.
                    width: 62
                    height: 24
                    radius: 4
                    color: root.privacyMode ? "#4a2a2a" : privacyBtn.containsMouse ? "#444" : "transparent"
                    border.color: root.privacyMode ? "#ff6b6b" : "transparent"
                    border.width: root.privacyMode ? 1 : 0

                    ToolTip.visible: privacyBtn.containsMouse
                    ToolTip.text: root.privacyMode
                                  ? qsTr("Resume learning from typing")
                                  : qsTr("Pause learning from typing")
                    ToolTip.delay: 400

                    Text {
                        anchors.centerIn: parent
                        text: root.privacyMode ? qsTr("Paused") : qsTr("Learning")
                        color: root.privacyMode ? "#ff6b6b" : "#bbb"
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
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

                // Snippets button: opens the quick-insert popup of
                // saved personal info / phrases the user taps to type
                // in one click. Sits next to Learning for discoverability.
                Rectangle {
                    width: 28
                    height: 24
                    radius: 4
                    color: snippetsBtn.containsMouse ? "#444" : "transparent"

                    ToolTip.visible: snippetsBtn.containsMouse
                    ToolTip.text: qsTr("Snippets: saved text you tap to type")
                    ToolTip.delay: 400

                    Text {
                        anchors.centerIn: parent
                        text: "☰"
                        font.pixelSize: 15
                        color: snippetsWindow.visible ? root.themeAccent : "#999"
                    }

                    MouseArea {
                        id: snippetsBtn
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (snippetsWindow.visible) snippetsWindow.hide()
                            else snippetsWindow.openList()
                        }
                    }
                }

                // (Clear-context button moved into the prediction bar below —
                // it's a bigger, easier target parked at the right end of the
                // suggestion pills. See `clearCtxPill` in predBar.)

                // Settings button (gear icon) - opens unified settings
                Rectangle {
                    width: 28
                    height: 24
                    radius: 4
                    color: settingsBtn.containsMouse ? "#444" : "transparent"

                    ToolTip.visible: settingsBtn.containsMouse
                    ToolTip.text: qsTr("Settings")
                    ToolTip.delay: 400

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

                    ToolTip.visible: minBtn.containsMouse
                    ToolTip.text: qsTr("Minimize")
                    ToolTip.delay: 400

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

                    ToolTip.visible: closeBtn.containsMouse
                    ToolTip.text: qsTr("Close")
                    ToolTip.delay: 400

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
                id: predBar
                Layout.fillWidth: true
                // Pill geometry tracks the main keyboard so the bar stays
                // proportional when the user resizes the window. At default
                // keyH ≈ 50 / keyW ≈ 56 these collapse to the historical
                // 36 px pill / 15 px font / 28 px horizontal padding /
                // 60 px floor — so legacy sizing is preserved at the
                // default geometry and only departs from it once the
                // user actually resizes.
                property real predPillHeight: Math.max(34, root.keyH * 0.86)
                property real predFontSize: Math.max(14, root.keyH * 0.36)
                property real predHorizontalPad: Math.max(24, root.keyW * 0.58)
                property real predMinWidth: Math.max(48, root.keyW * 1.25)
                // Right-edge zone reserved for the clear-context button so the
                // centered pill row never slides under it. Subtracted from both
                // sides of the pill row's available width to keep pills centered.
                property real clearCtxReserve: predPillHeight + 16
                Layout.preferredHeight: root.suggestionsEnabled ? predPillHeight + 4 : 0
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
                            property real naturalWidth: Math.max(predBar.predMinWidth, predText.implicitWidth + predBar.predHorizontalPad)
                            property real maxPillWidth: {
                                var count = root.predictions.length
                                if (count <= 0) return naturalWidth
                                var avail = root.width - 32 - predBar.clearCtxReserve * 2 - (count - 1) * predRow.spacing
                                return Math.max(predBar.predPillHeight * 1.4, avail / count)
                            }
                            width: Math.min(naturalWidth, maxPillWidth)
                            height: predBar.predPillHeight
                            radius: Math.max(4, predBar.predPillHeight * 0.22)
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
                                anchors.leftMargin: Math.max(6, predBar.predHorizontalPad * 0.28)
                                anchors.rightMargin: Math.max(6, predBar.predHorizontalPad * 0.28)
                                horizontalAlignment: Text.AlignHCenter
                                text: modelData
                                color: predMouse.containsMouse ? Qt.lighter(root.themeTextColor, 1.3) : root.themeTextColor
                                font.pixelSize: predBar.predFontSize
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

                // Clear-context button parked at the right end of the
                // suggestion bar: a big, easy target that wipes the prediction
                // context buffers (current word, sentence buffer, sliding
                // 200-char context) so the next pill is computed from scratch.
                // App-switch clears context automatically, but the
                // foreground-window poll misses things like browser tab changes
                // or a focus change to a child window with the same hwnd, so
                // this is the manual escape hatch. Hidden when suggestions are
                // off (the bar collapses to zero height anyway).
                Rectangle {
                    id: clearCtxPill
                    visible: root.suggestionsEnabled
                    anchors.right: parent.right
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    width: predBar.predPillHeight
                    height: predBar.predPillHeight
                    radius: width / 2
                    color: clearCtxBtn.containsMouse ? Qt.lighter(root.themeKeyColor, 1.3)
                                                     : Qt.rgba(0, 0, 0, 0.18)
                    border.color: clearCtxBtn.containsMouse ? root.themeAccent
                                                            : Qt.rgba(1, 1, 1, 0.18)
                    border.width: 1

                    ToolTip.visible: clearCtxBtn.containsMouse
                    ToolTip.text: qsTr("Clear suggestion context")
                    ToolTip.delay: 400

                    Text {
                        anchors.centerIn: parent
                        text: "⟲"
                        font.pixelSize: predBar.predFontSize * 1.35
                        color: clearCtxBtn.containsMouse ? root.themeTextColor : "#bbb"
                    }

                    MouseArea {
                        id: clearCtxBtn
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (keyboard) keyboard.resetContext()
                            contextClearedToast.flash()
                        }
                    }

                    Behavior on color { ColorAnimation { duration: 100 } }
                    Behavior on border.color { ColorAnimation { duration: 100 } }
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
                        keyW: root.keyW
                        keyH: root.keyH * 0.7
                        keySpacing: root.keySpacing
                        keyColor: Qt.darker(root.themeKeyColor, 1.15)
                        keyPressedColor: root.themeKeyPressed
                        keyTextColor: root.themeTextColor
                        accentColor: root.themeAccent
                        borderColor: root.themeBorder
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
                                repeatDelay: root.repeatDelay
                                repeatInterval: root.repeatInterval

                                onKeyPressed: {
                                    if (kd.type === "char") {
                                        var ch = root.shiftOn && kd.shifted ? kd.shifted : kd.key
                                        keyboard.pressKey(ch)
                                        // displayText already reflects shift/
                                        // caps casing, so it matches the char
                                        // pressKey actually sends to the OS.
                                        if (root.keyPreviewEnabled)
                                            root.showKeyPreview(keyBtn, keyBtn.displayText)
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

                                // Right-click on a char key → shifted glyph
                                // (e.g. "!" on "1") or uppercase letter,
                                // without touching the sticky shift state.
                                // Uses pressKeyLiteral because pressKey
                                // applies shift/caps case normalization
                                // and would lowercase the "A" we just
                                // chose back to "a".  Modifier / special
                                // keys are deliberate no-ops — right-
                                // clicking Shift or Enter has no obvious
                                // meaning.
                                onKeyRightPressed: {
                                    if (!root.rightClickShift) return
                                    if (kd.type !== "char") return
                                    var rch = ""
                                    if (kd.shifted) {
                                        rch = kd.shifted
                                    } else if (kd.key && kd.key.length === 1 && /[a-z]/i.test(kd.key)) {
                                        rch = kd.key.toUpperCase()
                                        if (rch === kd.key) return  // already uppercase, nothing to do
                                    } else {
                                        return
                                    }
                                    keyboard.pressKeyLiteral(rch)
                                    if (root.keyPreviewEnabled)
                                        root.showKeyPreview(keyBtn, rch)
                                }

                                // Dismiss the preview on release — true
                                // phone behaviour: bubble lives only while
                                // the key is held (with a min-visible floor
                                // so fast clicks still register).
                                onKeyReleased: root.hideKeyPreview()
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
                keyW: root.keyW
                keyH: root.keyH
                keySpacing: root.keySpacing
                keyColor: Qt.darker(root.themeKeyColor, 1.15)
                keyPressedColor: root.themeKeyPressed
                keyTextColor: root.themeTextColor
                accentColor: root.themeAccent
                borderColor: root.themeBorder
                repeatDelay: root.repeatDelay
                repeatInterval: root.repeatInterval
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
                keyW: root.keyW
                keyH: root.keyH
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

                // Upweight
                Rectangle {
                    width: parent.width - 8
                    height: 34
                    x: 4
                    radius: 6
                    color: goodMa.containsMouse ? "#2a4a3a" : "transparent"

                    Row {
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                        anchors.leftMargin: 12
                        spacing: 10
                        Text { text: "\u25B2"; font.pixelSize: 11; color: "#7d7"; anchors.verticalCenter: parent.verticalCenter }
                        Text { text: "Show more"; font.pixelSize: 13; color: "#ddd"; anchors.verticalCenter: parent.verticalCenter }
                    }

                    MouseArea {
                        id: goodMa
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (keyboard) keyboard.markGoodSuggestion(predContextMenu.targetWord)
                            predContextMenu.close()
                        }
                    }
                }

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
                        if (predEditField.text.trim() && keyboard) {
                            keyboard.editPrediction(predEditField.originalWord, predEditField.text.trim())
                            editSavedToast.flash()
                        }
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
                            editSavedToast.flash()
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
                                editSavedToast.flash()
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

        // Snippets popup: a tap-to-insert list of the user's saved
        // quick text (name, email, phone, address, canned phrases).
        //
        // This is a SEPARATE top-level Window, not a Popup. A Popup is
        // clipped to its parent window's overlay, so it can't be dragged
        // outside the keyboard. A standalone Window can float anywhere on
        // the desktop. It carries the same OSK window flags as the main
        // window (frameless, stays-on-top, does-not-accept-focus) so it
        // never steals focus from the app the user is typing into; the
        // Python side applies WS_EX_NOACTIVATE to it too (see
        // _apply_window_flags / the snippetsWindowReady signal).
        //
        // Two views share the window (an editingIndex switch: -1 list,
        // >= 0 editor). Edit mode is only turned on while the editor is
        // showing, so tapping a snippet in the list still synthesises to
        // the OS via the bridge's insertSnippet slot. The header is a
        // drag handle.
        Window {
            id: snippetsWindow
            // objectName lets the Python side find this window to apply
            // WS_EX_NOACTIVATE (so clicking it never steals focus).
            objectName: "snippetsWindow"
            width: 360
            height: Math.max(160, snipContent.implicitHeight + 24)
            minimumWidth: 360
            minimumHeight: 160
            color: "transparent"
            title: "Alpha-OSK Snippets"
            flags: Qt.Window | Qt.FramelessWindowHint
                   | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus

            // -1 = list view; >= 0 = editing that snippet index.
            property int editingIndex: -1
            // Which editor field OSK keys flow to while editing.
            property string editTarget: "value"
            property var snippetList: []

            function refresh() {
                snippetList = keyboard ? keyboard.getSnippets() : []
            }

            function activeField() {
                return editTarget === "label" ? snipLabelField : snipValueField
            }

            function openList() {
                editingIndex = -1
                if (keyboard) keyboard.setEditMode(false)
                refresh()
                // Center over the keyboard the first time; afterwards the
                // user's dragged position is kept (x/y persist while the
                // window object lives).
                if (!_positioned) {
                    snippetsWindow.x = root.x + (root.width - snippetsWindow.width) / 2
                    snippetsWindow.y = Math.max(0, root.y - snippetsWindow.height - 8)
                    _positioned = true
                }
                snippetsWindow.show()
                snippetsWindow.raise()
            }
            property bool _positioned: false

            function beginEdit(idx) {
                refresh()
                var s = snippetList[idx]
                snipLabelField.text = s ? s.label : ""
                snipValueField.text = s ? s.value : ""
                editTarget = "value"
                editingIndex = idx
                if (keyboard) keyboard.setEditMode(true)
                snipValueField.forceActiveFocus()
            }

            function endEdit() {
                if (keyboard) keyboard.setEditMode(false)
                editingIndex = -1
            }

            function saveEdit() {
                if (editingIndex >= 0 && keyboard) {
                    keyboard.setSnippet(editingIndex, snipLabelField.text.trim(), snipValueField.text)
                    editSavedToast.flash()
                }
                endEdit()
            }

            onVisibleChanged: {
                if (!visible && keyboard) keyboard.setEditMode(false)
            }

            // While the editor is open, OSK key presses are short-
            // circuited in the bridge and routed here instead of being
            // synthesised to the OS. Apply them to whichever editor
            // field is active (label or value).
            Connections {
                target: keyboard
                enabled: snippetsWindow.visible

                function onSnippetsChanged(list) {
                    snippetsWindow.snippetList = list
                }

                function onEditKeyTyped(ch) {
                    if (snippetsWindow.editingIndex < 0) return
                    var f = snippetsWindow.activeField()
                    if (f.selectedText)
                        f.remove(f.selectionStart, f.selectionEnd)
                    f.insert(f.cursorPosition, ch)
                }

                function onEditSpecialPressed(name) {
                    if (snippetsWindow.editingIndex < 0) return
                    var f = snippetsWindow.activeField()
                    var pos = f.cursorPosition
                    var len = f.length
                    if (name === "backspace") {
                        if (f.selectedText) f.remove(f.selectionStart, f.selectionEnd)
                        else if (pos > 0) f.remove(pos - 1, pos)
                    } else if (name === "delete") {
                        if (f.selectedText) f.remove(f.selectionStart, f.selectionEnd)
                        else if (pos < len) f.remove(pos, pos + 1)
                    } else if (name === "left") {
                        f.cursorPosition = Math.max(0, pos - 1)
                    } else if (name === "right") {
                        f.cursorPosition = Math.min(len, pos + 1)
                    } else if (name === "home") {
                        f.cursorPosition = 0
                    } else if (name === "end") {
                        f.cursorPosition = len
                    } else if (name === "space") {
                        if (f.selectedText) f.remove(f.selectionStart, f.selectionEnd)
                        f.insert(f.cursorPosition, " ")
                    } else if (name === "return" || name === "enter") {
                        snippetsWindow.saveEdit()
                    } else if (name === "escape") {
                        snippetsWindow.endEdit()
                    }
                }
            }

            // Window background (rounded card).
            Rectangle {
                anchors.fill: parent
                color: root.themeBackground
                border.color: root.themeAccent
                border.width: 1
                radius: 8
            }

            ColumnLayout {
                id: snipContent
                anchors.fill: parent
                anchors.margins: 12
                spacing: 8

                // Header — drag handle for the whole window.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6

                    Item {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 28

                        Row {
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 6
                            Row {
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 3
                                Repeater {
                                    model: 4
                                    Rectangle { width: 3; height: 3; radius: 1.5; color: "#666" }
                                }
                            }
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                text: snippetsWindow.editingIndex >= 0 ? qsTr("Edit snippet") : qsTr("Snippets")
                                color: root.themeTextColor
                                font.pixelSize: 14
                                font.weight: Font.DemiBold
                            }
                        }

                        MouseArea {
                            id: snipDragArea
                            anchors.fill: parent
                            cursorShape: Qt.SizeAllCursor
                            property real startMx
                            property real startMy
                            property real startX
                            property real startY
                            onPressed: function(mouse) {
                                var g = mapToGlobal(mouse.x, mouse.y)
                                startMx = g.x; startMy = g.y
                                startX = snippetsWindow.x; startY = snippetsWindow.y
                            }
                            onPositionChanged: function(mouse) {
                                if (!pressed) return
                                // Free movement anywhere on the desktop —
                                // this is a real top-level window, so no
                                // overlay clamp is needed.
                                var g = mapToGlobal(mouse.x, mouse.y)
                                snippetsWindow.x = startX + (g.x - startMx)
                                snippetsWindow.y = startY + (g.y - startMy)
                            }
                        }
                    }

                    Rectangle {
                        width: 28; height: 28; radius: 4
                        color: snipCloseMa.containsMouse ? "#c33" : "transparent"
                        Text {
                            anchors.centerIn: parent; text: "✕"
                            font.pixelSize: 13
                            color: snipCloseMa.containsMouse ? "#fff" : "#999"
                        }
                        MouseArea {
                            id: snipCloseMa; anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: snippetsWindow.hide()
                        }
                    }
                }

                // ---- List view ----
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    visible: snippetsWindow.editingIndex < 0

                    Repeater {
                        model: snippetsWindow.snippetList
                        delegate: RowLayout {
                            Layout.fillWidth: true
                            spacing: 4

                            // Primary action: insert if it has a value,
                            // otherwise open the editor (so an empty slot
                            // is never a dead tap).
                            Rectangle {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 44
                                radius: 6
                                color: insMa.containsMouse
                                       ? Qt.lighter(root.themeKeyColor, 1.2) : root.themeKeyColor
                                border.color: "#444"; border.width: 1

                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 10
                                    anchors.rightMargin: 10
                                    spacing: 0
                                    Text {
                                        Layout.fillWidth: true
                                        text: (modelData.label && modelData.label.length)
                                              ? modelData.label : qsTr("(unnamed)")
                                        color: root.themeTextColor
                                        font.pixelSize: 13
                                        font.weight: Font.DemiBold
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: (modelData.value && modelData.value.length)
                                              ? modelData.value : qsTr("empty, tap to fill in")
                                        color: "#999"
                                        font.pixelSize: 11
                                        elide: Text.ElideRight
                                    }
                                }
                                MouseArea {
                                    id: insMa
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        if (modelData.value && modelData.value.length > 0) {
                                            if (keyboard) keyboard.insertSnippet(index)
                                            snippetsWindow.hide()
                                        } else {
                                            snippetsWindow.beginEdit(index)
                                        }
                                    }
                                }
                            }

                            // Edit
                            Rectangle {
                                width: 38; height: 44; radius: 6
                                color: edMa.containsMouse ? "#2a4a6a" : "#1e3450"
                                border.color: "#46a"; border.width: 1
                                Text { anchors.centerIn: parent; text: "✎"; font.pixelSize: 16; color: "#9cf" }
                                MouseArea {
                                    id: edMa; anchors.fill: parent; hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: snippetsWindow.beginEdit(index)
                                }
                            }

                            // Delete
                            Rectangle {
                                width: 38; height: 44; radius: 6
                                color: delMa.containsMouse ? "#6a2a2a" : "#3e1e1e"
                                border.color: "#a44"; border.width: 1
                                Text { anchors.centerIn: parent; text: "✕"; font.pixelSize: 15; color: "#f88" }
                                MouseArea {
                                    id: delMa; anchors.fill: parent; hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: if (keyboard) keyboard.deleteSnippet(index)
                                }
                            }
                        }
                    }

                    // Add button
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 38
                        radius: 6
                        color: addMa.containsMouse ? "#2a5a2a" : "#1e3e1e"
                        border.color: "#4a4"; border.width: 1
                        Text {
                            anchors.centerIn: parent; text: qsTr("+ Add snippet")
                            color: "#8d8"; font.pixelSize: 13; font.weight: Font.DemiBold
                        }
                        MouseArea {
                            id: addMa; anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (keyboard) {
                                    keyboard.addSnippet()
                                    snippetsWindow.refresh()
                                    snippetsWindow.beginEdit(snippetsWindow.snippetList.length - 1)
                                }
                            }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: qsTr("Tap a snippet to type it. Pencil edits, trash removes.")
                        color: "#777"; font.pixelSize: 10
                        wrapMode: Text.WordWrap
                    }
                }

                // ---- Edit view ----
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    visible: snippetsWindow.editingIndex >= 0

                    Text { text: qsTr("Label (shown on the button)"); color: "#aaa"; font.pixelSize: 11 }
                    TextField {
                        id: snipLabelField
                        Layout.fillWidth: true
                        Layout.preferredHeight: 36
                        color: "#f0f0f0"; font.pixelSize: 14
                        selectionColor: root.themeAccent; selectedTextColor: "#fff"
                        leftPadding: 10; rightPadding: 10
                        background: Rectangle {
                            color: "#1a1a2a"; radius: 6
                            border.color: snippetsWindow.editTarget === "label" ? root.themeAccent : "#444"
                            border.width: snippetsWindow.editTarget === "label" ? 2 : 1
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.IBeamCursor
                            onClicked: { snippetsWindow.editTarget = "label"; snipLabelField.forceActiveFocus() }
                        }
                    }

                    Text { text: qsTr("Text to type"); color: "#aaa"; font.pixelSize: 11 }
                    TextField {
                        id: snipValueField
                        Layout.fillWidth: true
                        Layout.preferredHeight: 36
                        color: "#f0f0f0"; font.pixelSize: 14
                        selectionColor: root.themeAccent; selectedTextColor: "#fff"
                        leftPadding: 10; rightPadding: 10
                        background: Rectangle {
                            color: "#1a1a2a"; radius: 6
                            border.color: snippetsWindow.editTarget === "value" ? root.themeAccent : "#444"
                            border.width: snippetsWindow.editTarget === "value" ? 2 : 1
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.IBeamCursor
                            onClicked: { snippetsWindow.editTarget = "value"; snipValueField.forceActiveFocus() }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: qsTr("Type with the keyboard below. The highlighted box is where text goes. Tap the other box to switch.")
                        color: "#777"; font.pixelSize: 10
                        wrapMode: Text.WordWrap
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        Item { Layout.fillWidth: true }
                        Rectangle {
                            width: 84; height: 36; radius: 6
                            color: snipCancelMa.containsMouse ? "#6a2a2a" : "#3e1e1e"
                            border.color: "#a44"; border.width: 1
                            Text { anchors.centerIn: parent; text: qsTr("Cancel"); color: "#f88"; font.pixelSize: 13 }
                            MouseArea {
                                id: snipCancelMa; anchors.fill: parent; hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: snippetsWindow.endEdit()
                            }
                        }
                        Rectangle {
                            width: 84; height: 36; radius: 6
                            color: snipSaveMa.containsMouse ? "#2a6a2a" : "#1e3e1e"
                            border.color: "#4a4"; border.width: 1
                            Text {
                                anchors.centerIn: parent; text: qsTr("Save")
                                color: "#6f6"; font.pixelSize: 13; font.weight: Font.Bold
                            }
                            MouseArea {
                                id: snipSaveMa; anchors.fill: parent; hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: snippetsWindow.saveEdit()
                            }
                        }
                    }
                }
            }
        }

        // Post-update toast — shown once on the first launch after the
        // auto-updater applied a new version. Confirms to the user
        // that the install completed (the previous flow gave no signal:
        // OSK closed, OSK reopened, no way to tell if it was a successful
        // update or a crash-and-restart). Payload comes from the
        // relauncher's update_handoff.json breadcrumb via the bridge's
        // consumeUpdateHandoff slot.
        Popup {
            id: updateAppliedToast
            parent: Overlay.overlay
            x: (root.width - width) / 2
            y: 36
            width: 220
            height: 36
            modal: false
            dim: false
            closePolicy: Popup.NoAutoClose

            property string newVersion: ""
            property string previousVersion: ""

            background: Rectangle {
                color: "#1e3354"
                border.color: "#4a8eff"
                border.width: 1
                radius: 8
            }

            contentItem: Row {
                spacing: 8
                anchors.verticalCenter: parent.verticalCenter
                Text {
                    text: "✓"
                    color: "#7ec8ff"
                    font.pixelSize: 15
                    font.weight: Font.Bold
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    text: updateAppliedToast.previousVersion !== ""
                          ? qsTr("Updated to v%1 from v%2").arg(updateAppliedToast.newVersion).arg(updateAppliedToast.previousVersion)
                          : qsTr("Updated to v%1").arg(updateAppliedToast.newVersion)
                    color: "#cfe0ff"
                    font.pixelSize: 13
                    anchors.verticalCenter: parent.verticalCenter
                }
            }

            Timer {
                id: updateAppliedToastTimer
                interval: 4000
                onTriggered: updateAppliedToast.close()
            }

            function flash(version, prevVersion) {
                updateAppliedToast.newVersion = version
                updateAppliedToast.previousVersion = prevVersion
                open()
                updateAppliedToastTimer.restart()
            }
        }

        // Pre-update toast — flashed by KeyboardBridge.updateInstallHandoffPending
        // immediately before the installer is launched, so the user
        // knows why the keyboard is about to disappear and that it
        // will come back on its own. The toast is wider than the
        // post-update one because the message has more to say.
        Popup {
            id: updateStartingToast
            parent: Overlay.overlay
            x: (root.width - width) / 2
            y: 36
            width: 360
            height: 56
            modal: false
            dim: false
            closePolicy: Popup.NoAutoClose

            property string newVersion: ""

            background: Rectangle {
                color: "#1e3354"
                border.color: "#4a8eff"
                border.width: 1
                radius: 8
            }

            contentItem: Column {
                spacing: 2
                anchors.verticalCenter: parent.verticalCenter
                Text {
                    text: qsTr("Installing v%1…").arg(updateStartingToast.newVersion)
                    color: "#7ec8ff"
                    font.pixelSize: 14
                    font.weight: Font.Bold
                    anchors.horizontalCenter: parent.horizontalCenter
                }
                Text {
                    text: qsTr("The keyboard will disappear briefly and come back.")
                    color: "#cfe0ff"
                    font.pixelSize: 12
                    anchors.horizontalCenter: parent.horizontalCenter
                }
            }

            // No close timer: the installer's taskkill will close us
            // along with the rest of the process within ~1-2 s. Setting
            // a timer would risk the toast vanishing before the
            // keyboard does, leaving the user with the silence we're
            // trying to avoid.

            function flash(version) {
                updateStartingToast.newVersion = version
                open()
            }
        }

        // "Saved" confirmation toast — appears briefly after a successful
        // prediction edit so the user knows the change persisted. Auto-
        // dismisses after ~1.4 s.
        Popup {
            id: editSavedToast
            parent: Overlay.overlay
            x: (root.width - width) / 2
            y: 36
            width: 110
            height: 32
            modal: false
            dim: false
            closePolicy: Popup.NoAutoClose

            background: Rectangle {
                color: "#1e3e1e"
                border.color: "#4a4"
                border.width: 1
                radius: 8
            }

            contentItem: Row {
                spacing: 6
                Text {
                    text: "✓"
                    color: "#6f6"
                    font.pixelSize: 14
                    font.weight: Font.Bold
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    text: "Saved"
                    color: "#cfc"
                    font.pixelSize: 13
                    anchors.verticalCenter: parent.verticalCenter
                }
            }

            Timer {
                id: editSavedToastTimer
                interval: 1400
                onTriggered: editSavedToast.close()
            }

            function flash() {
                open()
                editSavedToastTimer.restart()
            }
        }

        // "Context cleared" confirmation toast, fired from the title-bar
        // ⟲ button.  Mirrors editSavedToast's pattern: non-modal, centered
        // near the top, auto-closes after a short dwell.
        Popup {
            id: contextClearedToast
            parent: Overlay.overlay
            x: (root.width - width) / 2
            y: 36
            width: 150
            height: 32
            modal: false
            dim: false
            closePolicy: Popup.NoAutoClose

            background: Rectangle {
                color: "#1e2e3e"
                border.color: "#4a8"
                border.width: 1
                radius: 8
            }

            contentItem: Row {
                spacing: 6
                Text {
                    text: "⟲"
                    color: "#6cf"
                    font.pixelSize: 14
                    font.weight: Font.Bold
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    text: "Context cleared"
                    color: "#cef"
                    font.pixelSize: 13
                    anchors.verticalCenter: parent.verticalCenter
                }
            }

            Timer {
                id: contextClearedToastTimer
                interval: 1400
                onTriggered: contextClearedToast.close()
            }

            function flash() {
                open()
                contextClearedToastTimer.restart()
            }
        }

        // Key-press preview bubble — flashed just above a key to confirm
        // the character that was actually typed (the shifted variant on
        // right-click isn't always the glyph drawn on the key).  Shown on
        // press, hidden on release, like a phone.  Positioned by
        // root.showKeyPreview(); fixed width so the first show centers
        // correctly before the content is measured.
        Popup {
            id: keyPreviewBubble
            parent: Overlay.overlay
            property string previewText: ""
            // True once the key is released while the min-visible floor is
            // still running — close() then fires when that timer elapses.
            property bool pendingHide: false
            width: 40
            height: 40
            modal: false
            dim: false
            closePolicy: Popup.NoAutoClose

            background: Rectangle {
                color: root.themeAccent
                border.color: Qt.lighter(root.themeAccent, 1.4)
                border.width: 1
                radius: 8
            }

            contentItem: Text {
                text: keyPreviewBubble.previewText
                // Match KeyButton's contrast rule: dark text on bright
                // accents, white on dark.
                color: {
                    var bg = root.themeAccent
                    var lum = bg.r * 0.299 + bg.g * 0.587 + bg.b * 0.114
                    return lum > 0.5 ? "#111111" : "#ffffff"
                }
                font.pixelSize: 20
                font.weight: Font.Bold
                font.family: "Segoe UI, Inter, Ubuntu, Noto Sans, sans-serif"
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }

            // Just like a phone: the bubble shows on press and hides on
            // release.  Two guards make that robust on a mouse-driven OSK:
            //   * minTimer — a short visibility floor so a lightning-fast
            //     click (press+release in a few ms) still flashes long
            //     enough to register, instead of opening and closing in
            //     the same frame.
            //   * safetyTimer — WS_EX_NOACTIVATE can swallow the mouse
            //     release when the cursor leaves the OSK, so force the
            //     bubble closed after a bound that comfortably exceeds any
            //     real tap, in case keyReleased never arrives.
            Timer {
                id: keyPreviewMinTimer
                interval: 110
                onTriggered: if (keyPreviewBubble.pendingHide) keyPreviewBubble.close()
            }
            Timer {
                id: keyPreviewSafetyTimer
                interval: 1500
                onTriggered: keyPreviewBubble.close()
            }

            function show() {
                pendingHide = false
                open()
                keyPreviewMinTimer.restart()
                keyPreviewSafetyTimer.restart()
            }

            function hide() {
                keyPreviewSafetyTimer.stop()
                // Honour the visibility floor: if the press was shorter
                // than minTimer, defer the close until the floor elapses.
                if (keyPreviewMinTimer.running) {
                    pendingHide = true
                } else {
                    close()
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

        // Center on screen when first shown.  Also reset the
        // drill-down panel to its home view -- otherwise re-opening
        // settings would land on whatever sub-page the user was
        // viewing last time, which reads as "the menu changed".
        onVisibleChanged: {
            if (visible) {
                settingsWindow.x = Screen.width / 2 - settingsWindow.width / 2
                settingsWindow.y = Screen.height / 2 - settingsWindow.height / 2
                if (settingsPanel) settingsPanel.resetToHome()
            }
        }

        // Sync close button with main showSettings flag
        onClosing: root.showSettings = false

        color: "#1e1e1e"

        Comp.UnifiedSettingsPanel {
            id: settingsPanel
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
            rightClickShift: root.rightClickShift
            keyPreviewEnabled: root.keyPreviewEnabled
            repeatDelay: root.repeatDelay
            repeatInterval: root.repeatInterval
            compatMode: root.compatMode
            compatAutoDetect: root.compatAutoDetect
            mergeStrategy: root.mergeStrategy
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
                } else if (setting === "rightClickShift") {
                    root.rightClickShift = value
                    appSettings.savedRightClickShift = value
                } else if (setting === "keyPreview") {
                    root.keyPreviewEnabled = value
                    appSettings.savedKeyPreview = value
                } else if (setting === "repeatDelay") {
                    root.repeatDelay = value
                    appSettings.savedRepeatDelay = value
                } else if (setting === "repeatInterval") {
                    root.repeatInterval = value
                    appSettings.savedRepeatInterval = value
                } else if (setting === "compatMode") {
                    root.compatMode = value
                    appSettings.savedCompatMode = value
                    if (keyboard) keyboard.setCompatMode(value)
                } else if (setting === "compatAutoDetect") {
                    root.compatAutoDetect = value
                    appSettings.savedCompatAutoDetect = value
                    if (keyboard) keyboard.setCompatAutoDetect(value)
                } else if (setting === "mergeStrategy") {
                    root.mergeStrategy = value
                    appSettings.savedMergeStrategy = value
                    if (keyboard) keyboard.setMergeStrategy(value)
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
        title: "Your Language Model"
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
