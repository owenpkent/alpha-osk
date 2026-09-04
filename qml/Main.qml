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
        // Reachable by name from the headless tests. QML's Settings element
        // batches its writes and flushes through QSettings on its own
        // schedule, so a test that reads a fresh QSettings straight after a
        // change sees nothing and cannot tell a write that was deferred from
        // one that never happened. Asserting on the property here tests the
        // half this code owns; the restore half is tested by seeding the
        // store before the engine loads.
        objectName: "appSettings"
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
        property bool savedIntelligentSpacing: true
        property bool savedSnippetDetection: true
        property bool savedAutoCapitalizeAfterPunctuation: false
        property bool savedAutoSaveOnExit: true
        property bool savedRightClickShift: true
        // Flash a small bubble above a key showing the character it just
        // typed (left- or right-click).  Mobile-keyboard "key preview".
        property bool savedKeyPreview: true
        // Hold a letter or digit to repeat it. See the `characterRepeat`
        // property for why the default moved to on.
        property bool savedCharacterRepeat: true
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

        // Snippets window position, same sentinel and same reasoning. It
        // used to reset to "centred above the keyboard" on every launch,
        // which undoes the one adjustment a user makes to it: dragging it
        // clear of the field they are filling in.
        property int savedSnippetsX: -1000000
        property int savedSnippetsY: -1000000

        // Whether the microphone appears in the suggestion bar at all.
        // Mirrored here as well as in dictation.json because QML has to
        // decide whether to reserve the bar's left edge before the bridge
        // has been asked anything, and a button that pops in a frame late
        // shifts every pill sideways under a pointer already moving.
        property bool savedDictationEnabled: false
        property int savedSymbolsX: -1000000
        property int savedSymbolsY: -1000000
        // Recently-tapped glyphs, newest first, as a JSON array of strings.
        // In the settings layer rather than a file of its own on purpose:
        // see the note on symbolsWindow.recent.
        property string savedRecentGlyphs: ""
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

    // Safety net: if the keyboard was parked off-screen (DOCK) and then put
    // away and brought back via the tray, restore it to a usable on-screen
    // NORMAL state instead of reappearing off-screen. Tuck's own move doesn't
    // change visibility, so this only fires on the tray round trip. Minimized
    // is excluded alongside Hidden for the same reason: it's a way *out*, so
    // untucking there would fire on the way down instead of the way back up.
    onVisibilityChanged: {
        if (root.tucked
                && root.visibility !== Window.Hidden
                && root.visibility !== Window.Minimized) {
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
    // block above for why).

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
            keyboard.setIntelligentSpacing(appSettings.savedIntelligentSpacing)
            keyboard.setSnippetDetection(appSettings.savedSnippetDetection)
            // Push the QML mirror of the enable flag down, then read the
            // whole record back: `savedDictationEnabled` is what decides
            // whether the bar reserves room for the mic, so it has to be
            // authoritative on this side, while everything else (the key,
            // the model, the device) lives in dictation.json and is only
            // ever read from there.
            keyboard.setDictationEnabled(appSettings.savedDictationEnabled)
            root.refreshDictation(false)
            keyboard.setAutoCapitalizeAfterPunctuation(appSettings.savedAutoCapitalizeAfterPunctuation)
            keyboard.setAutoSaveOnExit(appSettings.savedAutoSaveOnExit)
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
        // (monitor unplugged, resolution drop) since the last run,
        // via the same desktop-wide clampedWindowPos() the snippets
        // window uses, not Screen.width/height (the primary screen
        // alone): a window saved at x=2400 on a second monitor came
        // back at 1560 on the primary one on every launch, and a
        // monitor to the *left* of the primary has negative
        // coordinates that collapsed to 0 the same way.
        if (appSettings.savedWindowX > -1000000
                && appSettings.savedWindowY > -1000000) {
            var restoredPos = root.clampedWindowPos(appSettings.savedWindowX,
                                                      appSettings.savedWindowY,
                                                      root.width, root.height)
            root.x = restoredPos.x
            root.y = restoredPos.y
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

    // Window transparency (0.3 = very transparent, 1.0 = fully opaque)
    property real windowOpacity: appSettings.savedWindowOpacity

    // Audio feedback
    property bool audioEnabled: appSettings.savedAudioEnabled

    // Auto-space and auto-capitalize after punctuation
    property bool autoSpaceAfterPunctuation: appSettings.savedAutoSpaceAfterPunctuation
    // Skip the punctuation auto-space inside a structured token (an
    // email, a link, a decimal). Only meaningful while the auto-space
    // above is on; see src/text_patterns.py for the shapes.
    property bool intelligentSpacing: appSettings.savedIntelligentSpacing
    // Offer to save a just-typed email / phone / address to Snippets.
    property bool snippetDetection: appSettings.savedSnippetDetection
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

    // Right-click on a char key types its shifted variant (e.g. "1" → "!",
    // "a" → "A") without flipping the sticky shift state.  Purely additive
    // — left-click behaviour is unchanged whether this is on or off.
    property bool rightClickShift: appSettings.savedRightClickShift

    // When on, every key press (left- or right-click) flashes a brief
    // preview bubble above the key showing the character that was typed.
    property bool keyPreviewEnabled: appSettings.savedKeyPreview

    // Hold a letter or digit to repeat it, the way a physical keyboard
    // does.  **On by default**, which reverses the position KeyButton.qml
    // argued for, and the reversal was the user's call rather than a
    // change of mind about the risk.
    //
    // The risk is real and worth keeping in view: a mouse-driven key is
    // held by *not letting go* of the button, and a slow release is
    // ordinary on this keyboard rather than a mistake, so a repeating
    // letter can turn one intended character into several.  What settles
    // it is that the person it protects is the person who asked for the
    // opposite, twice, having met the absence of it as a bug.  A setting
    // they have to go and find is not a neutral default; it is the
    // feature being off for everyone who does not know it exists.
    //
    // Still a setting, so it can be turned off by anyone the original
    // argument does describe. The repeat is kept from starting until at
    // least 800 ms of deliberate hold by KeyButton's `repeatArmFloorMs`,
    // set on every character key below - a hard floor rather than
    // `repeatDelay + warmUpGrace` arithmetic, because `repeatDelay` is
    // itself a user setting clamped down to 300 ms, where the arithmetic
    // alone would let the first repeat land at 600 ms.
    property bool characterRepeat: appSettings.savedCharacterRepeat

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

    // Dictation.  `dictationEnabled` is the user's setting (does the mic
    // exist), `dictationConfigured` is whether pressing it could work (a
    // key is stored and this host has audio), and `dictationState` is the
    // run itself.  Three separate things: the button is present, greyed,
    // or live, and collapsing any two of them loses a state the user can
    // actually be in.
    property bool dictationEnabled: appSettings.savedDictationEnabled
    property bool dictationConfigured: false
    property string dictationState: "idle"
    readonly property bool dictationActive: root.dictationState !== "idle"
    property string dictationTranscript: ""
    property real dictationLevel: 0.0

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

    // The vertical gap between two stacked keyboard rows.  A property
    // rather than a literal on the ColumnLayout because `keyHitMarginV`
    // below has to be exactly half of it, and the same number written in
    // two places is how the two drift apart.
    property real rowSpacing: 2

    // A key's share of the gap around it: half, so two neighbours meet
    // in the middle of it and no strip of the grid is dead.  See the
    // `hitMarginH` comment in KeyButton.qml for why the gap could not
    // just be left to nobody.  The two axes differ because the gaps do:
    // `keySpacing` separates keys within a row, `rowSpacing` separates
    // the rows, and the side panels lay their own rows out on
    // `keySpacing` in both directions.
    //
    // The extra half pixel on the vertical share is not slop, it is the
    // positioner's: a `Row` reports a height ceiled above its tallest
    // key (measured 53 against 52.719 at one width), and that remainder
    // sits below the keys, inside no key, on top of `rowSpacing`.  The
    // true gap between two rows is therefore `rowSpacing` plus up to a
    // pixel that neither row can predict, so each key takes half a pixel
    // more than its half.  Vertical neighbours then overlap by under a
    // pixel instead of leaving a strip under a pixel wide, which is the
    // right way round: an overlap resolves to the lower key, a gap
    // resolves to nothing at all.
    property real keyHitMarginH: keySpacing / 2
    property real keyHitMarginV: rowSpacing / 2 + 0.5
    property real panelHitMarginV: keySpacing / 2 + 0.5

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
    // OS window-snap, DPI change, or panel toggle), clamp it back up.
    onWidthChanged: {
        if (width < minimumWidth) width = minimumWidth
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

    // Perceived luminance of an opaque colour (Rec. 601 weights). The one
    // definition every "is this light or dark" decision in this file
    // shares: inkOn() below and snippetsWindow.lightTheme both used to
    // carry their own copy, alongside KeyButton._onFillColor's, and three
    // independent copies is three chances for the weights to drift apart.
    function luminance(c) {
        return c.r * 0.299 + c.g * 0.587 + c.b * 0.114
    }

    // Ink for text drawn ON TOP of an accent-coloured fill.  Same
    // luminance rule as KeyButton._onFillColor, and it exists for the
    // same reason: several themes ship a pale accent (Blackboard,
    // Spaceship) and Typewriter is a light theme outright, so any fixed
    // colour is unreadable on about half of them.  Pass an *opaque*
    // fill — a semi-transparent one reports its own channels rather than
    // the blend the eye sees, so the luminance would be a lie.
    function inkOn(fill) {
        return root.luminance(fill) > 0.5 ? "#111111" : "#ffffff"
    }

    // Pull the dictation record out of the bridge and push it into the
    // settings panel.
    //
    // One call rather than a property per field, and imperative rather
    // than bound, because the record lives in dictation.json rather than
    // in appSettings: the API key has to be there, and a record split
    // across two stores is a record whose halves drift.  Nothing changes
    // it behind the panel's back, so re-reading on open and after each
    // write is sufficient and a notify signal per field would be noise.
    // `withDevices` is false on the startup call and true when the
    // settings window opens.  Enumerating audio inputs walks the host's
    // drivers (~29 ms with an audio interface attached), and every user
    // would pay that on every launch, including the ones who never switch
    // dictation on.  Opening the settings view is also the only moment a
    // fresh list is worth anything, since a microphone can be plugged in
    // while the keyboard is running.
    function refreshDictation(withDevices) {
        if (!keyboard)
            return
        var d = keyboard.getDictationSettings()
        root.dictationConfigured = d.available && d.enabled && d.hasKey
        if (typeof settingsPanel === "undefined" || !settingsPanel)
            return
        if (withDevices)
            settingsPanel.dictationDevices = keyboard.getDictationDevices()
        settingsPanel.dictationAvailable = d.available
        settingsPanel.dictationHasKey = d.hasKey
        settingsPanel.dictationMaskedKey = d.maskedKey
        settingsPanel.dictationModel = d.model
        settingsPanel.dictationLanguage = d.language
        settingsPanel.dictationDevice = d.device
        settingsPanel.dictationModels = d.models
        settingsPanel.dictationLanguages = d.languages
        settingsPanel.dictationMaxSeconds = d.maxSeconds
        settingsPanel.dictationSilenceSeconds = d.silenceSeconds
        settingsPanel.dictationKeyterms = d.keyterms
        settingsPanel.dictationStreamInserts = d.streamInserts
    }

    // Apply a Ctrl chord the bridge resolved for us to `field`, returning
    // true if it was one.  Shared by the two edit surfaces (the
    // prediction-edit popup and the snippets editor) because they are
    // exactly the parallel blocks this project keeps getting bitten by:
    // the same six names would otherwise be spelled out twice, and the
    // one that got them would be whichever was edited last.
    //
    // Paste is the reason any of this exists.  Every character of a long
    // address is a click here, so pasting one in from somewhere else is
    // the difference between a snippet being worth making and not.
    function applyEditChord(field, name) {
        if (name === "selectall") field.selectAll()
        else if (name === "copy") field.copy()
        else if (name === "cut") field.cut()
        else if (name === "paste") field.paste()
        else if (name === "undo") field.undo()
        else if (name === "redo") field.redo()
        else return false
        return true
    }

    // Bounding box of every monitor, in virtual-desktop coordinates.
    //
    // `Screen` is the screen the *item* is on, and `Screen.virtualX/Y`
    // describe that screen's origin, not the desktop's, so neither one can
    // answer "is this saved position still somewhere a user can see". The
    // union of Qt.application.screens can.
    function desktopBounds() {
        var screens = Qt.application.screens
        if (!screens || screens.length === 0)
            return { left: 0, top: 0, right: Screen.width, bottom: Screen.height }
        var left = screens[0].virtualX
        var top = screens[0].virtualY
        var right = left + screens[0].width
        var bottom = top + screens[0].height
        for (var i = 1; i < screens.length; ++i) {
            var s = screens[i]
            left = Math.min(left, s.virtualX)
            top = Math.min(top, s.virtualY)
            right = Math.max(right, s.virtualX + s.width)
            bottom = Math.max(bottom, s.virtualY + s.height)
        }
        return { left: left, top: top, right: right, bottom: bottom }
    }

    // Clamp a restored window position back onto the desktop, in case the
    // display layout changed since it was saved.
    //
    // Clamping against the primary screen instead is worse than not
    // persisting at all: a window saved at x=2400 on a second monitor came
    // back at 1560 on the primary one every launch, nowhere near the
    // keyboard it belongs to, and a monitor to the *left* of the primary
    // has negative coordinates that collapse to 0 the same way.
    function clampedWindowPos(savedX, savedY, w, h) {
        var b = root.desktopBounds()
        return {
            x: Math.max(b.left, Math.min(savedX, b.right - w)),
            y: Math.max(b.top, Math.min(savedY, b.bottom - h))
        }
    }

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

        // Dictation
        function onDictationStateChanged(state) {
            root.dictationState = state
            if (state === "idle")
                root.dictationLevel = 0.0
        }
        function onDictationTranscriptChanged(text) { root.dictationTranscript = text }
        function onDictationLevelChanged(level) { root.dictationLevel = level }
        function onDictationError(message) {
            // The mic is in the suggestion bar, so a failure has to be
            // reported somewhere the user is already looking rather than
            // in a dialog they have to travel to and dismiss.
            root.dictationTranscript = ""
            dictationErrorToast.flash(message)
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

                // Snippets, the fallback copy.  The button now lives beside
                // the clear-context ring in the suggestion bar, which is a
                // far bigger target than a 28x24 patch of title bar, and this
                // one is hidden whenever that one is showing.
                //
                // It is not dead weight: the suggestion bar collapses to zero
                // height when suggestions are switched off, taking everything
                // in it with it, and Snippets is a feature rather than a
                // suggestion-adjacent control, so losing the only way in
                // because of an unrelated setting is not acceptable.  The
                // clear-context button has always had that hole; this one
                // does not get to inherit it.
                Rectangle {
                    objectName: "symbolsTitleBarButton"
                    visible: !root.suggestionsEnabled
                    width: visible ? 28 : 0
                    height: 24
                    radius: 4
                    color: symbolsBtn.containsMouse ? "#444" : "transparent"

                    ToolTip.visible: symbolsBtn.containsMouse
                    ToolTip.text: qsTr("Symbols & emoji: tap one to type it")
                    ToolTip.delay: 400

                    // Same icon as the bar button, and here for the same
                    // reason its Snippets neighbour is: the suggestion bar
                    // collapses to zero height when suggestions are switched
                    // off, taking every control in it, and an unrelated
                    // setting must not be the only thing standing between the
                    // user and a feature.
                    Comp.StrokeIcon {
                        anchors.centerIn: parent
                        width: 16
                        height: 16
                        paths: ["M2 12 A10 10 0 0 1 22 12 A10 10 0 0 1 2 12",
                                "M8 14s1.5 2 4 2 4-2 4-2",
                                "M9 9L9.01 9", "M15 9L15.01 9"]
                        ink: symbolsWindow.visible ? root.themeAccent : "#999"
                    }

                    MouseArea {
                        id: symbolsBtn
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (symbolsWindow.visible) symbolsWindow.hide()
                            else symbolsWindow.openPicker()
                        }
                    }
                }

                Rectangle {
                    objectName: "snippetsTitleBarButton"
                    visible: !root.suggestionsEnabled
                    width: visible ? 28 : 0
                    height: 24
                    radius: 4
                    color: snippetsBtn.containsMouse ? "#444" : "transparent"

                    ToolTip.visible: snippetsBtn.containsMouse
                    ToolTip.text: qsTr("Snippets: saved text you tap to copy")
                    ToolTip.delay: 400

                    // Feather's "bookmark", the same icon as the bar button,
                    // drawn rather than typeset.  It used to be "☰" in a
                    // Text, which on Windows resolves through Segoe UI Emoji
                    // and ignores the colour it is given, the trap this file
                    // documents twice already.
                    Comp.StrokeIcon {
                        anchors.centerIn: parent
                        width: 16
                        height: 16
                        paths: ["M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"]
                        ink: snippetsWindow.visible ? root.themeAccent : "#999"
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

                // Dictation mirror, for exactly the reason the snippets one
                // above exists: the suggestion bar collapses to zero height
                // when suggestions are switched off, and dictation is a
                // feature rather than a suggestion-adjacent control, so an
                // unrelated setting must not be able to remove the only way
                // into it.  Hidden whenever the bar button is showing, so
                // there is never a choice of two.
                Rectangle {
                    objectName: "dictationTitleBarButton"
                    visible: !root.suggestionsEnabled && root.dictationEnabled
                    width: visible ? 28 : 0
                    height: 24
                    radius: 4
                    color: root.dictationActive ? root.themeAccent
                           : (micTitleBtn.containsMouse ? "#444" : "transparent")
                    opacity: root.dictationConfigured ? 1.0 : 0.45

                    ToolTip.visible: micTitleBtn.containsMouse
                    ToolTip.text: root.dictationConfigured
                                  ? (root.dictationActive ? qsTr("Stop dictating")
                                     : qsTr("Dictate: click, speak, click again"))
                                  : qsTr("Add a Deepgram API key in Settings to dictate")
                    ToolTip.delay: 400

                    Comp.StrokeIcon {
                        anchors.centerIn: parent
                        width: 16
                        height: 16
                        paths: [
                            "M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z",
                            "M19 10v2a7 7 0 0 1-14 0v-2",
                            "M12 19 L12 23",
                            "M8 23 L16 23"
                        ]
                        ink: root.dictationActive ? root.inkOn(root.themeAccent) : "#999"
                    }

                    MouseArea {
                        id: micTitleBtn
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: keyboard.toggleDictation()
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
                // Gap between the two buttons parked at the right end.
                property real predButtonGap: 6
                // How many round buttons are parked at the right end.  A
                // number rather than a hardcoded reserve below, because the
                // reserve went from one button to two to three and each time
                // it was the formula that had to be re-derived by hand.
                property int predButtonCount: 3
                // Width the pills must keep clear so the row can never render
                // underneath the buttons.  Derived from the same numbers the
                // Row is laid out from, because a reserve that is merely
                // *near* the truth puts a pill under a control, which is the
                // bug this property exists to prevent.
                property real clearCtxReserve: root.suggestionsEnabled
                    ? predPillHeight * predButtonCount
                      + predButtonGap * (predButtonCount - 1) + 16 : 0
                // The mirror of clearCtxReserve for the microphone parked at
                // the left end (8 px left margin + the button + 8 px
                // clearance).  Zero unless dictation is actually switched on,
                // which is what keeps the row's geometry byte-identical to
                // before for every user who never turns this on: the pills
                // are centred in `width - micReserve - clearCtxReserve`, so a
                // reserve of 0 collapses the expression to the original one.
                property real micReserve: (root.suggestionsEnabled && root.dictationEnabled)
                    ? predPillHeight + 16 : 0
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

                // Dictation microphone.
                //
                // It sits at the LEFT end rather than joining the pair on the
                // right for the reason the snippets button was put left of the
                // ⟲ ring: the ring is pressed from muscle memory and must not
                // move, and a third control in that Row would push it.  The
                // left end is also the only edge of the bar with nothing on
                // it, so this costs no existing target its position.
                //
                // Its own Row, mirroring predBarButtons, so `micReserve` is
                // the width of a thing that exists rather than a number kept
                // in step by hand.
                Row {
                    id: predBarMicRow
                    anchors.left: parent.left
                    anchors.leftMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    visible: root.suggestionsEnabled && root.dictationEnabled

                    Rectangle {
                        id: micPill
                        objectName: "dictationMicButton"
                        width: predBar.predPillHeight
                        height: predBar.predPillHeight
                        radius: width / 2

                        readonly property bool listening: root.dictationState === "listening"
                        readonly property bool busy: root.dictationState === "connecting"
                                                     || root.dictationState === "finishing"
                        // "Enabled but unusable" is a real state (the toggle is
                        // on and no API key has been entered yet) and it has to
                        // read as unavailable rather than as broken, so the
                        // button greys out and says why in its tooltip instead
                        // of failing on click.
                        readonly property bool ready: root.dictationConfigured
                        // Driven by the busy pulse below, multiplied into
                        // `opacity` rather than animated onto it.  See the
                        // animation's own comment: this property exists so the
                        // pulse has something to own that carries no binding.
                        property real pulse: 1.0

                        color: micPill.listening ? root.themeAccent
                               : (micBtn.containsMouse && micPill.ready
                                  ? Qt.lighter(root.themeKeyColor, 1.3)
                                  : Qt.rgba(0, 0, 0, 0.18))
                        border.color: (micPill.listening || micPill.busy)
                                      ? root.themeAccent
                                      : (micBtn.containsMouse && micPill.ready
                                         ? root.themeAccent : Qt.rgba(1, 1, 1, 0.18))
                        border.width: 1
                        opacity: (micPill.ready ? 1.0 : 0.45) * micPill.pulse

                        ToolTip.visible: micBtn.containsMouse
                        ToolTip.text: micPill.ready
                                      ? (root.dictationActive
                                         ? qsTr("Stop dictating")
                                         : qsTr("Dictate: click, speak, click again"))
                                      : qsTr("Add a Deepgram API key in Settings to dictate")
                        ToolTip.delay: 400

                        // Live microphone level, drawn as a ring outside the
                        // button.  This is the only feedback that the mic is
                        // actually open and hearing something: everything else
                        // on screen looks identical whether audio is arriving
                        // or the device is muted at the OS.  Outside rather
                        // than inside so it cannot fight the icon for contrast.
                        Rectangle {
                            anchors.centerIn: parent
                            width: parent.width + 8 + 10 * root.dictationLevel
                            height: width
                            radius: width / 2
                            color: "transparent"
                            border.color: root.themeAccent
                            border.width: 2
                            opacity: micPill.listening ? 0.15 + 0.5 * root.dictationLevel : 0
                            visible: opacity > 0
                            Behavior on width { NumberAnimation { duration: 90 } }
                            Behavior on opacity { NumberAnimation { duration: 90 } }
                        }

                        // Feather's "mic", MIT, (c) 2013-2023 Cole Bemis.
                        // See THIRD_PARTY_NOTICES.md.  Verbatim from the 24x24
                        // source, with its two <line> elements written as the
                        // equivalent M/L paths, so it can be diffed against
                        // upstream.  Centred in its own box, so no inkOffsetX.
                        Comp.StrokeIcon {
                            anchors.fill: parent
                            paths: [
                                "M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z",
                                "M19 10v2a7 7 0 0 1-14 0v-2",
                                "M12 19 L12 23",
                                "M8 23 L16 23"
                            ]
                            boxFraction: 0.58
                            // On the accent fill the label follows the shared
                            // luminance rule, same as an active keycap: nine
                            // themes ship and several have a pale accent, so a
                            // fixed white is unreadable on about half of them.
                            ink: micPill.listening ? root.inkOn(root.themeAccent)
                                 : (micPill.busy ? root.themeAccent
                                    : (micBtn.containsMouse && micPill.ready
                                       ? root.themeTextColor : "#bbb"))
                        }

                        // Connecting and finishing are both short, and both
                        // have to look like something is happening without
                        // looking like recording, or the user clicks again and
                        // starts a second run.
                        //
                        // Written as a standalone animation driving `pulse`,
                        // NOT as `SequentialAnimation on opacity`.  The `on`
                        // form takes ownership of the property it animates and
                        // does not hand it back when it stops, so it would
                        // destroy the `opacity` binding above on the first run
                        // and leave the button parked at whatever value the
                        // loop was passing through, which for half of each
                        // cycle is nearly transparent.  A separate property
                        // with no binding of its own is a thing the animation
                        // can own safely, and `onRunningChanged` puts it back
                        // to 1.0 rather than trusting where the loop stopped.
                        SequentialAnimation {
                            running: micPill.busy
                            loops: Animation.Infinite
                            onRunningChanged: if (!running) micPill.pulse = 1.0
                            NumberAnimation {
                                target: micPill; property: "pulse"
                                to: 0.45; duration: 420
                            }
                            NumberAnimation {
                                target: micPill; property: "pulse"
                                to: 1.0; duration: 420
                            }
                        }

                        MouseArea {
                            id: micBtn
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: keyboard.toggleDictation()
                        }

                        Behavior on color { ColorAnimation { duration: 100 } }
                    }
                }

                // Live transcript, in the space the pills normally occupy.
                //
                // Only the phrase currently being spoken is shown: a finalised
                // phrase leaves this and is typed into the app, so the text
                // here stays short by construction rather than by truncation,
                // and what is on screen is exactly what has *not* been typed
                // yet.  It elides on the LEFT so the newest words are the ones
                // that survive, which is the opposite of every other elide in
                // this file and is the whole point: while speaking, the tail is
                // what you are checking.
                Item {
                    id: dictationBanner
                    objectName: "dictationTranscript"
                    anchors.verticalCenter: parent.verticalCenter
                    x: predBar.micReserve
                    width: Math.max(0, predBar.width - predBar.micReserve - predBar.clearCtxReserve)
                    height: predBar.predPillHeight
                    visible: root.suggestionsEnabled && root.dictationActive && !root.privacyMode

                    Text {
                        objectName: "dictationTranscriptText"
                        anchors.fill: parent
                        anchors.leftMargin: 10
                        anchors.rightMargin: 10
                        verticalAlignment: Text.AlignVCenter
                        horizontalAlignment: root.dictationTranscript ? Text.AlignLeft
                                                                      : Text.AlignHCenter
                        elide: Text.ElideLeft
                        // Transcript text is whatever the user said, so it is
                        // exactly as untrusted as anything else displayed here:
                        // AutoText would sniff it for markup.
                        textFormat: Text.PlainText
                        font.pixelSize: predBar.predFontSize
                        font.italic: !root.dictationTranscript
                        color: root.dictationTranscript ? root.themeTextColor
                                                        : Qt.rgba(1, 1, 1, 0.55)
                        text: root.dictationTranscript !== ""
                              ? root.dictationTranscript
                              : (root.dictationState === "connecting" ? qsTr("Connecting...")
                                 : (root.dictationState === "finishing" ? qsTr("Finishing...")
                                    : qsTr("Listening...")))
                    }
                }

                Row {
                    id: predRow
                    // Named so tests/test_qml_prediction_bar.py can assert the
                    // row never overlaps clearContextButton.
                    objectName: "predictionRow"
                    anchors.verticalCenter: parent.verticalCenter
                    // Centred in the bar minus the button zones at each end,
                    // floored at the 8 px margin.  Not anchors.centerIn: that
                    // centres on the full bar and pushes the right-hand pill
                    // under the ⟲ button.  `micReserve` is 0 whenever the mic
                    // is not on screen, which collapses this back to exactly
                    // the single-reserve expression it grew from.
                    x: predBar.micReserve + Math.max(
                        8, (predBar.width - predBar.micReserve
                            - predBar.clearCtxReserve - width) / 2)
                    spacing: 8
                    // A live transcript takes the whole bar: the pills would
                    // be about a prefix the user is no longer typing, and the
                    // two cannot share the row.
                    visible: root.suggestionsEnabled && !root.privacyMode && !root.dictationActive

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
                    // `reserve` is the total width both ends own, not just the
                    // ⟲ end: the fitter has no notion of which side a reserve
                    // sits on, it only needs to know how much of the bar the
                    // pills may not have.  Passing the sum keeps the signature
                    // and keeps the no-elide guarantee true once a mic button
                    // is taking space off the left.
                    property var fit: predRow.computeFit(
                        root.predictions, root.width, predBar.predFontSize,
                        predBar.predHorizontalPad, predBar.predMinWidth,
                        predBar.predPillHeight, predRow.spacing,
                        predBar.clearCtxReserve + predBar.micReserve,
                        predBar.predTextInset)

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
                        model: (root.suggestionsEnabled && !root.privacyMode
                                && !root.dictationActive) ? predRow.fit.words : []
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
                                        // Every action in this menu is a
                                        // word-model operation, and a
                                        // structured token is not a word:
                                        // "Show more" would push a phone
                                        // number into the vocabulary and
                                        // onto the dashboard, while "Show
                                        // less" and "Remove" write tables
                                        // the token store never reads.
                                        // Forgetting one is the dashboard's
                                        // Saved Numbers & Addresses.
                                        if (keyboard && keyboard.isTokenPill(modelData))
                                            return
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
                // Both bar buttons live in one Row anchored to the right, so
                // the reserve `computeFit` subtracts is the width of a thing
                // that exists rather than a number kept in step by hand.
                // Snippets sits to the LEFT of the clear-context ring, which
                // leaves the ring exactly where it has always been: it is
                // pressed by muscle memory, and the new control should be the
                // one that has to be found.
                Row {
                    id: predBarButtons
                    anchors.right: parent.right
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: predBar.predButtonGap
                    visible: root.suggestionsEnabled

                Rectangle {
                    id: symbolsPill
                    objectName: "symbolsBarButton"
                    width: predBar.predPillHeight
                    height: predBar.predPillHeight
                    radius: width / 2
                    color: symbolsBarBtn.containsMouse ? Qt.lighter(root.themeKeyColor, 1.3)
                                                       : Qt.rgba(0, 0, 0, 0.18)
                    border.color: symbolsWindow.visible || symbolsBarBtn.containsMouse
                                  ? root.themeAccent : Qt.rgba(1, 1, 1, 0.18)
                    border.width: 1

                    ToolTip.visible: symbolsBarBtn.containsMouse
                    ToolTip.text: qsTr("Symbols & emoji: tap one to type it")
                    ToolTip.delay: 400

                    // Feather's "smile", MIT, (c) 2013-2023 Cole Bemis.
                    // See THIRD_PARTY_NOTICES.md.  One deviation, and it is
                    // forced: StrokeIcon takes path data only, so the
                    // source's <circle> is written as the equivalent pair of
                    // arcs.  The eyes are upstream's zero-length lines,
                    // verbatim, which rely on the SVG round-cap rule and
                    // were measured rendering correctly through Canvas
                    // rather than assumed to.  Drawn rather than typeset for
                    // the usual reason: a smiley in a Text resolves through
                    // Segoe UI Emoji on Windows and comes out as a colour
                    // glyph that ignores the ink it is given.
                    Comp.StrokeIcon {
                        anchors.fill: parent
                        paths: ["M2 12 A10 10 0 0 1 22 12 A10 10 0 0 1 2 12",
                                "M8 14s1.5 2 4 2 4-2 4-2",
                                "M9 9L9.01 9", "M15 9L15.01 9"]
                        boxFraction: 0.62
                        ink: symbolsWindow.visible ? root.themeAccent
                             : (symbolsBarBtn.containsMouse ? root.themeTextColor : "#bbb")
                    }

                    MouseArea {
                        id: symbolsBarBtn
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (symbolsWindow.visible) symbolsWindow.hide()
                            else symbolsWindow.openPicker()
                        }
                    }

                    Behavior on color { ColorAnimation { duration: 100 } }
                }

                Rectangle {
                    id: snippetsPill
                    objectName: "snippetsBarButton"
                    width: predBar.predPillHeight
                    height: predBar.predPillHeight
                    radius: width / 2
                    color: snippetsBarBtn.containsMouse ? Qt.lighter(root.themeKeyColor, 1.3)
                                                        : Qt.rgba(0, 0, 0, 0.18)
                    border.color: snippetsWindow.visible || snippetsBarBtn.containsMouse
                                  ? root.themeAccent : Qt.rgba(1, 1, 1, 0.18)
                    border.width: 1

                    ToolTip.visible: snippetsBarBtn.containsMouse
                    ToolTip.text: qsTr("Snippets: saved text you tap to copy")
                    ToolTip.delay: 400

                    // Feather's "bookmark", MIT, (c) 2013-2023 Cole Bemis.
                    // See THIRD_PARTY_NOTICES.md.  Verbatim from the 24x24
                    // source so it can be diffed against upstream.  Its ink
                    // is centred in its own box, so no inkOffsetX.
                    Comp.StrokeIcon {
                        anchors.fill: parent
                        paths: ["M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"]
                        boxFraction: 0.62
                        ink: snippetsWindow.visible ? root.themeAccent
                             : (snippetsBarBtn.containsMouse ? root.themeTextColor : "#bbb")
                    }

                    MouseArea {
                        id: snippetsBarBtn
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (snippetsWindow.visible) snippetsWindow.hide()
                            else snippetsWindow.openList()
                        }
                    }

                    Behavior on color { ColorAnimation { duration: 100 } }
                }

                Rectangle {
                    id: clearCtxPill
                    objectName: "clearContextButton"
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

                    // Feather's "rotate-ccw", drawn from its published
                    // path data rather than typeset as a glyph.
                    //
                    // The glyph it replaces was "⟲" in a Text with
                    // anchors.centerIn, which centres the text *item* while
                    // the ink inside it sits wherever the font puts it: the
                    // ring the eye reads sat down-and-right of the circle it
                    // lives on, with the glyph's tail hanging out to the
                    // left.  Same lesson as the padlock badge that came off
                    // the keycaps -- any glyph small enough to be an icon is
                    // at the mercy of the host font.
                    //
                    // Drawn on a Canvas via ctx.path, which takes SVG path
                    // data directly.  That is deliberate: QtQuick.Shapes or
                    // QtSvg would render it just as well and would each add
                    // a QML module the frozen build has to carry, where a
                    // missing module does not degrade -- it fails Main.qml
                    // and ships as a blank keyboard.
                    //
                    // Feather rotate-ccw, MIT, (c) 2013-2023 Cole Bemis.
                    // See THIRD_PARTY_NOTICES.md.  Verbatim from the 24x24
                    // source, with the polyline written as an equivalent
                    // path; keep them that way so the icon can be diffed
                    // against upstream.
                    //
                    // Drawn through the shared StrokeIcon rather than a
                    // Canvas of its own.  That component was written for the
                    // snippets window as a generalisation of exactly this
                    // routine and then this copy was left behind, so the
                    // invalidation bug StrokeIcon's own comment records
                    // could have happened twice.
                    Comp.StrokeIcon {
                        anchors.fill: parent
                        paths: ["M1 4 L1 10 L7 10",
                                "M3.51 15a9 9 0 1 0 2.13-9.36L1 10"]
                        boxFraction: 0.70
                        // The composition is not centred in its own viewBox:
                        // the corner arrow sits outside the ring at the top
                        // left, which puts the ink's centre one unit to the
                        // left of the box's.  Measured, not guessed.
                        inkOffsetX: 1
                        ink: clearCtxBtn.containsMouse ? root.themeTextColor : "#bbb"
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
                    // Half of this is what each row's keys reach into,
                    // so it and `keyHitMarginV` must stay in step.
                    spacing: root.rowSpacing

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
                        hitMarginH: root.keyHitMarginH
                        hitMarginV: root.keyHitMarginV
                        keyColor: Qt.darker(root.themeKeyColor, 1.3)
                        accentKeyColor: root.accentKeyColor
                        keyPressedColor: root.themeKeyPressed
                        keyTextColor: root.themeTextColor
                        accentColor: root.themeAccent
                        borderColor: root.themeBorder
                        shiftOn: root.shiftOn
                        rightClickShift: root.rightClickShift
                        keyPreviewEnabled: root.keyPreviewEnabled
                        characterRepeat: root.characterRepeat
                        repeatDelay: root.repeatDelay
                        repeatInterval: root.repeatInterval
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
                        hitMarginH: root.keyHitMarginH
                        hitMarginV: root.keyHitMarginV
                        // Centred rather than filling the grid width, which
                        // leaves visible space at both ends. That is the
                        // chosen shape, not an oversight: see the geometry
                        // note in FunctionRow.qml before changing it.
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
                                hitMarginH: root.keyHitMarginH
                                hitMarginV: root.keyHitMarginV
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
                                        // Not a modifier: the symbol layer's
                                        // entry key sits on the always-visible
                                        // space row, so lighting it is the only
                                        // thing on screen that says which page
                                        // the letters have been swapped for.
                                        case "symLayer": return root.activeLayer === "sym"
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

                                // Repeat-worthy specials always; character
                                // keys only when the user asked for it (see
                                // `characterRepeat`).  The special set is the
                                // same one the Navigation panel repeats,
                                // because the compact layouts put Del and the
                                // arrows in the main grid and holding them has
                                // to behave the same in both places.
                                enableRepeat: kd.type === "char"
                                              ? root.characterRepeat
                                              : (kd.type === "special"
                                                 && root.repeatableActions.indexOf(kd.action) !== -1)
                                repeatDelay: root.repeatDelay
                                repeatInterval: root.repeatInterval
                                // Character keys get the 800 ms hard floor
                                // (see KeyButton's `repeatArmFloorMs`) so
                                // the "won't fire on a slow release" promise
                                // holds even when the user has turned
                                // `repeatDelay` down for a snappier
                                // Backspace. Del/arrows share that lowered
                                // delay on purpose, so they stay at 0.
                                repeatArmFloorMs: kd.type === "char" ? 800 : 0

                                onKeyPressed: {
                                    if (kd.type === "char") {
                                        var ch = root.shiftOn && kd.shifted ? kd.shifted : kd.key
                                        // `literal` keys type exactly the glyph
                                        // on the cap, whatever Shift and Caps
                                        // Lock are doing. The symbol layer is
                                        // built from them, and it is not a
                                        // stylistic choice: pressKey applies
                                        // case normalisation, and Python's
                                        // upper() is not the identity on every
                                        // non-ASCII character. Caps Lock is
                                        // deliberately left alone by a layer
                                        // switch, so without this, Caps + the
                                        // micro sign typed a Greek capital Mu.
                                        if (kd.literal)
                                            keyboard.pressKeyLiteral(kd.key, keyBtn.pressDx, keyBtn.pressDy)
                                        else
                                            keyboard.pressKey(ch, keyBtn.pressDx, keyBtn.pressDy)
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
                                        //
                                        // A layer key whose target is already
                                        // showing goes back to base instead of
                                        // re-selecting the layer it is on. The
                                        // full-size layouts reach their symbol
                                        // page from the space row, which has no
                                        // `layer` field and therefore renders on
                                        // every layer, so the same key has to be
                                        // both the way in and the way out. Every
                                        // other layer key targets something it
                                        // is not on, so this branch is dead for
                                        // them and their behaviour is unchanged.
                                        keyboard.releaseShift()
                                        var want = kd.target || "base"
                                        root.activeLayer = (want === root.activeLayer)
                                                           ? "base" : want
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
                                    keyboard.pressKeyLiteral(rch, keyBtn.pressDx, keyBtn.pressDy)
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
                // Vertically off `keySpacing`, not `rowSpacing`: this
                // panel lays its own rows out on it.
                hitMarginH: root.keyHitMarginH
                hitMarginV: root.panelHitMarginV
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
                // Vertically off `keySpacing`, not `rowSpacing`: this
                // panel lays its own rows out on it.
                hitMarginH: root.keyHitMarginH
                hitMarginV: root.panelHitMarginV
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
                characterRepeat: root.characterRepeat
                repeatDelay: root.repeatDelay
                repeatInterval: root.repeatInterval
            }
            }
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
                    // The Ctrl chords the bridge resolves for us (see
                    // _EDIT_CHORDS): they act on this field and are never
                    // passed to the app behind the keyboard.
                    if (root.applyEditChord(predEditField, name)) return
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

        // Snippets: the user's saved quick text (name, email, phone,
        // address, canned phrases). See qml/components/SnippetsWindow.qml
        // for the window itself -- why it is a separate top-level Window
        // rather than a Popup, the three-view split, and why a tap copies
        // to the clipboard rather than types.
        Comp.SnippetsWindow {
            id: snippetsWindow
            applyEditChord: root.applyEditChord
            clampedWindowPos: root.clampedWindowPos
            inkOn: root.inkOn
            luminance: root.luminance
            shiftOn: root.shiftOn
            themeAccent: root.themeAccent
            themeBackground: root.themeBackground
            themeBorder: root.themeBorder
            themeKeyColor: root.themeKeyColor
            themeKeyPressed: root.themeKeyPressed
            themeTextColor: root.themeTextColor
            keyboardWidth: root.width
            keyboardX: root.x
            keyboardY: root.y
            settings: appSettings

            // The toasts have to stay on the keyboard window (see
            // SnippetsWindow.qml): this window hides itself on the same
            // tap that would trigger one, and would take it down too.
            onCopied: function(label) { snippetCopiedToast.flash(label) }
            onProblem: function(message) { snippetProblemToast.flash(message) }
            onSaved: function() { editSavedToast.flash() }
        }

        // Symbols & Emoji: the long tail behind the keyboard's own symbol
        // layer. See qml/components/SymbolsWindow.qml for the window
        // itself and the full rationale -- it deliberately shares its
        // shell with the Snippets window above.
        Comp.SymbolsWindow {
            id: symbolsWindow
            clampedWindowPos: root.clampedWindowPos
            themeAccent: root.themeAccent
            themeBackground: root.themeBackground
            themeKeyColor: root.themeKeyColor
            themeKeyPressed: root.themeKeyPressed
            themeTextColor: root.themeTextColor
            keyboardWidth: root.width
            keyboardX: root.x
            keyboardY: root.y
            settings: appSettings

            onProblem: function(message) { snippetProblemToast.flash(message) }
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

        // "Copied" confirmation toast, fired when a snippet tile is tapped.
        //
        // It lives on the *keyboard* window rather than in the snippets
        // window, because the snippets window hides itself on the same tap:
        // a toast parented there would be taken down with it and never
        // seen. It is also the only feedback a copy has. An insert types
        // visible characters into the target app, but a clipboard write is
        // invisible by nature, and the failure the user most needs to catch
        // (nothing happened) looks exactly like the success.
        //
        // Names the snippet, since with colour tags there may be three
        // similar ones and the confirmation is worth nothing if it does not
        // say which was taken.
        Popup {
            id: snippetCopiedToast
            objectName: "snippetCopiedToast"
            parent: Overlay.overlay
            x: (root.width - width) / 2
            y: 36
            width: Math.min(root.width - 24, copiedRow.implicitWidth + 28)
            height: 32
            modal: false
            dim: false
            // Every OSK key click is a press-outside, so any auto-close
            // policy would slam this shut on the next keystroke (the trap
            // the prediction-edit popup documents).
            closePolicy: Popup.NoAutoClose

            property string snippetLabel: ""

            background: Rectangle {
                color: Qt.rgba(root.themeAccent.r, root.themeAccent.g, root.themeAccent.b, 0.18)
                border.color: root.themeAccent
                border.width: 1
                radius: 8
            }

            contentItem: Row {
                id: copiedRow
                spacing: 7
                Comp.StrokeIcon {
                    id: copiedIcon
                    width: 14; height: 14
                    anchors.verticalCenter: parent.verticalCenter
                    ink: root.themeAccent
                    strokeWidth: 2.4
                    paths: ["M4 12 L9 17 L20 6"]
                }
                Text {
                    text: snippetCopiedToast.snippetLabel.length
                          ? qsTr("Copied %1").arg(snippetCopiedToast.snippetLabel)
                          : qsTr("Copied")
                    // The label is user data and round-trips through the
                    // Data Backup import.
                    textFormat: Text.PlainText
                    color: root.themeTextColor
                    font.pixelSize: 13
                    // A Text only elides when it has a width, and inside a
                    // Row it is laid out at its natural one -- so a
                    // 40-character label (MAX_LABEL_LEN) rendered straight
                    // past the toast's own background instead of eliding.
                    // Derived from root.width rather than from the Row, so
                    // the toast's implicit width does not depend on the
                    // width it is handing back.
                    width: Math.min(implicitWidth,
                                    root.width - 24 - 28 - copiedIcon.width - copiedRow.spacing)
                    elide: Text.ElideRight
                    anchors.verticalCenter: parent.verticalCenter
                }
            }

            Timer {
                id: snippetCopiedToastTimer
                // Longer than the 1.4 s confirmations: this one is read for
                // *which* snippet, not just that something happened.
                interval: 2000
                onTriggered: snippetCopiedToast.close()
            }

            function flash(label) {
                snippetCopiedToast.snippetLabel = label ? label : ""
                open()
                snippetCopiedToastTimer.restart()
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

        // "Save this to Snippets?" offer, raised by the bridge when a
        // just-typed email / phone / address looks worth keeping.
        //
        // Unlike its two sibling toasts above this one is *interactive*,
        // which changes three things:
        //   * closePolicy MUST stay NoAutoClose. Every OSK key click is a
        //     press-outside, so CloseOnPressOutside would slam it shut on
        //     the first keystroke after it appears — the same trap the
        //     prediction-edit popup documents.
        //   * the dwell is long (8 s, not 1.4 s) because the user has to
        //     read it, decide, and land a click with an imprecise pointer.
        //     Any tap on either button stops the timer.
        //   * it must never steal a keystroke, so it is not modal and
        //     installs no overlay.
        // The value is typed content, so the Text renders it as
        // PlainText — see the AutoText gotcha in CLAUDE.md.
        Popup {
            id: snippetOfferToast
            objectName: "snippetOfferToast"
            parent: Overlay.overlay

            property string offerKind: ""
            property string offerLabel: ""
            property string offerValue: ""
            // Both buttons stay inert for a moment after the banner opens.
            // The offer is raised by the same keystroke that repopulates
            // the suggestion pills, so without this a click already on its
            // way to a pill would be caught by a Save button that did not
            // exist when the user started the movement -- and "Save" here
            // means writing their email or address to disk. Deliberately
            // longer than a UI-polish delay: this is a mouse-driven
            // keyboard for imprecise motor input, so a click can land well
            // after the intent formed.
            property bool armed: false

            x: (root.width - width) / 2
            // Parked at the bottom, clear of the suggestion row. The pill
            // row (y 52 to ~95, see outerLayout.anchors.topMargin) is the
            // one place on screen that changes at the instant this appears,
            // which makes it exactly the wrong place to put a button that
            // persists personal data. The bottom row is static by
            // comparison, and a mis-click that lands on a key instead of
            // here merely types a character.
            y: root.height - height - 10
            width: Math.min(root.width - 24, Math.max(280, offerRow.implicitWidth + 24))
            height: offerRow.implicitHeight + 16
            modal: false
            dim: false
            closePolicy: Popup.NoAutoClose

            background: Rectangle {
                color: "#22282e"
                border.color: root.themeAccent
                border.width: 1
                radius: 8
            }

            contentItem: Row {
                id: offerRow
                spacing: 10

                Column {
                    spacing: 1
                    anchors.verticalCenter: parent.verticalCenter
                    Text {
                        text: qsTr("Save this %1 to Snippets?").arg(
                                  snippetOfferToast.offerLabel.toLowerCase())
                        color: "#dfe6ec"
                        font.pixelSize: 12
                        textFormat: Text.PlainText
                    }
                    Text {
                        // The detected value, so the user can see exactly
                        // what would be stored before agreeing to store it.
                        text: snippetOfferToast.offerValue
                        color: root.themeAccent
                        font.pixelSize: 13
                        font.weight: Font.Bold
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        width: Math.min(implicitWidth, root.width - 190)
                    }
                }

                // Deliberately large hit targets: this is a mouse-driven
                // keyboard for imprecise motor input, and a 20 px "x" in a
                // corner would be unusable.
                Rectangle {
                    width: 64
                    height: 34
                    radius: 6
                    anchors.verticalCenter: parent.verticalCenter
                    // Kept opaque in both states so inkOn() can read a
                    // luminance that matches what is actually on screen.
                    color: saveOfferMouse.containsMouse
                           ? Qt.lighter(root.themeAccent, 1.15)
                           : root.themeAccent
                    border.color: root.themeAccent
                    border.width: 1
                    Text {
                        anchors.centerIn: parent
                        text: qsTr("Save")
                        color: root.inkOn(parent.color)
                        font.pixelSize: 13
                        font.weight: Font.Bold
                        textFormat: Text.PlainText
                    }
                    MouseArea {
                        id: saveOfferMouse
                        anchors.fill: parent
                        enabled: snippetOfferToast.armed
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            // Only confirm what actually got written. The
                            // store refuses past its 50-snippet cap, and
                            // flashing "Saved" regardless left the user
                            // believing their email was stored when it
                            // was not.
                            var saved = keyboard.acceptSnippetOffer()
                            snippetOfferToast.dismiss()
                            if (saved) {
                                editSavedToast.flash()
                            } else {
                                snippetsFullToast.flash()
                            }
                        }
                    }
                }

                Rectangle {
                    width: 34
                    height: 34
                    radius: 6
                    anchors.verticalCenter: parent.verticalCenter
                    color: dismissOfferMouse.containsMouse ? "#3a4048" : "transparent"
                    border.color: "#55606a"
                    border.width: 1
                    Text {
                        anchors.centerIn: parent
                        text: "✕"
                        color: "#aab4be"
                        font.pixelSize: 13
                        textFormat: Text.PlainText
                    }
                    MouseArea {
                        id: dismissOfferMouse
                        anchors.fill: parent
                        enabled: snippetOfferToast.armed
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            keyboard.dismissSnippetOffer()
                            snippetOfferToast.dismiss()
                        }
                    }
                }
            }

            Timer {
                id: snippetOfferArmTimer
                interval: 400
                onTriggered: snippetOfferToast.armed = true
            }

            Timer {
                id: snippetOfferTimer
                interval: 8000
                // Ignoring an offer is a decision too: tell the bridge, or
                // the value stays "pending" there and the next word
                // boundary can't raise a different one.
                onTriggered: {
                    keyboard.dismissSnippetOffer()
                    snippetOfferToast.close()
                }
            }

            function show(kind, label, value) {
                offerKind = kind
                offerLabel = label
                offerValue = value
                armed = false
                open()
                snippetOfferArmTimer.restart()
                snippetOfferTimer.restart()
            }

            function dismiss() {
                snippetOfferArmTimer.stop()
                snippetOfferTimer.stop()
                armed = false
                close()
            }
        }

        // Shown when Save is tapped but the snippet list is already at its
        // 50-entry cap, so the user finds out instead of walking away
        // believing the value was stored.
        Popup {
            id: snippetsFullToast
            parent: Overlay.overlay
            x: (root.width - width) / 2
            y: 36
            width: 260
            height: 32
            modal: false
            dim: false
            closePolicy: Popup.NoAutoClose

            background: Rectangle {
                color: "#3e2e1e"
                border.color: "#e0a85a"
                border.width: 1
                radius: 8
            }

            contentItem: Text {
                text: qsTr("Snippets are full — delete one first")
                color: "#f0d0a0"
                font.pixelSize: 12
                textFormat: Text.PlainText
                verticalAlignment: Text.AlignVCenter
            }

            Timer {
                id: snippetsFullToastTimer
                interval: 2600
                onTriggered: snippetsFullToast.close()
            }

            function flash() {
                open()
                snippetsFullToastTimer.restart()
            }
        }

        // Shared "that did not work" toast for the snippets window.
        //
        // Both the things a tile tap can do are invisible when they fail:
        // a clipboard write leaves nothing on screen, and a save that the
        // store refused looks exactly like one it took. Saying nothing is
        // indistinguishable from a tap that did not register, so the user
        // taps again, which on an OSK driven by an imprecise pointer is
        // the failure mode worth spending a toast on.
        Popup {
            id: snippetProblemToast
            objectName: "snippetProblemToast"
            parent: Overlay.overlay
            x: (root.width - width) / 2
            y: 36
            width: Math.min(root.width - 24, problemText.implicitWidth + 28)
            height: 32
            modal: false
            dim: false
            // Every OSK key click is a press-outside.
            closePolicy: Popup.NoAutoClose

            property string message: ""

            background: Rectangle {
                color: "#3e2e1e"
                border.color: "#e0a85a"
                border.width: 1
                radius: 8
            }

            contentItem: Text {
                id: problemText
                text: snippetProblemToast.message
                color: "#f0d0a0"
                font.pixelSize: 12
                textFormat: Text.PlainText
                elide: Text.ElideRight
                verticalAlignment: Text.AlignVCenter
            }

            Timer {
                id: snippetProblemToastTimer
                interval: 2600
                onTriggered: snippetProblemToast.close()
            }

            function flash(msg) {
                snippetProblemToast.message = msg ? msg : qsTr("That did not work")
                open()
                snippetProblemToastTimer.restart()
            }
        }

        // Dictation failures.  Same argument as snippetProblemToast: the
        // mic button is a control whose failure is silent (no transcript
        // ever arrives, and the button returns to idle looking exactly as
        // it did before the click), so without this the user clicks it
        // again.  It dwells longer than the confirmation toasts because
        // every message here names a next step the user has to read.
        Popup {
            id: dictationErrorToast
            objectName: "dictationErrorToast"
            parent: Overlay.overlay
            x: (root.width - width) / 2
            y: 36
            width: Math.min(root.width - 24, dictationErrorText.implicitWidth + 28)
            height: 32
            modal: false
            dim: false
            // Every OSK key click is a press-outside.
            closePolicy: Popup.NoAutoClose

            property string message: ""

            background: Rectangle {
                color: "#3e2e1e"
                border.color: "#e0a85a"
                border.width: 1
                radius: 8
            }

            contentItem: Text {
                id: dictationErrorText
                text: dictationErrorToast.message
                color: "#f0d0a0"
                font.pixelSize: 12
                textFormat: Text.PlainText
                elide: Text.ElideRight
                verticalAlignment: Text.AlignVCenter
            }

            Timer {
                id: dictationErrorToastTimer
                interval: 4000
                onTriggered: dictationErrorToast.close()
            }

            function flash(msg) {
                dictationErrorToast.message = msg ? msg : qsTr("Dictation stopped")
                open()
                dictationErrorToastTimer.restart()
            }
        }

        Connections {
            target: keyboard
            function onSnippetOffered(kind, label, value) {
                if (!root.snippetDetection) return
                snippetOfferToast.show(kind, label, value)
            }
            // The bridge dropped the offer underneath us (app switch,
            // context reset, privacy mode). Close the toast rather than
            // leave a Save button on screen that would do nothing.
            function onSnippetOfferWithdrawn() {
                snippetOfferToast.dismiss()
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
                root.refreshDictation(true)
            }
        }

        // Sync close button with main showSettings flag
        onClosing: root.showSettings = false

        color: "#1e1e1e"

        Comp.UnifiedSettingsPanel {
            id: settingsPanel
            anchors.fill: parent

            // Dictation.  Everything except `dictationEnabled` is pulled
            // from the bridge on open rather than mirrored into
            // appSettings, because it lives in dictation.json (which is
            // where the API key has to be, and splitting the record across
            // two stores is how the two drift).  `refreshDictation()` is
            // called from the popup's onVisibleChanged below.
            dictationEnabled: root.dictationEnabled

            showFunctionRow: root.showFunctionRow
            showNavigation: root.showNavigation
            showNumpad: root.showNumpad
            currentTheme: root.currentTheme
            themeData: root.themeData
            windowOpacity: root.windowOpacity
            currentLayout: root.currentLayout
            compactView: root.compactView
            characterRepeat: root.characterRepeat
            audioEnabled: root.audioEnabled
            suggestionsEnabled: root.suggestionsEnabled
            predictionCount: keyboard ? keyboard.predictionCount : 8
            autoSpaceAfterPunctuation: root.autoSpaceAfterPunctuation
            intelligentSpacing: root.intelligentSpacing
            snippetDetection: root.snippetDetection
            autoCapitalizeAfterPunctuation: root.autoCapitalizeAfterPunctuation
            autoSaveOnExit: root.autoSaveOnExit
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
                } else if (setting === "intelligentSpacing") {
                    root.intelligentSpacing = value
                    appSettings.savedIntelligentSpacing = value
                    if (keyboard) keyboard.setIntelligentSpacing(value)
                } else if (setting === "dictationEnabled") {
                    root.dictationEnabled = value
                    appSettings.savedDictationEnabled = value
                    if (keyboard) {
                        keyboard.setDictationEnabled(value)
                        root.refreshDictation(true)
                    }
                } else if (setting === "dictationApiKey") {
                    // Honour the bool: the store can refuse the write, and a
                    // green "Saved" over a key that never landed sends the
                    // user away believing dictation is set up.  Same failure
                    // acceptSnippetOffer and setSnippet were each given a
                    // bool return for.
                    if (keyboard) {
                        if (keyboard.setDictationApiKey(value)) {
                            editSavedToast.flash()
                            root.refreshDictation(true)
                        } else {
                            dictationErrorToast.flash(qsTr("Could not save that key"))
                        }
                    }
                } else if (setting === "dictationApiKeyCleared") {
                    if (keyboard) {
                        keyboard.clearDictationApiKey()
                        root.refreshDictation(true)
                    }
                } else if (setting === "dictationModel") {
                    if (keyboard) { keyboard.setDictationModel(value); root.refreshDictation(true) }
                } else if (setting === "dictationLanguage") {
                    if (keyboard) { keyboard.setDictationLanguage(value); root.refreshDictation(true) }
                } else if (setting === "dictationDevice") {
                    if (keyboard) { keyboard.setDictationDevice(value); root.refreshDictation(true) }
                } else if (setting === "dictationMaxSeconds") {
                    if (keyboard) { keyboard.setDictationMaxSeconds(value); root.refreshDictation(true) }
                } else if (setting === "dictationSilenceSeconds") {
                    if (keyboard) {
                        keyboard.setDictationSilenceSeconds(value)
                        root.refreshDictation(true)
                    }
                } else if (setting === "dictationStreamInserts") {
                    if (keyboard) {
                        keyboard.setDictationStreamInserts(value)
                        root.refreshDictation(true)
                    }
                } else if (setting === "dictationKeyterms") {
                    if (keyboard) {
                        keyboard.setDictationKeyterms(value)
                        root.refreshDictation(true)
                        editSavedToast.flash()
                    }
                } else if (setting === "snippetDetection") {
                    root.snippetDetection = value
                    appSettings.savedSnippetDetection = value
                    if (keyboard) keyboard.setSnippetDetection(value)
                } else if (setting === "autoSaveOnExit") {
                    root.autoSaveOnExit = value
                    appSettings.savedAutoSaveOnExit = value
                    if (keyboard) keyboard.setAutoSaveOnExit(value)
                } else if (setting === "rightClickShift") {
                    root.rightClickShift = value
                    appSettings.savedRightClickShift = value
                } else if (setting === "keyPreview") {
                    root.keyPreviewEnabled = value
                    appSettings.savedKeyPreview = value
                } else if (setting === "characterRepeat") {
                    root.characterRepeat = value
                    appSettings.savedCharacterRepeat = value
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
