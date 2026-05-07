import QtQuick 2.15
import QtQuick.Layouts 1.15
import QtQuick.Controls 2.15
import QtQuick.Window 2.15
import Qt.labs.platform 1.1 as Platform

Item {
    id: unifiedSettings

    // Layout properties
    property bool showFunctionRow: false
    property bool showNavigation: false
    property bool showNumpad: false
    property string currentTheme: "dark"
    property var themeData: ({})
    property real windowOpacity: 1.0
    property string currentLayout: "qwerty"
    property bool audioEnabled: false

    // Suggestions
    property bool suggestionsEnabled: true
    property int predictionCount: 8
    property bool autoSpaceAfterPunctuation: true
    property bool autoCapitalizeAfterPunctuation: false
    // Merge strategy — see docs/HYBRID_MERGING.md.  "rank" is the
    // default and historical behaviour; the others are alternatives.
    property string mergeStrategy: "rank"

    // Data
    property bool autoSaveOnExit: true

    // Input methods
    property bool swipeEnabled: false
    property bool rightClickShift: true
    // Hold-to-repeat timing (ms).  Defaults must match KeyButton.qml.
    property int repeatDelay: 500
    property int repeatInterval: 120
    // Compatibility mode — see KeyboardBridge.setCompatMode.  Covers
    // remote-desktop sessions and IDEs with always-on keystroke
    // interception (VS Code + forks, JetBrains family).
    property bool compatMode: false
    property bool compatAutoDetect: true

    // Debug
    property bool debugMode: false

    // Auto-update — see src/updater.py
    property bool autoCheckUpdates: true
    // "Check now" feedback — set to "checking" / "uptodate" / "" by parent
    property string updateStatus: ""
    // Running app version, surfaced in the Updates section so a user
    // (or you, debugging) can confirm what's actually installed.
    property string appVersion: ""

    signal settingChanged(string setting, var value)
    signal checkForUpdatesNowRequested()
    signal closeRequested()
    signal showHelpRequested()
    signal showVisualizationRequested()

    Rectangle {
        anchors.fill: parent
        color: "#1e1e1e"
        radius: 10
        border.color: "#444"
        border.width: 1
        clip: true

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 14
            spacing: 0

            // -- Header / drag handle --
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 36
                Layout.bottomMargin: 6

                MouseArea {
                    anchors.fill: parent
                    anchors.rightMargin: 34
                    cursorShape: Qt.SizeAllCursor
                    onPressed: {
                        var win = Window.window
                        if (win && win.startSystemMove) win.startSystemMove()
                    }
                }

                RowLayout {
                    anchors.fill: parent

                    Text {
                        text: "\u2699 Settings"
                        color: "#fff"
                        font.pixelSize: 16
                        font.bold: true
                    }

                    Item { Layout.fillWidth: true }

                    Rectangle {
                        width: 26
                        height: 26
                        radius: 5
                        color: closeArea.containsMouse ? "#5a2020" : "transparent"

                        Text {
                            anchors.centerIn: parent
                            text: "\u2715"
                            color: closeArea.containsMouse ? "#ff6666" : "#888"
                            font.pixelSize: 14
                        }

                        MouseArea {
                            id: closeArea
                            anchors.fill: parent
                            hoverEnabled: true
                            onClicked: unifiedSettings.closeRequested()
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                height: 1
                color: "#3a3a3a"
                Layout.bottomMargin: 4
            }

            // -- Scrollable content --
            Flickable {
                id: flickArea
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentWidth: width
                contentHeight: contentColumn.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds

                ScrollBar.vertical: ScrollBar {
                    policy: ScrollBar.AsNeeded
                    width: 8

                    contentItem: Rectangle {
                        radius: 4
                        color: parent.pressed ? "#aaa" : (parent.hovered ? "#888" : "#666")
                        Behavior on color { ColorAnimation { duration: 80 } }
                    }

                    background: Rectangle {
                        color: "#2a2a2a"
                        radius: 4
                    }
                }

                ColumnLayout {
                    id: contentColumn
                    width: flickArea.width - 12
                    spacing: 16

                    // -- ANALYTICS -- (top of panel: lifetime savings
                    // is the most rewarding read on every open;
                    // burying it under config sections meant the user
                    // had to scroll down every time)
                    SettingsSection {
                        title: "Analytics"
                        Layout.fillWidth: true

                        AnalyticsDashboard {
                            Layout.fillWidth: true
                            visible: true
                        }
                    }

                    // -- LAYOUT --
                    SettingsSection {
                        title: "Layout"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4

                            SettingsToggle {
                                Layout.fillWidth: true
                                text: "Function Keys (F1\u2013F12)"
                                checked: unifiedSettings.showFunctionRow
                                onToggled: function(c) { unifiedSettings.settingChanged("functionRow", c) }
                            }

                            SettingsToggle {
                                Layout.fillWidth: true
                                text: "Navigation Keys"
                                checked: unifiedSettings.showNavigation
                                onToggled: function(c) { unifiedSettings.settingChanged("navigation", c) }
                            }

                            SettingsToggle {
                                Layout.fillWidth: true
                                text: "Number Pad"
                                checked: unifiedSettings.showNumpad
                                onToggled: function(c) { unifiedSettings.settingChanged("numpad", c) }
                            }
                        }
                    }

                    // -- KEYBOARD LAYOUT --
                    SettingsSection {
                        title: "Keyboard Layout"
                        Layout.fillWidth: true

                        Row {
                            spacing: 6

                            Repeater {
                                model: keyboard ? keyboard.getAvailableLayouts() : []

                                Rectangle {
                                    width: 72
                                    height: 28
                                    radius: 5
                                    color: unifiedSettings.currentLayout === modelData.id
                                           ? "#4a9eff" : (layoutBtnArea.containsMouse ? "#444" : "#333")
                                    border.color: unifiedSettings.currentLayout === modelData.id ? "#6ab4ff" : "#555"

                                    Text {
                                        anchors.centerIn: parent
                                        text: modelData.name
                                        color: unifiedSettings.currentLayout === modelData.id ? "#fff" : "#ccc"
                                        font.pixelSize: 11
                                        font.weight: Font.DemiBold
                                    }

                                    MouseArea {
                                        id: layoutBtnArea
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: unifiedSettings.settingChanged("layout", modelData.id)
                                    }
                                }
                            }
                        }
                    }

                    // -- SUGGESTIONS --
                    SettingsSection {
                        title: "Suggestions"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4

                            SettingsToggle {
                                Layout.fillWidth: true
                                text: "Show Suggestions"
                                checked: unifiedSettings.suggestionsEnabled
                                onToggled: function(c) { unifiedSettings.settingChanged("suggestions", c) }
                            }

                            SettingsToggle {
                                Layout.fillWidth: true
                                text: "Auto-Space After Punctuation"
                                checked: unifiedSettings.autoSpaceAfterPunctuation
                                onToggled: function(c) { unifiedSettings.settingChanged("autoSpaceAfterPunctuation", c) }
                            }

                            SettingsToggle {
                                Layout.fillWidth: true
                                text: "Auto-Capitalize After Punctuation"
                                checked: unifiedSettings.autoCapitalizeAfterPunctuation
                                onToggled: function(c) { unifiedSettings.settingChanged("autoCapitalizeAfterPunctuation", c) }
                            }

                            SettingsToggle {
                                Layout.fillWidth: true
                                text: "Swipe Typing (drag across keys)"
                                checked: unifiedSettings.swipeEnabled
                                onToggled: function(c) { unifiedSettings.settingChanged("swipeEnabled", c) }
                            }

                            // Prediction count
                            Item {
                                Layout.fillWidth: true
                                implicitHeight: 28

                                Rectangle {
                                    anchors.fill: parent
                                    radius: 4
                                    color: "transparent"

                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 4
                                        anchors.rightMargin: 4
                                        spacing: 8

                                        Text {
                                            text: "Max Suggestions"
                                            color: "#c0c0c0"
                                            font.pixelSize: 12
                                            Layout.fillWidth: true
                                        }

                                        Rectangle {
                                            width: 24; height: 22; radius: 4
                                            color: countDownArea.containsMouse ? "#444" : "#333"
                                            Text { anchors.centerIn: parent; text: "\u2212"; color: "#ccc"; font.pixelSize: 14 }
                                            MouseArea {
                                                id: countDownArea
                                                anchors.fill: parent; hoverEnabled: true
                                                onClicked: {
                                                    var n = Math.max(3, unifiedSettings.predictionCount - 1)
                                                    unifiedSettings.settingChanged("predictionCount", n)
                                                }
                                            }
                                        }

                                        Text {
                                            text: unifiedSettings.predictionCount
                                            color: "#fff"
                                            font.pixelSize: 13
                                            font.bold: true
                                            horizontalAlignment: Text.AlignHCenter
                                            Layout.preferredWidth: 20
                                        }

                                        Rectangle {
                                            width: 24; height: 22; radius: 4
                                            color: countUpArea.containsMouse ? "#444" : "#333"
                                            Text { anchors.centerIn: parent; text: "+"; color: "#ccc"; font.pixelSize: 14 }
                                            MouseArea {
                                                id: countUpArea
                                                anchors.fill: parent; hoverEnabled: true
                                                onClicked: {
                                                    var n = Math.min(10, unifiedSettings.predictionCount + 1)
                                                    unifiedSettings.settingChanged("predictionCount", n)
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // -- SUGGESTION ENGINE --
                    // Picks the formula used to merge candidate words from
                    // the n-gram, PPM, and fuzzy predictors.  Default is
                    // "rank" — the historical rank-based fusion every
                    // existing user has been on.  See docs/HYBRID_MERGING.md
                    // for the trade-offs.
                    SettingsSection {
                        title: "Suggestion Engine"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4

                            Repeater {
                                model: [
                                    {
                                        id: "rank",
                                        name: "Default",
                                        desc: "Original behaviour — ranks by source position.",
                                        tooltip: "Sums weight / (rank + 1) from each source.\nIgnores how confident each predictor is — only positional rank matters.\nA 99%-confident #1 contributes the same as a 51%-confident #1.\nCheap, predictable, and what every existing user has been on."
                                    },
                                    {
                                        id: "rrf",
                                        name: "Consensus boost",
                                        desc: "Words multiple sources agree on rank higher.",
                                        tooltip: "Reciprocal Rank Fusion: weight / (60 + rank + 1).\nThe k=60 constant shrinks the #1 vs #2 gap from 2× to ~1.02×,\nso words that show up in multiple sources at modest rank can\nbeat words that lead in only one source. Try this if predictions\nfeel like they're shouting one source's pick over the others."
                                    },
                                    {
                                        id: "linear",
                                        name: "Confidence-weighted",
                                        desc: "Use each source's actual confidence, not just its rank.",
                                        tooltip: "Probability-space linear interpolation: Σ wᵢ · Pᵢ(w).\nEach source's raw scores are normalised to a sum-to-1\ndistribution before combining, so a very confident pick\ncontributes more than a barely-above-#2 pick. Defers to\nwhichever source is most sure on a given word.\nWhat Presage's MeritocracyCombiner ships."
                                    },
                                    {
                                        id: "loglinear",
                                        name: "Multiplicative",
                                        desc: "Strict — favours words that score well in every source.",
                                        tooltip: "Log-linear: Π Pᵢ(w)^wᵢ.\nThe per-source weights become exponents — a word that scores\nwell in every source wins, a word missing from one takes a heavy\n(but bounded) penalty. Fewer, surer suggestions; lower recall.\nKlakow (1998) showed log-linear beats linear interpolation by\n~20% relative perplexity on n-gram smoothing."
                                    }
                                ]

                                Rectangle {
                                    id: engineCard
                                    Layout.fillWidth: true
                                    height: 44
                                    radius: 5
                                    property bool isCurrent: unifiedSettings.mergeStrategy === modelData.id
                                    color: isCurrent
                                           ? "#2d4a7a"
                                           : (engineBtnArea.containsMouse ? "#3a3a3a" : "#2a2a2a")
                                    border.color: isCurrent ? "#6ab4ff" : "#444"
                                    border.width: isCurrent ? 2 : 1

                                    ColumnLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 10
                                        anchors.rightMargin: 10
                                        spacing: 1

                                        Text {
                                            text: modelData.name
                                            color: engineCard.isCurrent ? "#fff" : "#ddd"
                                            font.pixelSize: 12
                                            font.weight: Font.DemiBold
                                        }
                                        Text {
                                            text: modelData.desc
                                            color: engineCard.isCurrent ? "#cfe0ff" : "#888"
                                            font.pixelSize: 10
                                            wrapMode: Text.WordWrap
                                            Layout.fillWidth: true
                                        }
                                    }

                                    MouseArea {
                                        id: engineBtnArea
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: unifiedSettings.settingChanged("mergeStrategy", modelData.id)
                                    }

                                    // Hover tooltip — reveals the full explanation
                                    // (formula + when to use it) without crowding
                                    // the always-visible card.  Same delay as the
                                    // truncated-pill tooltip in Main.qml so OSK
                                    // hover behaviour stays consistent.
                                    ToolTip.visible: engineBtnArea.containsMouse
                                    ToolTip.text: modelData.tooltip
                                    ToolTip.delay: 400
                                }
                            }
                        }
                    }

                    // -- INPUT --
                    SettingsSection {
                        title: "Input"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4

                            SettingsToggle {
                                Layout.fillWidth: true
                                text: "Right-Click for Shifted Character"
                                checked: unifiedSettings.rightClickShift
                                onToggled: function(c) { unifiedSettings.settingChanged("rightClickShift", c) }
                            }

                            // Compatibility Mode has three meaningful
                            // states (Off / Auto / Always On) living
                            // behind two booleans (`compatMode` =
                            // manual force-on, `compatAutoDetect` =
                            // auto-detect on focus change).  Effective
                            // on-state is
                            // `manual OR (auto_enabled AND auto_active)`.
                            // The two-toggle UI was confusing because it
                            // exposed the boolean composition, not the
                            // user-visible state.  Collapse to a single
                            // 3-card picker; on click set both booleans
                            // unambiguously so toggling Always→Off
                            // doesn't leave auto-detect silently armed.
                            Item {
                                Layout.fillWidth: true
                                implicitHeight: 28

                                Text {
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.left: parent.left
                                    anchors.leftMargin: 4
                                    text: "Compatibility Mode"
                                    color: "#c0c0c0"
                                    font.pixelSize: 12
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 6

                                Repeater {
                                    model: [
                                        {
                                            id: "off",
                                            name: "Off",
                                            desc: "Predictions inserted with the fast suffix-only path everywhere.",
                                            tooltip: "Lowest-latency insertion, but can produce duplicate or scrambled text inside remote-desktop windows (TeamViewer, RDP, VNC) and IDEs that intercept keystrokes (VS Code, JetBrains).\nPick this only if you never type into those apps."
                                        },
                                        {
                                            id: "auto",
                                            name: "Auto (recommended)",
                                            desc: "Switches to the safer backspace+retype path when a known problem app has focus.",
                                            tooltip: "Detects remote-desktop clients (TeamViewer/RDP/VNC/AnyDesk) and IDEs with always-on keystroke interception (VS Code + Monaco forks, JetBrains family) and switches prediction insertion to BackSpace × N + retype while they're focused. Other apps keep the fast path. The detection list lives in _COMPAT_PROCESS_NAMES in keyboard_bridge.py."
                                        },
                                        {
                                            id: "always",
                                            name: "Always On",
                                            desc: "Forces backspace+retype everywhere. Pick this if Auto misses your app.",
                                            tooltip: "Use the BackSpace × N + retype path on every prediction click and every space-time autocorrect, regardless of which app has focus. Slightly more visible flicker on long words, but immune to keystroke-reordering issues. Pick this if you're using a remote-desktop tool or IDE that Auto doesn't recognise."
                                        }
                                    ]

                                    Rectangle {
                                        id: compatCard
                                        Layout.fillWidth: true
                                        // Auto-size to content so descriptions
                                        // that wrap onto a second line aren't
                                        // clipped.  10 px vertical padding on
                                        // each side matches the merge-strategy
                                        // cards visually for short descs while
                                        // letting longer ones grow.
                                        implicitHeight: compatCardContent.implicitHeight + 20
                                        radius: 5
                                        property string currentChoice: unifiedSettings.compatMode
                                                                       ? "always"
                                                                       : (unifiedSettings.compatAutoDetect ? "auto" : "off")
                                        property bool isCurrent: currentChoice === modelData.id
                                        color: isCurrent
                                               ? "#2d4a7a"
                                               : (compatBtnArea.containsMouse ? "#3a3a3a" : "#2a2a2a")
                                        border.color: isCurrent ? "#6ab4ff" : "#444"
                                        border.width: isCurrent ? 2 : 1

                                        ColumnLayout {
                                            id: compatCardContent
                                            anchors.left: parent.left
                                            anchors.right: parent.right
                                            anchors.verticalCenter: parent.verticalCenter
                                            anchors.leftMargin: 10
                                            anchors.rightMargin: 10
                                            spacing: 2

                                            Text {
                                                text: modelData.name
                                                color: compatCard.isCurrent ? "#fff" : "#ddd"
                                                font.pixelSize: 12
                                                font.weight: Font.DemiBold
                                            }
                                            Text {
                                                text: modelData.desc
                                                color: compatCard.isCurrent ? "#cfe0ff" : "#888"
                                                font.pixelSize: 10
                                                wrapMode: Text.WordWrap
                                                Layout.fillWidth: true
                                            }
                                        }

                                        MouseArea {
                                            id: compatBtnArea
                                            anchors.fill: parent
                                            hoverEnabled: true
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: {
                                                // Always set BOTH booleans so a transition
                                                // like Always→Off doesn't leave auto-detect
                                                // silently armed.
                                                if (modelData.id === "off") {
                                                    unifiedSettings.settingChanged("compatMode", false)
                                                    unifiedSettings.settingChanged("compatAutoDetect", false)
                                                } else if (modelData.id === "auto") {
                                                    unifiedSettings.settingChanged("compatMode", false)
                                                    unifiedSettings.settingChanged("compatAutoDetect", true)
                                                } else { // "always"
                                                    unifiedSettings.settingChanged("compatMode", true)
                                                    unifiedSettings.settingChanged("compatAutoDetect", false)
                                                }
                                            }
                                        }

                                        ToolTip.visible: compatBtnArea.containsMouse
                                        ToolTip.text: modelData.tooltip
                                        ToolTip.delay: 400
                                    }
                                }
                            }

                            // Hold-to-repeat delay — the threshold below
                            // which a press counts as a single click.
                            // Range 300–1500 ms in 100 ms steps.  Slow-
                            // motor users can crank up the delay to
                            // eliminate accidental double-keystrokes.
                            Item {
                                Layout.fillWidth: true
                                implicitHeight: 28

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 4
                                    anchors.rightMargin: 4
                                    spacing: 8

                                    Text {
                                        text: "Wait before auto-repeat"
                                        color: "#c0c0c0"
                                        font.pixelSize: 12
                                        Layout.fillWidth: true
                                    }

                                    Rectangle {
                                        width: 24; height: 22; radius: 4
                                        color: delayDownArea.containsMouse ? "#444" : "#333"
                                        Text { anchors.centerIn: parent; text: "−"; color: "#ccc"; font.pixelSize: 14 }
                                        MouseArea {
                                            id: delayDownArea
                                            anchors.fill: parent; hoverEnabled: true
                                            onClicked: {
                                                var n = Math.max(300, unifiedSettings.repeatDelay - 100)
                                                unifiedSettings.settingChanged("repeatDelay", n)
                                            }
                                        }
                                    }

                                    Text {
                                        text: unifiedSettings.repeatDelay + " ms"
                                        color: "#fff"
                                        font.pixelSize: 12
                                        horizontalAlignment: Text.AlignHCenter
                                        Layout.preferredWidth: 56
                                    }

                                    Rectangle {
                                        width: 24; height: 22; radius: 4
                                        color: delayUpArea.containsMouse ? "#444" : "#333"
                                        Text { anchors.centerIn: parent; text: "+"; color: "#ccc"; font.pixelSize: 14 }
                                        MouseArea {
                                            id: delayUpArea
                                            anchors.fill: parent; hoverEnabled: true
                                            onClicked: {
                                                var n = Math.min(1500, unifiedSettings.repeatDelay + 100)
                                                unifiedSettings.settingChanged("repeatDelay", n)
                                            }
                                        }
                                    }
                                }
                            }

                            // Hold-to-repeat interval — cadence between
                            // repeats once auto-repeat has started.
                            // Range 60–300 ms in 20 ms steps.  Lower =
                            // faster repeat (good for bulk delete);
                            // higher = slower (good if you overshoot).
                            Item {
                                Layout.fillWidth: true
                                implicitHeight: 28

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 4
                                    anchors.rightMargin: 4
                                    spacing: 8

                                    Text {
                                        text: "Time between repeats"
                                        color: "#c0c0c0"
                                        font.pixelSize: 12
                                        Layout.fillWidth: true
                                    }

                                    Rectangle {
                                        width: 24; height: 22; radius: 4
                                        color: rateDownArea.containsMouse ? "#444" : "#333"
                                        Text { anchors.centerIn: parent; text: "−"; color: "#ccc"; font.pixelSize: 14 }
                                        MouseArea {
                                            id: rateDownArea
                                            anchors.fill: parent; hoverEnabled: true
                                            onClicked: {
                                                // − decreases the displayed ms value (faster repeats).
                                                // Both rows now follow "+ raises the number, − lowers it."
                                                var n = Math.max(60, unifiedSettings.repeatInterval - 20)
                                                unifiedSettings.settingChanged("repeatInterval", n)
                                            }
                                        }
                                    }

                                    Text {
                                        text: unifiedSettings.repeatInterval + " ms"
                                        color: "#fff"
                                        font.pixelSize: 12
                                        horizontalAlignment: Text.AlignHCenter
                                        Layout.preferredWidth: 56
                                    }

                                    Rectangle {
                                        width: 24; height: 22; radius: 4
                                        color: rateUpArea.containsMouse ? "#444" : "#333"
                                        Text { anchors.centerIn: parent; text: "+"; color: "#ccc"; font.pixelSize: 14 }
                                        MouseArea {
                                            id: rateUpArea
                                            anchors.fill: parent; hoverEnabled: true
                                            onClicked: {
                                                // + raises the displayed ms value (slower repeats).
                                                var n = Math.min(300, unifiedSettings.repeatInterval + 20)
                                                unifiedSettings.settingChanged("repeatInterval", n)
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // -- VOCABULARY PACKS --
                    SettingsSection {
                        title: "Vocabulary Packs"
                        Layout.fillWidth: true

                        ColumnLayout {
                            id: vocabColumn
                            Layout.fillWidth: true
                            spacing: 4

                            property var enabledPacks: []

                            Component.onCompleted: {
                                if (keyboard) enabledPacks = keyboard.getEnabledPacks()
                            }

                            Repeater {
                                model: [
                                    { id: "medical",      label: "Medical" },
                                    { id: "programming",  label: "Programming" },
                                    { id: "academic",     label: "Academic" },
                                    { id: "gaming",       label: "Gaming" },
                                    { id: "business",     label: "Business" },
                                    { id: "nsfw",         label: "NSFW / Adult Language" }
                                ]

                                SettingsToggle {
                                    Layout.fillWidth: true
                                    text: modelData.label
                                    checked: vocabColumn.enabledPacks.indexOf(modelData.id) >= 0

                                    onToggled: function(c) {
                                        if (keyboard) {
                                            if (c)
                                                keyboard.enableVocabularyPack(modelData.id)
                                            else
                                                keyboard.disableVocabularyPack(modelData.id)
                                            vocabColumn.enabledPacks = keyboard.getEnabledPacks()
                                        }
                                    }
                                }
                            }

                            // Import custom pack
                            Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: 30
                                radius: 5
                                color: importPackArea.containsMouse ? "#3a3a5a" : "#2a2a3a"
                                border.color: "#4a4a6a"

                                Text {
                                    anchors.centerIn: parent
                                    text: vocabColumn.importStatus || "Import Custom Pack\u2026"
                                    color: vocabColumn.importStatus ? "#aaffaa" : "#aaaaff"
                                    font.pixelSize: 12
                                }

                                MouseArea {
                                    id: importPackArea
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: packFolderDialog.open()
                                }

                                Timer {
                                    id: importStatusTimer
                                    interval: 3000
                                    onTriggered: vocabColumn.importStatus = ""
                                }
                            }

                            property string importStatus: ""

                            Platform.FolderDialog {
                                id: packFolderDialog
                                title: "Select vocabulary pack folder"
                                onAccepted: {
                                    if (keyboard) {
                                        var path = folder.toString().replace("file:///", "")
                                        var packId = keyboard.importVocabularyPack(path)
                                        if (packId) {
                                            vocabColumn.importStatus = "Imported: " + packId
                                            vocabColumn.enabledPacks = keyboard.getEnabledPacks()
                                        } else {
                                            vocabColumn.importStatus = "Failed (needs dictionary.txt)"
                                        }
                                        importStatusTimer.restart()
                                    }
                                }
                            }

                            Text {
                                text: "Custom packs: " + (keyboard ? keyboard.getUserPacksDir() : "")
                                color: "#666"
                                font.pixelSize: 9
                                wrapMode: Text.WrapAnywhere
                                Layout.fillWidth: true
                            }
                        }
                    }

                    // -- THEME --
                    SettingsSection {
                        title: "Theme"
                        Layout.fillWidth: true

                        Flow {
                            Layout.fillWidth: true
                            spacing: 6

                            Repeater {
                                model: Object.keys(unifiedSettings.themeData)

                                Column {
                                    spacing: 3
                                    property var t: unifiedSettings.themeData[modelData]
                                    property bool isCurrent: unifiedSettings.currentTheme === modelData

                                    Rectangle {
                                        width: 34
                                        height: 34
                                        radius: 7
                                        color: t.background
                                        border.color: isCurrent ? t.accent : themeMa.containsMouse ? "#888" : "#555"
                                        border.width: isCurrent ? 2 : 1
                                        anchors.horizontalCenter: parent.horizontalCenter

                                        // Show key color + text color as inner swatch
                                        Rectangle {
                                            anchors.centerIn: parent
                                            width: 16; height: 12; radius: 3
                                            color: t.keyColor
                                            Text {
                                                anchors.centerIn: parent
                                                text: isCurrent ? "\u2713" : "A"
                                                color: t.textColor
                                                font.pixelSize: 9
                                                font.bold: true
                                            }
                                        }

                                        MouseArea {
                                            id: themeMa
                                            anchors.fill: parent
                                            hoverEnabled: true
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: unifiedSettings.settingChanged("theme", modelData)
                                        }
                                    }

                                    Text {
                                        text: t.name || modelData
                                        color: isCurrent ? "#ddd" : "#888"
                                        font.pixelSize: 9
                                        font.weight: isCurrent ? Font.DemiBold : Font.Normal
                                        anchors.horizontalCenter: parent.horizontalCenter
                                    }
                                }
                            }
                        }
                    }

                    // -- APPEARANCE --
                    SettingsSection {
                        title: "Appearance"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4

                            SettingsToggle {
                                Layout.fillWidth: true
                                text: "Key Click Sound"
                                checked: unifiedSettings.audioEnabled
                                onToggled: function(c) { unifiedSettings.settingChanged("audio", c) }
                            }

                            // Opacity slider
                            Item {
                                Layout.fillWidth: true
                                implicitHeight: 28

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 4
                                    anchors.rightMargin: 4
                                    spacing: 8

                                    Text {
                                        text: "Opacity"
                                        color: "#c0c0c0"
                                        font.pixelSize: 12
                                    }

                                    Slider {
                                        id: opacitySlider
                                        Layout.fillWidth: true
                                        from: 0.3
                                        to: 1.0
                                        stepSize: 0.05
                                        value: unifiedSettings.windowOpacity

                                        background: Rectangle {
                                            x: opacitySlider.leftPadding
                                            y: opacitySlider.topPadding + opacitySlider.availableHeight / 2 - height / 2
                                            width: opacitySlider.availableWidth
                                            height: 4
                                            radius: 2
                                            color: "#333"

                                            Rectangle {
                                                width: opacitySlider.visualPosition * parent.width
                                                height: parent.height
                                                radius: 2
                                                color: "#4a9eff"
                                            }
                                        }

                                        handle: Rectangle {
                                            x: opacitySlider.leftPadding + opacitySlider.visualPosition * (opacitySlider.availableWidth - width)
                                            y: opacitySlider.topPadding + opacitySlider.availableHeight / 2 - height / 2
                                            width: 14
                                            height: 14
                                            radius: 7
                                            color: opacitySlider.pressed ? "#fff" : "#ddd"
                                        }

                                        onMoved: unifiedSettings.settingChanged("windowOpacity", value)
                                    }

                                    Text {
                                        text: Math.round(opacitySlider.value * 100) + "%"
                                        color: "#fff"
                                        font.pixelSize: 11
                                        Layout.preferredWidth: 32
                                        horizontalAlignment: Text.AlignRight
                                    }
                                }
                            }
                        }
                    }

                    // -- DATA --
                    SettingsSection {
                        title: "Prediction Model"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            Text {
                                text: "Your learned words and phrases. Settings (layout, theme, etc.) save automatically."
                                color: "#888"
                                font.pixelSize: 10
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }

                            SettingsToggle {
                                Layout.fillWidth: true
                                text: "Auto-Save on Exit"
                                checked: unifiedSettings.autoSaveOnExit
                                onToggled: function(c) { unifiedSettings.settingChanged("autoSaveOnExit", c) }
                            }

                            // Save model button
                            Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: 30
                                radius: 5
                                color: saveArea.containsMouse ? "#3a5a3a" : "#2a3a2a"
                                border.color: "#4a6a4a"

                                Text {
                                    anchors.centerIn: parent
                                    text: "Save Now"
                                    color: "#aaffaa"
                                    font.pixelSize: 12
                                }

                                MouseArea {
                                    id: saveArea
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: { if (keyboard) keyboard.savePredictionModel() }
                                }
                            }

                            // Clear user data button with multi-step confirmation
                            Rectangle {
                                id: clearBtn
                                Layout.fillWidth: true
                                implicitHeight: 30
                                radius: 5

                                property int confirmStep: 0  // 0=idle, 1=first click, 2=confirmed

                                color: {
                                    if (confirmStep === 2) return "#2a3a2a"
                                    if (confirmStep === 1) return clearArea.containsMouse ? "#7a2a2a" : "#5a2a2a"
                                    return clearArea.containsMouse ? "#5a2a2a" : "#3a2222"
                                }
                                border.color: confirmStep === 1 ? "#ff4444" : "#6a4444"
                                border.width: confirmStep === 1 ? 2 : 1

                                Text {
                                    anchors.centerIn: parent
                                    text: {
                                        if (clearBtn.confirmStep === 2) return "Cleared!"
                                        if (clearBtn.confirmStep === 1) return "Are you sure? Click again to confirm"
                                        return "Clear Learned Data"
                                    }
                                    color: {
                                        if (clearBtn.confirmStep === 2) return "#aaffaa"
                                        if (clearBtn.confirmStep === 1) return "#ff6666"
                                        return "#ffaaaa"
                                    }
                                    font.pixelSize: 12
                                    font.bold: clearBtn.confirmStep === 1
                                }

                                Timer {
                                    id: clearResetTimer
                                    interval: 3000
                                    onTriggered: clearBtn.confirmStep = 0
                                }

                                MouseArea {
                                    id: clearArea
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        if (clearBtn.confirmStep === 0) {
                                            clearBtn.confirmStep = 1
                                            clearResetTimer.restart()
                                        } else if (clearBtn.confirmStep === 1) {
                                            if (keyboard) keyboard.clearUserData()
                                            clearBtn.confirmStep = 2
                                            clearResetTimer.restart()
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // -- TOOLS --
                    SettingsSection {
                        title: "Tools"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: 32
                                radius: 5
                                color: helpBtnArea.containsMouse ? "#3a3a5a" : "#2a2a3a"
                                border.color: "#4a4a6a"
                                border.width: 1

                                Text {
                                    anchors.centerIn: parent
                                    text: "Help & Shortcuts"
                                    color: helpBtnArea.containsMouse ? "#cce" : "#aab"
                                    font.pixelSize: 12
                                }

                                MouseArea {
                                    id: helpBtnArea
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: unifiedSettings.showHelpRequested()
                                }
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: 32
                                radius: 5
                                color: vizBtnArea.containsMouse ? "#3a3a5a" : "#2a2a3a"
                                border.color: "#4a4a6a"
                                border.width: 1

                                Text {
                                    anchors.centerIn: parent
                                    text: "Language Model Visualization"
                                    color: vizBtnArea.containsMouse ? "#cce" : "#aab"
                                    font.pixelSize: 12
                                }

                                MouseArea {
                                    id: vizBtnArea
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: unifiedSettings.showVisualizationRequested()
                                }
                            }
                        }
                    }

                    // -- UPDATES --
                    SettingsSection {
                        title: "Updates"
                        Layout.fillWidth: true

                        // Running version — read straight from the bridge
                        // (src/__version__.py).  First place to look when
                        // diagnosing "did the auto-update actually apply?"
                        Text {
                            Layout.fillWidth: true
                            color: "#bbb"
                            font.pixelSize: 12
                            text: unifiedSettings.appVersion !== ""
                                  ? "Installed: Alpha-OSK v" + unifiedSettings.appVersion
                                  : ""
                            visible: unifiedSettings.appVersion !== ""
                        }

                        SettingsToggle {
                            Layout.fillWidth: true
                            text: "Check for updates on startup"
                            checked: unifiedSettings.autoCheckUpdates
                            onToggled: function(c) { unifiedSettings.settingChanged("autoCheckUpdates", c) }
                        }

                        // "Check now" row — kicks the bridge and shows
                        // status text returned via the updateStatus
                        // property (the parent wires this to update
                        // signals from the bridge).
                        Item {
                            Layout.fillWidth: true
                            implicitHeight: 32

                            RowLayout {
                                anchors.fill: parent
                                spacing: 8

                                Button {
                                    text: "Check Now"
                                    enabled: unifiedSettings.updateStatus !== "checking"
                                    onClicked: unifiedSettings.checkForUpdatesNowRequested()
                                }
                                Text {
                                    Layout.fillWidth: true
                                    color: "#bbb"
                                    font.pixelSize: 12
                                    elide: Text.ElideRight
                                    text: {
                                        switch (unifiedSettings.updateStatus) {
                                            case "checking": return "Checking…"
                                            case "uptodate": return "Up to date."
                                            case "available": return "Update available — see banner."
                                            case "failed":   return "Check failed — try again later."
                                            default: return ""
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // -- DEVELOPER --
                    SettingsSection {
                        title: "Developer"
                        Layout.fillWidth: true

                        SettingsToggle {
                            Layout.fillWidth: true
                            text: "Debug Mode"
                            checked: unifiedSettings.debugMode
                            onToggled: function(c) { unifiedSettings.settingChanged("debugMode", c) }
                        }
                    }

                    // Bottom spacer
                    Item { height: 4 }
                }
            }
        }
    }
}
