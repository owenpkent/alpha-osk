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
    height: outerLayout.implicitHeight + 80  // Extra height for the (taller) title bar + bottom padding
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
        // Compact view — a denser 13×4 grid with a ?123 layer, for small
        // screens.  Orthogonal to savedLayout (which letter arrangement);
        // see the Compact view block near `currentLayout` for how the two
        // resolve into one layout id.
        property bool savedCompactView: false
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
        // keyboard's content (`height: outerLayout.implicitHeight + 80`),
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

    // Off-screen "Tuck away" state (X11 only — see docs/architecture/GOTCHAS.md
    // "Tuck away"). `tucked`: the keyboard is parked off the bottom edge as a
    // DOCK-type window (the one type GNOME/Mutter won't clamp back on-screen),
    // with only the title bar peeking in as a grab handle. preTuckX/Y hold the
    // on-screen position to restore when un-tucked. tuckSupported gates the
    // title-bar button (false off X11). The tuck position is deliberately never
    // persisted (see the saveGeometryTimer guard).
    property bool tuckSupported: false
    property bool tucked: false
    property real preTuckX: 0
    property real preTuckY: 0

    // Flip the keyboard between its normal on-screen state and the parked,
    // mostly-off-screen state. Ordering matters both ways (the window-type
    // change and the move travel on separate X connections, so we sequence
    // them): tucking flips to DOCK first, then moves off-screen a beat later
    // (tuckMoveTimer) so Mutter sees DOCK before the off-work-area move and
    // doesn't clamp it; un-tucking moves back on-screen first, then reverts to
    // NORMAL so the re-clamp lands on an already-on-screen window (a no-op).
    function toggleTuck() {
        if (!root.tuckSupported || !keyboard) return
        if (!root.tucked) {
            root.preTuckX = root.x
            root.preTuckY = root.y
            root.tucked = true
            keyboard.setWindowDock(root, true)
            tuckMoveTimer.restart()
        } else {
            root.x = root.preTuckX
            root.y = root.preTuckY
            root.tucked = false
            keyboard.setWindowDock(root, false)
        }
    }

    Timer {
        id: tuckMoveTimer
        interval: 60
        repeat: false
        onTriggered: {
            // Slide down so only the title bar remains on-screen at the bottom
            // edge as a grab handle; the keys hang off the bottom of the work
            // area (allowed now that the window is DOCK-typed).
            root.x = root.preTuckX
            root.y = Screen.virtualY + Screen.height - titleBar.height
        }
    }

    // Safety net: if the keyboard was parked off-screen (DOCK) and then hidden
    // and re-shown via the tray, bring it back to a usable on-screen NORMAL
    // state instead of reappearing off-screen. Tuck's own move doesn't change
    // visibility, so this only fires on the tray hide→show path.
    onVisibilityChanged: {
        if (root.tucked && root.visibility !== Window.Hidden) {
            root.x = root.preTuckX
            root.y = root.preTuckY
            root.tucked = false
            keyboard.setWindowDock(root, false)
        }
    }

    // Debounce window-resize writes — onWidthChanged / onHeightChanged
    // fire on every pixel during a drag, and Settings.write hits the
    // OS registry/config synchronously.  Wait 300 ms after the last
    // change before persisting.
    Timer {
        id: saveGeometryTimer
        interval: 300
        repeat: false
        onTriggered: {
            // Never persist the parked off-screen position — preTuckX/Y (the
            // last real on-screen spot) was already saved before tucking, and
            // un-tucking restores it and fires a fresh save.
            if (root._geometryRestored && !root.tucked) {
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

        // Load saved preferences.  Compact view forbids the side panels
        // (see onCompactViewChanged), and it is restored from settings
        // before this runs, so honour it on a cold start too, otherwise a
        // user who quit in compact would come back with the panels on.
        root.showNavigation = appSettings.savedShowNavigation && !root.compactView
        root.showNumpad = appSettings.savedShowNumpad && !root.compactView
        root.showFunctionRow = appSettings.savedShowFunctionRow
        root.currentTheme = appSettings.savedTheme
        root.suggestionsEnabled = appSettings.savedSuggestionsEnabled

        // Load audio setting
        if (keyboard && appSettings.savedAudioEnabled) {
            keyboard.setAudioEnabled(true)
        }

        // Enter a clean input state: drop any sticky modifier (Shift/
        // Ctrl/Alt/Win) left held from a prior run, a crash mid-chord, or
        // an external grab, and clear its key highlight. Without this a
        // stuck Super on Linux turns every click into a window-manager
        // move/resize gesture and the user can't recover.
        if (keyboard) keyboard.resetModifiers()

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

        // Load saved keyboard layout.  applyLayout() resolves the letter
        // arrangement + the compact-view toggle into a single layout id, so
        // both preferences are honoured on a cold start.
        root.currentLayout = appSettings.savedLayout
        if (keyboard) applyLayout()
        else root.layoutRows = []

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

        // The off-screen "Tuck away" button only works on X11 (the only
        // session where GNOME clamps the window on-screen and the DOCK-type
        // escape applies). Hide it everywhere else.
        if (keyboard) root.tuckSupported = keyboard.tuckSupported()

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
    //
    // The save is skipped while compact view is on, because compact forces
    // both panels off (see onCompactViewChanged) and that forced state is
    // not a preference.  Persisting it would mean turning compact on once
    // silently threw away the user's real panel choice, so leaving compact
    // could never bring the panels back.
    onShowNavigationChanged: {
        if (_loaded) root.width += showNavigation ? 220 : -220
        if (!compactView) appSettings.savedShowNavigation = showNavigation
    }
    onShowNumpadChanged: {
        if (_loaded) root.width += showNumpad ? 250 : -250
        if (!compactView) appSettings.savedShowNumpad = showNumpad
    }

    // Switching view density re-resolves the layout, then resizes the window
    // so the *key size* is preserved rather than the window width — giving
    // the screen back is the entire point of compact view, so keeping the
    // window the same size and just growing the keys would miss it.
    //
    // Compact view and the side panels are mutually exclusive.  The nav
    // cluster and numpad cost ~470 px of width, which is precisely what
    // compact exists to hand back, so offering all three independently
    // produced combinations that undid the mode the user just picked.
    // Compact wins: both panels are forced off here and their toggles are
    // disabled in Settings while it is on.  The user's real preference
    // still lives in appSettings (the change handlers above stop writing
    // to it while compact is on), so leaving compact restores whatever
    // they had.
    onCompactViewChanged: {
        appSettings.savedCompactView = compactView
        if (!_loaded || !keyboard) return
        // Captured before the panels move, so the post-toggle resize below
        // preserves the key size the user is actually looking at.
        var keyWBefore = root.keyW
        if (compactView) {
            root.showNavigation = false
            root.showNumpad = false
        } else {
            root.showNavigation = appSettings.savedShowNavigation
            root.showNumpad = appSettings.savedShowNumpad
        }
        applyLayout()
        Qt.callLater(function() {
            var target = Math.round(keyWBefore * root.totalKeyUnits + root.layoutFixedPixels)
            root.width = Math.max(root.minimumWidth, target)
        })
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

    // Special actions a layout key may auto-repeat on hold.  Deletion and
    // caret motion are the only ones where "hold to do it again" is what a
    // user means; Enter / Tab / Esc firing twenty times would be hostile.
    readonly property var repeatableActions: [
        "backspace", "delete", "left", "right", "up", "down",
        "pageup", "pagedown"
    ]

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

    // Two registries, both populated by each KeyButton on creation. They are
    // deliberately NOT the same list, and the split is load-bearing:
    //
    //  • charKeyRegistry: single character keys only. Feeds
    //    pushSwipeLayout(), i.e. the recogniser's key-centre map. A
    //    "backspace" centre in there is a phantom letter in every shape
    //    match, so this filter must stay exactly as strict as it is.
    //  • tappableKeyRegistry: EVERY key under the swipe overlay. Feeds the
    //    overlay's hit testing only.
    //
    // One list served both consumers until swipe typing was found to make
    // Backspace, Enter, Tab, the arrows, the modifiers, ?123 and the Number
    // Row's Esc dead taps: the overlay takes every press in its rectangle,
    // then resolved it against a char-only list that could not contain them.
    // Widening the char filter would have fixed the taps and corrupted swipe
    // decoding; giving each consumer its own list fixes one without touching
    // the other.
    property var charKeyRegistry: []
    property var tappableKeyRegistry: []

    function registerCharKey(item, kd) {
        // Registered first and unconditionally: the overlay must be able to
        // hit-test every key it covers, whatever its type.
        tappableKeyRegistry.push({ item: item, kd: kd })
        if (!kd || kd.type !== "char" || !kd.key || kd.key.length !== 1) return
        charKeyRegistry.push({ item: item, kd: kd })
        swipeLayoutPushTimer.restart()
    }

    function unregisterCharKey(item) {
        for (var t = 0; t < tappableKeyRegistry.length; t++) {
            if (tappableKeyRegistry[t].item === item) {
                tappableKeyRegistry.splice(t, 1)
                break
            }
        }
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
            // Skip keys that are not on screen. A KeyButton inside a hidden
            // panel is still constructed and still registers, so with the
            // Number Row switched off its keys contribute centres computed
            // from stale geometry, and this map is keyed by character, so a
            // hidden key silently overwrites the visible one of the same
            // name. Inert today only because the Number Row holds nothing but
            // digits and Esc, and SwipeRecognizer.set_layout drops every
            // non-alphabetic key: the corruption cannot reach decoding until
            // some future panel carries a letter. Cheap to be correct now
            // rather than to debug then.
            if (!entry.item.visible) continue
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
    // Right-click "lock" (held-down) state for each modifier — drives a
    // distinct indicator so a locked key reads differently from a sticky
    // one-shot press.
    property bool shiftLocked: keyboard ? keyboard.shiftLocked : false
    property bool ctrlLocked: keyboard ? keyboard.ctrlLocked : false
    property bool altLocked: keyboard ? keyboard.altLocked : false
    property bool winLocked: keyboard ? keyboard.winLocked : false
    property string layer: keyboard ? keyboard.currentLayer : "lower"
    property bool showNumbers: layer === "numbers"
    property bool showSymbols: layer === "symbols"
    
    // Predictions from hybrid engine
    property var predictions: []
    property bool predictionsLoading: false

    // Keyboard layout (data-driven from JSON)
    property var layoutRows: keyboard ? keyboard.getLayoutRows() : []
    property string currentLayout: appSettings.savedLayout

    // ===== Compact view =====
    // A *view* preference, orthogonal to which letter arrangement is picked:
    // `currentLayout` stays "qwerty"/"dvorak"/"colemak" and the compact
    // variant is derived from it ("qwerty" + "-compact").  Layouts with no
    // compact variant fall back to full size, so the toggle is always safe.
    property bool compactView: appSettings.savedCompactView
    property var availableLayouts: keyboard ? keyboard.getAvailableLayouts() : []

    function hasLayout(id) {
        for (var i = 0; i < availableLayouts.length; i++)
            if (availableLayouts[i].id === id) return true
        return false
    }

    // Resolve the layout id the bridge should actually be on, given the
    // letter arrangement + the compact toggle.
    function resolveLayoutId(base, compact) {
        var variant = base + "-compact"
        return (compact && hasLayout(variant)) ? variant : base
    }

    function applyLayout() {
        if (!keyboard) return
        keyboard.setLayout(resolveLayoutId(root.currentLayout, root.compactView))
        root.layoutRows = keyboard.getLayoutRows()
        root.activeLayer = "base"
    }

    // A layout may split its rows across named layers — the compact view has
    // a "base" layer and a "sym" (?123) layer.  Rows with no `layer` field
    // always render, so the full-size layouts are untouched by this.
    property string activeLayer: "base"
    property var visibleRows: {
        var out = []
        for (var i = 0; i < layoutRows.length; i++) {
            var r = layoutRows[i]
            if (!r.layer || r.layer === root.activeLayer) out.push(r)
        }
        return out
    }

    // Layout toggles (modular panels)
    //
    // The standalone number row is derived, not a setting.  Digits are
    // never optional: the full-size layouts carry a `number` row inside
    // their layout JSON, and compact moves the digits behind the ?123 hop,
    // so the standalone panel is exactly the compact layouts' missing row.
    // Deriving it means digits are always on screen in both views, with no
    // toggle that could either hide them or stack a second, narrower number
    // row on top of a layout that already has one.  Keyed off the layout's
    // own rows rather than `compactView` so a letter arrangement with no
    // compact variant (which silently falls back to full size) still
    // resolves correctly.
    property bool showNumberRow: {
        var rows = root.layoutRows
        if (!rows || !rows.length) return false
        for (var i = 0; i < rows.length; i++)
            if (rows[i].id === "number") return false
        return true
    }
    property bool showFunctionRow: false
    property bool showNavigation: false
    property bool showNumpad: false
    property bool showSettings: false
    property bool showHelp: false
    property bool suggestionsEnabled: true

    // Privacy
    property bool privacyMode: keyboard ? keyboard.privacyMode : false
    // False only when the platform's auto-detection backend failed to
    // initialise (no AT-SPI on Linux, no Accessibility grant on macOS,
    // etc). Defaults true so nothing warns before the bridge connects.
    property bool passwordDetectionAvailable: keyboard ? keyboard.passwordDetectionAvailable : true

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
    // The visible gap between two keycaps is `keySpacing` plus twice
    // KeyButton's own 1 px background inset, so keep this modest: the
    // inset already guarantees the caps never touch.  Wider gaps just
    // shrink the click targets, which is the wrong trade on a
    // mouse-driven OSK.
    property real keySpacing: Math.max(1, Math.floor(root.width * 0.0018))

    // The widest visible row drives sizing — every narrower row is centred
    // against it.  Derived from the layout data rather than hardcoded so a
    // layout with a different column count sizes itself correctly: the
    // full-size layouts resolve to the historical 15.5u / 14 gaps (number
    // row: Esc + ` + 10 digits + - + = + 1.5u Backspace), while the compact
    // view resolves to 13.0u / 12 gaps.  The fallback keeps the pre-load
    // frame identical to what the full-size layout will produce.
    //
    // Units and gaps are maxed INDEPENDENTLY, and that is load-bearing.  A
    // row's pixel width is `units * keyW + gaps * keySpacing`, so the row
    // that needs the most units and the row that needs the most gaps are not
    // necessarily the same row, and in a compact layout they never are,
    // because every row is deliberately the same 13.0u while the letter row
    // carries one more key than its neighbours.  Reading `gaps` off whichever
    // row happened to win the units comparison under-reserved exactly one
    // keySpacing, so the widest row rendered 2-3 px past the content area and
    // ate into the 8 px margin (worst at minimumWidth, which is computed from
    // the same number).  Guarded by
    // tests/test_qml_compact_view.py::TestEveryRowFitsTheContentArea.
    property var _widestRow: {
        var bestUnits = 0, bestGaps = 0
        var rows = root.visibleRows
        for (var i = 0; i < rows.length; i++) {
            var keys = rows[i].keys
            if (!keys || !keys.length) continue
            var u = 0
            for (var j = 0; j < keys.length; j++) u += (keys[j].width || 1.0)
            if (u > bestUnits) bestUnits = u
            if (keys.length - 1 > bestGaps) bestGaps = keys.length - 1
        }
        return bestUnits > 0 ? { units: bestUnits, gaps: bestGaps }
                             : { units: 15.5, gaps: 14 }
    }

    // Nav panel: 3 keys × 1.0 = 3.0 units;  Numpad: 4 keys × 1.0 = 4.0 units
    // (The 0.9× multiplier on nav/numpad keys was bumped to 1.0× so
    // labels like "PrtSc"/"PgDn" don't clip — keep this in sync with
    // the keyW bindings on the panels themselves below.)
    property real totalKeyUnits: _widestRow.units
        + (showNavigation ? 3.0 : 0)
        + (showNumpad ? 4.0 : 0)

    // Fixed-pixel overhead: margins(8×2=16) + widest-row gaps(N-1 × keySpacing)
    // + per-panel: separator(1) + 2 inner grid gaps + 2×RowLayout spacing(6)
    property real layoutFixedPixels: 16 + _widestRow.gaps * keySpacing
        + (showNavigation ? 1 + 2 * keySpacing + 12 : 0)
        + (showNumpad ? 1 + 3 * keySpacing + 12 : 0)

    property real keyW: Math.max(30, (root.width - layoutFixedPixels) / totalKeyUnits)
    // keyH simply tracks keyW at the keycap aspect ratio.  This works
    // because the window's `height` is bound to `outerLayout.implicitHeight + 80`
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

    // Key tint for the "accent" style: the editing keys a user reaches for
    // without looking (Esc, Tab, Shift, Backspace, Del) on the compact
    // layouts, where the grid is uniform and there are no size cues to tell
    // them apart from the letters.
    //
    // A wash of the accent over the theme's own key colour, NOT the raw
    // accent. The accent is chosen to stand out against the *background*, so
    // painting a whole key with it fights the key *label*: three of the nine
    // themes have a pale accent (Blackboard "#ffffaa", Spaceship "#00ff9f")
    // and Typewriter is a light theme with near-black text.
    //
    // The wash strength is derived, not fixed. A flat 35% was measured
    // against every theme and dropped the label below WCAG AA (4.5:1) on
    // five of the nine: Blackboard 6.19 -> 2.66, Vaporwave 6.17 -> 2.97,
    // Forest 7.53 -> 3.33, Spaceship 10.37 -> 3.85, Ocean 6.96 -> 4.44. That
    // is the worst possible place to lose contrast, because these are the
    // keys the style exists to make findable. Forest could not be rescued by
    // swapping the label to black or white either (best case 4.37), so the
    // wash itself has to yield. accentWashFor() walks the alpha down from
    // 0.35 until the theme's own text colour clears 4.5:1, which leaves the
    // five compliant themes untouched and backs the other four off to
    // 0.12-0.33. Guarded by tests/test_qml_compact_view.py::TestAccentKeysStayReadable.
    //
    // Where the wash has to back off it stops carrying the cue on its own,
    // so accent keys also take an accent-coloured border (accentKeyBorder).
    // A border sits beside the label rather than behind it, so it can be the
    // full-strength accent on every theme without costing any contrast.
    // The same "muted, not raw" reasoning is why Enter uses "#2a5a2a".
    function relativeLuminance(c) {
        function channel(v) {
            return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b)
    }
    function contrastRatio(a, b) {
        var la = root.relativeLuminance(a)
        var lb = root.relativeLuminance(b)
        return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
    }
    function accentWashFor(key, accent, text) {
        for (var a = 0.35; a > 0.005; a -= 0.01) {
            var candidate = Qt.tint(key, Qt.rgba(accent.r, accent.g, accent.b, a))
            if (root.contrastRatio(text, candidate) >= 4.5)
                return candidate
        }
        return key
    }
    readonly property color accentKeyColor: root.accentWashFor(
        root.themeKeyColor, root.themeAccent, root.themeTextColor)
    readonly property color accentKeyBorder: root.themeAccent
    property color themeBorder: activeTheme.border

    // Update state when bridge emits signals
    Connections {
        target: keyboard
        function onShiftActiveChanged(active) { root.shiftOn = active }
        function onCapsLockActiveChanged(active) { root.capsOn = active }
        function onCtrlActiveChanged(active) { root.ctrlOn = active }
        function onAltActiveChanged(active) { root.altOn = active }
        function onWinActiveChanged(active) { root.winOn = active }
        function onShiftLockedChanged(locked) { root.shiftLocked = locked }
        function onCtrlLockedChanged(locked) { root.ctrlLocked = locked }
        function onAltLockedChanged(locked) { root.altLocked = locked }
        function onWinLockedChanged(locked) { root.winLocked = locked }
        function onCurrentLayerChanged(newLayer) { root.layer = newLayer }
        
        // Prediction updates
        function onPredictionsChanged(preds) { root.predictions = preds }
        function onPredictionsRefined(preds) { root.predictions = preds }
        function onPredictionLoading(loading) { root.predictionsLoading = loading }
        
        // Layout updates
        // Always land on the base layer after a layout swap — leaving the
        // user on a ?123 layer that the new layout may not even define
        // would render an empty keyboard.
        function onLayoutDataChanged(rows) {
            root.layoutRows = rows
            root.activeLayer = "base"
        }

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
            // Tall, generous drag strip: the window never takes focus, so the
            // title bar is the only way to move it — a small bar is a hard
            // target for limited motor control. If you change this, also bump
            // outerLayout.anchors.topMargin and the root window-height offset
            // (`outerLayout.implicitHeight + 80`) by the same amount.
            height: 48
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
                anchors.rightMargin: 332  // Leave space for buttons (Learning switch, Snippets, Tuck, etc.)
                cursorShape: Qt.SizeAllCursor
                
                property real startMouseX
                property real startMouseY
                property real startWinX
                property real startWinY

                // Manual x/y drag on EVERY platform — deliberately NOT
                // startSystemMove(). This window is WindowDoesNotAcceptFocus,
                // and on X11/Mutter a WM-driven _NET_WM_MOVERESIZE interactive
                // move intermittently fails to take a pointer grab for a
                // non-focusable window. Worse, startSystemMove() returns true
                // the instant it *sends* the request (not when the WM performs
                // it), so the old code set sysMoveActive=true and suppressed
                // this manual fallback — leaving the drag silently dead on the
                // presses where Mutter declined. The ButtonPress here
                // establishes the implicit X11 pointer grab; because we never
                // call startSystemMove() we never release it, so every
                // MotionNotify is delivered to this MouseArea unconditionally
                // and the drag tracks the cursor deterministically (same path
                // Windows always used, and the leftResize handle below).
                // Trade-off: Mutter clamps the programmatic move on-screen, so
                // the keyboard can't be pushed past a screen edge — use the
                // Minimize button to stash it. See docs/architecture/GOTCHAS.md.
                onPressed: function(mouse) {
                    var global = mapToGlobal(mouse.x, mouse.y)
                    startMouseX = global.x
                    startMouseY = global.y
                    startWinX = root.x
                    startWinY = root.y
                }

                onPositionChanged: function(mouse) {
                    if (!pressed) return
                    var global = mapToGlobal(mouse.x, mouse.y)
                    root.x = startWinX + (global.x - startMouseX)
                    root.y = startWinY + (global.y - startMouseY)
                }
            }
            
            // Drag handle — a prominent grip so the title bar (which never
            // takes focus, so there's no cursor cue) is an easy, obvious
            // target to grab. dragArea above does the actual move; this is
            // the visual affordance and a bigger aim point. It accepts no
            // mouse events itself, so presses fall through to dragArea.
            Rectangle {
                anchors.left: parent.left
                anchors.leftMargin: 10
                anchors.verticalCenter: parent.verticalCenter
                height: 30
                width: gripGrid.implicitWidth + 22
                radius: 8
                color: Qt.rgba(root.themeTextColor.r, root.themeTextColor.g,
                               root.themeTextColor.b, 0.12)
                Grid {
                    id: gripGrid
                    anchors.centerIn: parent
                    rows: 2
                    columns: 6
                    rowSpacing: 4
                    columnSpacing: 5
                    Repeater {
                        model: 12
                        Rectangle {
                            width: 4; height: 4; radius: 2
                            color: Qt.rgba(root.themeTextColor.r, root.themeTextColor.g,
                                           root.themeTextColor.b, 0.6)
                        }
                    }
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

                // Privacy mode toggle (learning on/off).  History: a
                // play/pause icon first (misread as "is something
                // playing"), then a plain text label that swapped
                // between "Learning" and "Paused".  The label alone
                // still had to be *read* to know the state, and it was
                // ambiguous about whether it named the state or the
                // action a click would take.
                //
                // Now an actual switch: the label is static ("Learning",
                // the thing being toggled) and the track carries the
                // state: knob right + accent track = on, knob left +
                // red track = paused.  Shape says on/off at a glance,
                // which is what the two earlier attempts were missing.
                // The prediction bar independently spells out "Learning
                // paused" while it's off, so nothing depends on
                // decoding the switch.
                Rectangle {
                    id: privacyToggle
                    // Sized for track + gap + "Learning" at 11 px
                    // DemiBold.  The label is static so this never
                    // reflows; if you change it, bump this width AND
                    // dragArea.rightMargin further up together.
                    width: 96
                    height: 24
                    radius: 4
                    color: root.privacyMode ? "#4a2a2a" : privacyBtn.containsMouse ? "#444" : "transparent"
                    border.color: root.privacyMode ? "#ff6b6b" : "transparent"
                    border.width: root.privacyMode ? 1 : 0

                    ToolTip.visible: privacyBtn.containsMouse
                    // When auto-detection has no working backend this
                    // session (see KeyboardBridge.passwordDetectionAvailable),
                    // this switch is the user's only protection against
                    // typing a password into the model, so say so right on
                    // the control instead of a dialog they'd have to dismiss.
                    ToolTip.text: (root.privacyMode
                                  ? qsTr("Learning is off. Click to resume learning from your typing")
                                  : qsTr("Learning is on. Click to pause learning from your typing"))
                                  + (root.passwordDetectionAvailable ? ""
                                     : qsTr(" (auto-detect unavailable this session: this is your only protection)"))
                    ToolTip.delay: 400

                    Row {
                        anchors.centerIn: parent
                        spacing: 6

                        // Switch track
                        Rectangle {
                            id: privacyTrack
                            width: 28
                            height: 15
                            radius: height / 2
                            anchors.verticalCenter: parent.verticalCenter
                            color: root.privacyMode ? "#5c2b2b" : root.themeAccent
                            border.color: root.privacyMode ? "#ff6b6b" : Qt.lighter(root.themeAccent, 1.4)
                            border.width: 1

                            Behavior on color { ColorAnimation { duration: 120 } }

                            // Knob
                            Rectangle {
                                width: 11
                                height: 11
                                radius: height / 2
                                y: 2
                                x: root.privacyMode ? 2 : privacyTrack.width - width - 2
                                color: root.privacyMode ? "#ff6b6b" : "#ffffff"

                                Behavior on x { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
                                Behavior on color { ColorAnimation { duration: 120 } }
                            }
                        }

                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: qsTr("Learning")
                            color: root.privacyMode ? "#ff6b6b" : "#bbb"
                            font.pixelSize: 11
                            font.weight: Font.DemiBold
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
                
                // Tuck away / Bring back (X11 only — root.tuckSupported).
                // Parks the keyboard off the bottom edge as a DOCK-type window
                // so GNOME/Mutter won't clamp it back on-screen, leaving the
                // title bar as a grab handle; tap again to restore. This is the
                // sanctioned "push it off-screen" affordance — the everyday
                // drag stays on-screen by design. See docs/architecture/GOTCHAS.md.
                Rectangle {
                    width: 28
                    height: 24
                    radius: 4
                    visible: root.tuckSupported
                    color: tuckBtn.containsMouse ? "#444" : "transparent"

                    ToolTip.visible: tuckBtn.containsMouse
                    ToolTip.text: root.tucked ? qsTr("Bring keyboard back")
                                              : qsTr("Tuck keyboard off-screen")
                    ToolTip.delay: 400

                    Text {
                        anchors.centerIn: parent
                        text: root.tucked ? "⤒" : "⤓"
                        font.pixelSize: 15
                        color: root.tucked ? root.themeAccent : "#999"
                    }

                    MouseArea {
                        id: tuckBtn
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.toggleTuck()
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
            anchors.topMargin: 52  // Account for the 48px title bar + 4px gap
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
                // Horizontal inset the pill's Text actually reserves on EACH
                // side.  Load-bearing that `computeFit` and the delegate read
                // the same number: the fitter's whole no-elide guarantee is
                // "pill width >= text width + what the delegate eats", so if
                // these two drift the guarantee is silently false.  They did
                // drift — the fitter floored padding at `hPad * 0.45` while
                // the delegate ate `2 * hPad * 0.28` = `hPad * 0.56`, a ~4 px
                // deficit at compact-view geometry.  Pills whose width came
                // out text-driven were then born one or two characters too
                // narrow, and only the leftover-slack water-fill below
                // rescued them.  With a full row and slack near zero, nothing
                // rescued them and they elided — the exact bug the fitter was
                // written to prevent.
                property real predTextInset: Math.max(6, predHorizontalPad * 0.28)
                // Right-edge zone owned by the clear-context button (its own
                // width + the 8 px right margin + an 8 px gap).  The pill row
                // is both *sized* and *centred* inside the space left over, so
                // a pill can never slide under the button, which used to
                // happen with four long words, hiding the last one behind the
                // ⟲.  Reserved on the right only: taking the same bite out of
                // the left as well would centre the row in the window but cost
                // twice the width, and long words would start eliding sooner.
                property real clearCtxReserve: root.suggestionsEnabled ? predPillHeight + 16 : 0
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
                    // Named so tests/test_qml_prediction_bar.py can assert the
                    // row never overlaps clearContextButton.
                    objectName: "predictionRow"
                    anchors.verticalCenter: parent.verticalCenter
                    // Centred in the bar minus the clear-button zone, floored
                    // at the 8 px left margin.  Not anchors.centerIn: that
                    // centres on the full bar and pushes the right-hand pill
                    // under the ⟲ button.
                    x: Math.max(8, (predBar.width - predBar.clearCtxReserve - width) / 2)
                    spacing: 8
                    visible: root.suggestionsEnabled && !root.privacyMode

                    // Measures word widths in the same font the pills render
                    // so the fair-share allocation below can size every pill
                    // centrally, without each delegate publishing its own
                    // implicitWidth back up to the parent.
                    FontMetrics {
                        id: predMetrics
                        font.pixelSize: predBar.predFontSize
                        font.weight: Font.Medium
                        font.family: "Ubuntu, Noto Sans, sans-serif"
                    }

                    // What a pill must be at least as wide as to render `word`
                    // whole.
                    //
                    // It has to be FontMetrics rather than a real Text: the
                    // only way to ask a Text is to assign its `text` and read
                    // back `implicitWidth`, and doing that inside this binding
                    // makes `fit` depend on a property `fit` itself writes,
                    // which the engine reports as a binding loop. FontMetrics
                    // answers through method calls, which create no such
                    // dependency.
                    //
                    // Take the larger of the two metrics. `advanceWidth` sums
                    // per-glyph advances; `boundingRect` covers the ink,
                    // including side bearings that stick out past the advance.
                    // They are identical under Windows' font rendering, which
                    // is why measuring by advance alone looked correct here,
                    // and they diverge under freetype, where "document" elided
                    // inside a pill this function had called wide enough. Ceil
                    // on top, because Text elides on a sub-pixel overflow and
                    // the width handed back is a float.
                    //
                    // NOTE this cannot be verified on a machine without the
                    // real fonts installed: under the offscreen platform
                    // plugin Qt falls back to a fixed-width placeholder where
                    // every glyph is exactly `pixelSize` wide and both metrics
                    // agree by construction, so the no-elide sweep in
                    // tests/test_qml_prediction_bar.py is near-vacuous locally
                    // and only means something on CI's Linux runner.
                    function measure(word) {
                        return Math.ceil(Math.max(predMetrics.advanceWidth(word),
                                                  predMetrics.boundingRect(word).width))
                    }

                    // Fit whole words, never a truncated one. `fit` is
                    // { words, widths }: the prefix of the ranked predictions
                    // that physically fits, plus each one's pixel width. The
                    // binding reads predictions, window width and pill geometry
                    // so it re-evaluates whenever any of them change.
                    property var fit: predRow.computeFit(
                        root.predictions, root.width, predBar.predFontSize,
                        predBar.predHorizontalPad, predBar.predMinWidth,
                        predBar.predPillHeight, predRow.spacing,
                        predBar.clearCtxReserve, predBar.predTextInset)

                    // Kept as the name the width tests read.
                    readonly property var pillWidthList: predRow.fit.widths

                    // Three rules, in priority order:
                    //
                    //  1. No pill is ever elided.  Eight long candidates in a
                    //     940 px window rendered as eight identical "docu…"
                    //     pills, strictly worse than five readable ones, since
                    //     you cannot pick a suggestion you cannot read.  So the
                    //     row compresses padding first, then *drops* pills from
                    //     the tail (the lowest-ranked ones) until the survivors
                    //     fit at full text width.
                    //  2. Leftover space is handed back as padding max-min
                    //     fair, so a short word can't hog room a long one
                    //     needs. This is the property that stops "I"/"the" in
                    //     half-empty pills beside a clipped long word.
                    //  3. `reserve` keeps the row clear of the ⟲ button.
                    //     Without it the row is sized to the whole window and
                    //     the right-hand pill renders underneath it.
                    //
                    // Only one case can still elide: a single word too long for
                    // the whole bar, where there is nothing left to drop. The
                    // hover ToolTip covers it.
                    function computeFit(preds, totalWidth, fontSize, hPad, minNat, pillH, spacing, reserve, inset) {
                        var out = { words: [], widths: [] }
                        var n = preds.length
                        if (n <= 0)
                            return out

                        var avail = totalWidth - 32 - reserve
                        // Padding compresses to this before any pill is
                        // dropped.  It is exactly what the delegate's Text
                        // reserves (`inset` per side) and never less: this
                        // number IS the no-elide guarantee, so deriving it
                        // from anything but the delegate's own inset makes
                        // "tight" a width the word provably cannot render in.
                        var minPad = Math.max(14, 2 * inset)

                        var text = []
                        for (var i = 0; i < n; i++)
                            text.push(predRow.measure(preds[i]))

                        // Narrowest a pill may be and still show its whole word.
                        function tight(idx) { return Math.max(minNat, text[idx] + minPad) }

                        var count = n
                        while (count > 1) {
                            var need = (count - 1) * spacing
                            for (var j = 0; j < count; j++)
                                need += tight(j)
                            if (need <= avail)
                                break
                            count--
                        }

                        var widths = []
                        for (var k = 0; k < count; k++)
                            widths.push(tight(k))
                        out.words = preds.slice(0, count)
                        out.widths = widths

                        var slack = avail - (count - 1) * spacing
                        for (var s = 0; s < count; s++)
                            slack -= widths[s]

                        if (slack <= 0) {
                            // One word wider than the entire bar: clamp it so
                            // it can't slide under the ⟲, and let it elide.
                            if (count === 1)
                                widths[0] = Math.max(pillH * 1.4, avail)
                            return out
                        }

                        // Water-fill the slack back as padding: a pill that
                        // wants less than the current fair share settles at
                        // what it wants and releases the rest, raising the
                        // share for everyone else (≤ count passes).
                        var want = []
                        for (var w = 0; w < count; w++)
                            want.push(Math.max(0, Math.max(minNat, text[w] + hPad) - widths[w]))
                        var settled = new Array(count)
                        var unsettled = count
                        for (var pass = 0; pass < count && unsettled > 0; pass++) {
                            var share = slack / unsettled
                            var changed = false
                            for (var m = 0; m < count; m++) {
                                if (!settled[m] && want[m] <= share) {
                                    widths[m] += want[m]
                                    slack -= want[m]
                                    settled[m] = true
                                    unsettled--
                                    changed = true
                                }
                            }
                            if (!changed)
                                break
                        }
                        // Still-hungry pills split what's left evenly.
                        if (unsettled > 0) {
                            var even = slack / unsettled
                            for (var q = 0; q < count; q++)
                                if (!settled[q])
                                    widths[q] += even
                        }
                        return out
                    }

                    Repeater {
                        model: root.suggestionsEnabled && !root.privacyMode ? predRow.fit.words : []
                        delegate: Rectangle {
                            width: index < predRow.fit.widths.length
                                   ? predRow.fit.widths[index]
                                   : predBar.predMinWidth
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
                                // Tests assert none of these ever report
                                // `truncated`. See test_qml_prediction_bar.py.
                                objectName: "predictionPillText"
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.left: parent.left
                                anchors.right: parent.right
                                // Same property computeFit sizes against — see
                                // predBar.predTextInset. Do not inline this.
                                anchors.leftMargin: predBar.predTextInset
                                anchors.rightMargin: predBar.predTextInset
                                horizontalAlignment: Text.AlignHCenter
                                text: modelData
                                // Predictions can originate from an imported
                                // vocabulary pack's dictionary.txt, which is
                                // unsanitised: force plain text so a crafted
                                // entry can't auto-render as HTML.
                                textFormat: Text.PlainText
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
                    objectName: "clearContextButton"
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

                    // ===== Number Row (` 1-0 - =) =====
                    // Above the function row so the digits sit adjacent to
                    // the letters, the way they do on a physical keyboard.
                    // Full key height: unlike F-keys these are typed
                    // constantly, so they get a full-size target.
                    Comp.NumberRow {
                        // Lets the panel-width tests find this without
                        // property-sniffing; see TestPanelsSitFlushWithTheGrid.
                        objectName: "numberRowPanel"
                        visible: root.showNumberRow
                        Layout.alignment: Qt.AlignHCenter
                        keyW: root.keyW
                        keyH: root.keyH
                        keySpacing: root.keySpacing
                        keyColor: Qt.darker(root.themeKeyColor, 1.3)
                        accentKeyColor: root.accentKeyColor
                        keyPressedColor: root.themeKeyPressed
                        keyTextColor: root.themeTextColor
                        accentColor: root.themeAccent
                        borderColor: root.themeBorder
                        shiftOn: root.shiftOn
                        rightClickShift: root.rightClickShift
                        keyPreviewEnabled: root.keyPreviewEnabled
                        registerFn: root.registerCharKey
                        unregisterFn: root.unregisterCharKey
                        previewFn: root.showKeyPreview
                        hidePreviewFn: root.hideKeyPreview
                    }

                    // ===== Function Row (F1-F12) =====
                    Comp.FunctionRow {
                        objectName: "functionRowPanel"
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
                    model: root.visibleRows

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
                                // Right-click "lock" indicator — a held-down
                                // modifier (see onKeyRightPressed below).
                                isLocked: {
                                    if (!kd.stateKey) return false
                                    switch(kd.stateKey) {
                                        case "shiftOn": return root.shiftLocked
                                        case "ctrlOn": return root.ctrlLocked
                                        case "altOn": return root.altLocked
                                        case "winOn": return root.winLocked
                                        default: return false
                                    }
                                }
                                keyColor: {
                                    switch(kd.style || "default") {
                                        case "secondary": return Qt.darker(root.themeKeyColor, 1.3)
                                        case "special": return Qt.darker(root.themeKeyColor, 1.15)
                                        case "accent": return root.accentKeyColor
                                        case "enter": return "#2a5a2a"
                                        default: return root.themeKeyColor
                                    }
                                }
                                keyPressedColor: root.themeKeyPressed
                                keyTextColor: root.themeTextColor
                                accentColor: root.themeAccent
                                // Accent keys carry the cue on their border as
                                // well as their fill: the fill has to stay weak
                                // enough to keep the label readable (see
                                // accentWashFor), the border does not.
                                borderColor: (kd.style || "default") === "accent"
                                             ? root.accentKeyBorder : root.themeBorder

                                // Repeat-worthy specials only.  Character keys
                                // must never repeat (see KeyButton.qml for the
                                // rationale).  Same set the Navigation panel
                                // repeats: the compact layouts put Del and
                                // the arrows in the main grid, so holding them
                                // has to behave the same there as it does in
                                // the side panel.
                                enableRepeat: kd.type === "special"
                                              && root.repeatableActions.indexOf(kd.action) !== -1
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
                                    } else if (kd.type === "layer") {
                                        // Layer switch (?123 / =\< / ABC),
                                        // purely a QML-side view change.
                                        // Deliberately does NOT go through
                                        // keyboard.setLayout: that would persist
                                        // as the user's layout preference and
                                        // report the symbol layer from
                                        // getCurrentLayout().
                                        //
                                        // Drop a held Shift on the way. The
                                        // symbol pages carry no Shift key, so
                                        // one carried in from the letters page
                                        // could never be cleared from there,
                                        // and it would not merely be stuck: the
                                        // modifier is held at the OS level, so
                                        // tapping "1" would emit "!" while the
                                        // keycap still read "1". Every glyph
                                        // Shift used to reach on these pages now
                                        // has a key of its own, so there is
                                        // nothing left for it to do here.
                                        // releaseShift also clears a right-click
                                        // lock, which is what we want: a locked
                                        // Shift would mismatch just as loudly.
                                        // Caps is left alone; it only affects
                                        // letters, and these pages have none.
                                        //
                                        // releaseShift, not "if (shiftOn)
                                        // toggleShift": asking for the end state
                                        // is idempotent, so it cannot turn Shift
                                        // *on* here if root.shiftOn has drifted
                                        // from the bridge (it is a mirror kept
                                        // alive by signal delivery, not a live
                                        // binding, since the Connections handler
                                        // assigns to it).
                                        keyboard.releaseShift()
                                        root.activeLayer = kd.target || "base"
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
                                    // Right-click a modifier → hold it down
                                    // (locked). Independent of the right-
                                    // click-shift setting: a modifier has no
                                    // "shifted variant", and holding Ctrl/
                                    // Shift/Alt is the whole point of the
                                    // gesture. Caps Lock is already a
                                    // persistent toggle, so it's skipped.
                                    if (kd.type === "modifier") {
                                        if (kd.action === "shift" || kd.action === "ctrl"
                                                || kd.action === "alt" || kd.action === "win")
                                            keyboard.lockModifier(kd.action)
                                        return
                                    }
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
                objectName: "navigationPanel"
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
                objectName: "numpadPanel"
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
            // Lets tests/test_qml_swipe_overlay.py assert the overlay really
            // is in the way, so a tap test cannot pass by the overlay simply
            // not being there.
            objectName: "swipeOverlay"
            x: mainLayout.x + mainKeyboard.x
            y: mainLayout.y + mainKeyboard.y
            width: mainKeyboard.width
            height: mainKeyboard.height
            z: 50
            enabled: root.swipeEnabled
            keyboardBridge: keyboard
            // Two lists on purpose: keyRegistry is the recogniser's
            // key-centre map (characters only), tapRegistry is hit testing
            // (everything). See registerCharKey.
            keyRegistry: root.charKeyRegistry
            tapRegistry: root.tappableKeyRegistry
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
                    textFormat: Text.PlainText
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
                            // Manual x/y drag on every platform — same reason
                            // as the main-window dragArea: this window is
                            // WindowDoesNotAcceptFocus, so a WM-driven
                            // startSystemMove() is unreliable on X11/Mutter and
                            // its true-on-send return value used to suppress
                            // this fallback, killing the drag. Never call
                            // startSystemMove(), so the implicit press grab
                            // stays and motion tracking is deterministic.
                            onPressed: function(mouse) {
                                var g = mapToGlobal(mouse.x, mouse.y)
                                startMx = g.x; startMy = g.y
                                startX = snippetsWindow.x; startY = snippetsWindow.y
                            }
                            onPositionChanged: function(mouse) {
                                if (!pressed) return
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
                                        // Snippets round-trip through Data Backup import
                                        // (replace-on-import from a file the user picked),
                                        // so treat them as untrusted the same as pack data.
                                        textFormat: Text.PlainText
                                        color: root.themeTextColor
                                        font.pixelSize: 13
                                        font.weight: Font.DemiBold
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: (modelData.value && modelData.value.length)
                                              ? modelData.value : qsTr("empty, tap to fill in")
                                        textFormat: Text.PlainText
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
            compactView: root.compactView
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
            passwordDetectionAvailable: root.passwordDetectionAvailable

            onSettingChanged: function(setting, value) {
                if (setting === "functionRow") {
                    root.showFunctionRow = value
                    appSettings.savedShowFunctionRow = value
                } else if (setting === "navigation") {
                    // Compact forbids the side panels, and it enforces that
                    // only at the moment it is switched on.  The Settings
                    // toggle is disabled while compact is active, so the UI
                    // cannot reach here — this keeps the invariant true for
                    // any *other* caller of this dispatch (a preset, a
                    // shortcut, a restored panel), which would otherwise
                    // leave compact rendering the very panels it exists to
                    // remove until the user toggled compact again.
                    if (!root.compactView) root.showNavigation = value
                } else if (setting === "numpad") {
                    if (!root.compactView) root.showNumpad = value
                } else if (setting === "theme") {
                    root.currentTheme = value
                    appSettings.savedTheme = value
                } else if (setting === "windowOpacity") {
                    root.windowOpacity = value
                    appSettings.savedWindowOpacity = value
                } else if (setting === "layout") {
                    root.currentLayout = value
                    appSettings.savedLayout = value
                    root.applyLayout()
                } else if (setting === "compactView") {
                    root.compactView = value
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
