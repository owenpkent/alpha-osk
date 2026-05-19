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
    // Merge strategy -- see docs/architecture/HYBRID_MERGING.md.  "rank" is the
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
    // Compatibility mode -- see KeyboardBridge.setCompatMode.  Covers
    // remote-desktop sessions and IDEs with always-on keystroke
    // interception (VS Code + forks, JetBrains family).
    property bool compatMode: false
    property bool compatAutoDetect: true

    // Debug
    property bool debugMode: false

    // Auto-update -- see src/updater.py
    property bool autoCheckUpdates: true
    // "Check now" feedback -- set to "checking" / "uptodate" / "" by parent
    property string updateStatus: ""
    // Running app version, surfaced in the Updates section so a user
    // (or you, debugging) can confirm what's actually installed.
    property string appVersion: ""

    // Drill-down navigation.  "home" shows the four category cards;
    // each other value shows that category's sub-view with a back
    // arrow.  The parent calls resetToHome() on window open so the
    // panel always lands on home, not whatever the user last visited.
    property string currentView: "home"

    function resetToHome() { currentView = "home" }

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
                    anchors.leftMargin: unifiedSettings.currentView === "home" ? 0 : 34
                    cursorShape: Qt.SizeAllCursor
                    onPressed: {
                        var win = Window.window
                        if (win && win.startSystemMove) win.startSystemMove()
                    }
                }

                RowLayout {
                    anchors.fill: parent
                    spacing: 6

                    // Back arrow.  Only visible when drilled in; click
                    // returns to the home grid.  Kept separate from the
                    // close button so it doesn't visually compete with
                    // the dismiss action.
                    Rectangle {
                        Layout.preferredWidth: 28
                        Layout.preferredHeight: 26
                        radius: 5
                        visible: unifiedSettings.currentView !== "home"
                        color: backArea.containsMouse ? "#2a3a5a" : "transparent"

                        Text {
                            anchors.centerIn: parent
                            text: "‹"  // single left-pointing angle quote, looks like a back chevron
                            color: backArea.containsMouse ? "#cce" : "#aab"
                            font.pixelSize: 22
                        }

                        MouseArea {
                            id: backArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: unifiedSettings.currentView = "home"
                        }

                        ToolTip.visible: backArea.containsMouse
                        ToolTip.text: qsTr("Back to Settings")
                        ToolTip.delay: 400
                    }

                    Text {
                        text: {
                            switch (unifiedSettings.currentView) {
                                case "appearance": return qsTr("Appearance")
                                case "typing": return qsTr("Smart Typing")
                                case "model": return qsTr("Your Language Model")
                                case "data": return qsTr("Data & Privacy")
                                default: return "⚙ " + qsTr("Settings")
                            }
                        }
                        color: "#fff"
                        font.pixelSize: 16
                        font.bold: true
                    }

                    Item { Layout.fillWidth: true }

                    Rectangle {
                        Layout.preferredWidth: 26
                        Layout.preferredHeight: 26
                        radius: 5
                        color: closeArea.containsMouse ? "#5a2020" : "transparent"

                        Text {
                            anchors.centerIn: parent
                            text: "✕"
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

                // Reset scroll to the top whenever the user drills into
                // a new sub-view.  Without this the new view would inherit
                // the previous view's scroll offset and could open mid-
                // section, which reads as a bug.
                Connections {
                    target: unifiedSettings
                    function onCurrentViewChanged() { flickArea.contentY = 0 }
                }

                ScrollBar.vertical: ScrollBar {
                    // AsNeeded misbehaves in Qt 6 — sometimes shows the bar
                    // when contentHeight equals height (e.g. on the short
                    // home grid).  Drive it from an explicit overflow check.
                    policy: flickArea.contentHeight > flickArea.height + 1
                            ? ScrollBar.AlwaysOn
                            : ScrollBar.AlwaysOff
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

                    // ============================================================
                    // HOME view -- four category cards
                    // ============================================================
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        visible: unifiedSettings.currentView === "home"

                        // Category card factory.  Each card has a title,
                        // a subtitle listing what's inside, and a right-
                        // edge chevron to communicate "drill in".
                        Repeater {
                            model: [
                                {
                                    id: "appearance",
                                    title: qsTr("Appearance"),
                                    subtitle: qsTr("Panels shown, keyboard layout, theme, sound and opacity"),
                                    accent: "#6ab4ff"
                                },
                                {
                                    id: "typing",
                                    title: qsTr("Smart Typing"),
                                    subtitle: qsTr("Suggestions, prediction engine, input behaviour and timing"),
                                    accent: "#9eda6e"
                                },
                                {
                                    id: "model",
                                    title: qsTr("Your Language Model"),
                                    subtitle: qsTr("Vocabulary packs, learned words and your model dashboard"),
                                    accent: "#e0a85a"
                                },
                                {
                                    id: "data",
                                    title: qsTr("Data & Privacy"),
                                    subtitle: qsTr("Telemetry, updates, help and developer options"),
                                    accent: "#c98ee0"
                                }
                            ]

                            Rectangle {
                                id: catCard
                                Layout.fillWidth: true
                                implicitHeight: catContent.implicitHeight + 22
                                radius: 7
                                color: catArea.containsMouse ? "#33333a" : "#28282c"
                                border.color: catArea.containsMouse ? modelData.accent : "#3c3c42"
                                border.width: 1

                                Behavior on color { ColorAnimation { duration: 120 } }
                                Behavior on border.color { ColorAnimation { duration: 120 } }

                                RowLayout {
                                    id: catContent
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.leftMargin: 14
                                    anchors.rightMargin: 12
                                    spacing: 10

                                    // Accent stripe down the left edge --
                                    // colour-codes each category and
                                    // gives a faster scan than text alone.
                                    Rectangle {
                                        Layout.preferredWidth: 4
                                        Layout.preferredHeight: 38
                                        radius: 2
                                        color: modelData.accent
                                    }

                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 2

                                        Text {
                                            text: modelData.title
                                            color: "#fff"
                                            font.pixelSize: 14
                                            font.weight: Font.DemiBold
                                        }
                                        Text {
                                            text: modelData.subtitle
                                            color: "#9a9a9e"
                                            font.pixelSize: 11
                                            wrapMode: Text.WordWrap
                                            Layout.fillWidth: true
                                        }
                                    }

                                    Text {
                                        text: "›"  // single right-angle quote (chevron)
                                        color: catArea.containsMouse ? modelData.accent : "#666"
                                        font.pixelSize: 22
                                    }
                                }

                                MouseArea {
                                    id: catArea
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: unifiedSettings.currentView = modelData.id
                                }
                            }
                        }
                    }

                    // ============================================================
                    // APPEARANCE view -- panels, keyboard layout, theme, sound & opacity
                    // ============================================================
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 16
                        visible: unifiedSettings.currentView === "appearance"

                        // -- Panels (function row, navigation, numpad) --
                        SettingsSection {
                            title: "Panels"
                            Layout.fillWidth: true

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 4

                                SettingsToggle {
                                    Layout.fillWidth: true
                                    text: "Function Keys (F1–F12)"
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

                        // -- Keyboard Layout --
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

                        // -- Theme --
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
                                                    text: isCurrent ? "✓" : "A"
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

                        // -- Sound & Opacity --
                        // Renamed from "Appearance" since the parent
                        // category is called "Appearance" now.  Keep the
                        // audio toggle and the opacity slider together
                        // because they're both "the keyboard's physical
                        // presence" knobs (how loud, how visible).
                        SettingsSection {
                            title: "Sound & Opacity"
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
                    }

                    // ============================================================
                    // SMART TYPING view -- suggestions, engine, input
                    // ============================================================
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 16
                        visible: unifiedSettings.currentView === "typing"

                        // -- Suggestions --
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
                                                Text { anchors.centerIn: parent; text: "−"; color: "#ccc"; font.pixelSize: 14 }
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

                        // -- Suggestion Engine --
                        // Picks the formula used to merge candidate words from
                        // the n-gram, PPM, and fuzzy predictors.  Default is
                        // "rank" -- the historical rank-based fusion every
                        // existing user has been on.  See docs/architecture/HYBRID_MERGING.md
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
                                            desc: "Original behaviour -- ranks by source position.",
                                            tooltip: "Sums weight / (rank + 1) from each source.\nIgnores how confident each predictor is -- only positional rank matters.\nA 99%-confident #1 contributes the same as a 51%-confident #1.\nCheap, predictable, and what every existing user has been on."
                                        },
                                        {
                                            id: "rrf",
                                            name: "Consensus boost",
                                            desc: "Words multiple sources agree on rank higher.",
                                            tooltip: "Reciprocal Rank Fusion: weight / (60 + rank + 1).\nThe k=60 constant shrinks the #1 vs #2 gap from 2x to ~1.02x,\nso words that show up in multiple sources at modest rank can\nbeat words that lead in only one source. Try this if predictions\nfeel like they're shouting one source's pick over the others."
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
                                            desc: "Strict -- favours words that score well in every source.",
                                            tooltip: "Log-linear: Π Pᵢ(w)^wᵢ.\nThe per-source weights become exponents -- a word that scores\nwell in every source wins, a word missing from one takes a heavy\n(but bounded) penalty. Fewer, surer suggestions; lower recall.\nKlakow (1998) showed log-linear beats linear interpolation by\n~20% relative perplexity on n-gram smoothing."
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

                                        // Hover tooltip -- reveals the full explanation
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

                        // -- Input --
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
                                // unambiguously so toggling Always->Off
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
                                                tooltip: "Detects remote-desktop clients (TeamViewer/RDP/VNC/AnyDesk) and IDEs with always-on keystroke interception (VS Code + Monaco forks, JetBrains family) and switches prediction insertion to BackSpace x N + retype while they're focused. Other apps keep the fast path. The detection list lives in _COMPAT_PROCESS_NAMES in keyboard_bridge.py."
                                            },
                                            {
                                                id: "always",
                                                name: "Always On",
                                                desc: "Forces backspace+retype everywhere. Pick this if Auto misses your app.",
                                                tooltip: "Use the BackSpace x N + retype path on every prediction click and every space-time autocorrect, regardless of which app has focus. Slightly more visible flicker on long words, but immune to keystroke-reordering issues. Pick this if you're using a remote-desktop tool or IDE that Auto doesn't recognise."
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
                                                    // like Always->Off doesn't leave auto-detect
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

                                // Hold-to-repeat delay -- the threshold below
                                // which a press counts as a single click.
                                // Range 300-1500 ms in 100 ms steps.  Slow-
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

                                // Hold-to-repeat interval -- cadence between
                                // repeats once auto-repeat has started.
                                // Range 60-300 ms in 20 ms steps.  Lower =
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
                                                    // - decreases the displayed ms value (faster repeats).
                                                    // Both rows now follow "+ raises the number, - lowers it."
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
                    }

                    // ============================================================
                    // YOUR LANGUAGE MODEL view -- viz, vocab packs, prediction model
                    // ============================================================
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 16
                        visible: unifiedSettings.currentView === "model"

                        // Standalone "Open Dashboard" button -- the
                        // visualization window is the marquee tool here,
                        // so it gets the top spot rather than burying
                        // inside a Section.
                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: 38
                            radius: 6
                            color: vizBtnArea.containsMouse ? "#3a3a5a" : "#2a2a3a"
                            border.color: "#4a4a6a"
                            border.width: 1

                            Text {
                                anchors.centerIn: parent
                                text: "Open Dashboard →"
                                color: vizBtnArea.containsMouse ? "#cce" : "#aab"
                                font.pixelSize: 13
                                font.weight: Font.DemiBold
                            }

                            MouseArea {
                                id: vizBtnArea
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: unifiedSettings.showVisualizationRequested()
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: "Word cloud, word flow, and analytics for the words you've taught the keyboard."
                            color: "#888"
                            font.pixelSize: 10
                            wrapMode: Text.WordWrap
                        }

                        // -- Vocabulary Packs --
                        // Built-in packs (medical / programming / etc.)
                        // were dropped: each was 200-400 words, way too
                        // thin to compete with the engine's organic
                        // learning, and the disable path didn't fully
                        // undo the predictor injection.  The system is
                        // now import-only -- power users with a real
                        // domain wordlist can drop a folder in.  Any
                        // packs they import appear as toggles below;
                        // the empty state is just the Import button +
                        // a one-line note about the format.
                        SettingsSection {
                            title: "Vocabulary Packs"
                            Layout.fillWidth: true

                            ColumnLayout {
                                id: vocabColumn
                                Layout.fillWidth: true
                                spacing: 4

                                property var availablePacks: []
                                property var enabledPacks: []
                                property string importStatus: ""

                                function refresh() {
                                    if (!keyboard) return
                                    availablePacks = keyboard.getAvailablePacks()
                                    enabledPacks = keyboard.getEnabledPacks()
                                }

                                Component.onCompleted: refresh()

                                // Empty-state explainer.  Visible only
                                // when nothing has been imported yet.
                                Text {
                                    Layout.fillWidth: true
                                    visible: vocabColumn.availablePacks.length === 0
                                    text: "No vocabulary packs imported yet. A pack is a folder containing dictionary.txt (one word per line), with optional bigrams.txt and trigrams.txt for word-pair / triple boosts."
                                    color: "#888"
                                    font.pixelSize: 10
                                    wrapMode: Text.WordWrap
                                }

                                // Imported-pack toggles.  Driven by
                                // getAvailablePacks() so the list grows
                                // automatically as the user imports more.
                                Repeater {
                                    model: vocabColumn.availablePacks

                                    SettingsToggle {
                                        Layout.fillWidth: true
                                        text: modelData.name + " (" + modelData.words + " words)"
                                        checked: vocabColumn.enabledPacks.indexOf(modelData.id) >= 0

                                        onToggled: function(c) {
                                            if (!keyboard) return
                                            if (c)
                                                keyboard.enableVocabularyPack(modelData.id)
                                            else
                                                keyboard.disableVocabularyPack(modelData.id)
                                            vocabColumn.enabledPacks = keyboard.getEnabledPacks()
                                        }
                                    }
                                }

                                // Import custom pack
                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.topMargin: 6
                                    implicitHeight: 30
                                    radius: 5
                                    color: importPackArea.containsMouse ? "#3a3a5a" : "#2a2a3a"
                                    border.color: "#4a4a6a"

                                    Text {
                                        anchors.centerIn: parent
                                        text: vocabColumn.importStatus || "Import Custom Pack…"
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

                                Platform.FolderDialog {
                                    id: packFolderDialog
                                    title: "Select vocabulary pack folder"
                                    onAccepted: {
                                        if (keyboard) {
                                            var path = folder.toString().replace("file:///", "")
                                            var packId = keyboard.importVocabularyPack(path)
                                            if (packId) {
                                                vocabColumn.importStatus = "Imported: " + packId
                                                vocabColumn.refresh()
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

                        // -- Prediction Model (auto-save / save now / clear) --
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
                    }

                    // ============================================================
                    // DATA & PRIVACY view -- privacy, updates, help, developer
                    // ============================================================
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 16
                        visible: unifiedSettings.currentView === "data"

                        // Standalone "Help & Shortcuts" button -- single
                        // entry, lifted out of the old "Tools" section.
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

                        // -- Data Backup --
                        // Export the user's model + analytics + packs
                        // to a single .zip, import to restore on a new
                        // machine.  Telemetry anon_id is excluded by
                        // src/data_export.py -- contributions must not
                        // be linkable across machines.
                        SettingsSection {
                            title: "Data Backup"
                            Layout.fillWidth: true

                            ColumnLayout {
                                id: dataBackupCol
                                Layout.fillWidth: true
                                spacing: 6

                                property string statusMessage: ""
                                property string statusColor: "#aaffaa"
                                property string pendingImportPath: ""
                                property var pendingImportSummary: null

                                Text {
                                    text: "Back up your learned words, predictions, lifetime stats, and imported vocabulary packs to a single file. Move it to another computer to restore your data."
                                    color: "#aaa"
                                    font.pixelSize: 10
                                    wrapMode: Text.WordWrap
                                    Layout.fillWidth: true
                                }

                                Text {
                                    text: "Default folder: " + (keyboard ? keyboard.getDefaultExportDir() : "")
                                    color: "#777"
                                    font.pixelSize: 9
                                    wrapMode: Text.WrapAnywhere
                                    Layout.fillWidth: true
                                }

                                // Status / error toast
                                Text {
                                    text: dataBackupCol.statusMessage
                                    color: dataBackupCol.statusColor
                                    font.pixelSize: 11
                                    wrapMode: Text.WordWrap
                                    Layout.fillWidth: true
                                    visible: dataBackupCol.statusMessage !== ""
                                }

                                // Export button
                                Rectangle {
                                    Layout.fillWidth: true
                                    implicitHeight: 30
                                    radius: 5
                                    color: exportDataArea.containsMouse ? "#3a5a3a" : "#2a3a2a"
                                    border.color: "#4a6a4a"

                                    Text {
                                        anchors.centerIn: parent
                                        text: "Export Data…"
                                        color: "#aaffaa"
                                        font.pixelSize: 12
                                    }

                                    MouseArea {
                                        id: exportDataArea
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            if (!keyboard) return
                                            // Native Save dialog opens via Python
                                            // QFileDialog so the suggested filename
                                            // pre-populates -- the QML labs dialog
                                            // ignores initial-filename hints on
                                            // some Qt versions.
                                            var path = keyboard.pickExportPath()
                                            if (path === "") return
                                            var err = keyboard.exportUserData(path)
                                            if (err === "") {
                                                dataBackupCol.statusMessage = "Exported to " + path
                                                dataBackupCol.statusColor = "#aaffaa"
                                            } else {
                                                dataBackupCol.statusMessage = "Export failed: " + err
                                                dataBackupCol.statusColor = "#ffaaaa"
                                            }
                                            dataBackupStatusTimer.restart()
                                        }
                                    }
                                }

                                // Import button
                                Rectangle {
                                    Layout.fillWidth: true
                                    implicitHeight: 30
                                    radius: 5
                                    color: importDataArea.containsMouse ? "#3a3a5a" : "#2a2a3a"
                                    border.color: "#4a4a6a"

                                    Text {
                                        anchors.centerIn: parent
                                        text: "Import Data…"
                                        color: "#aabbff"
                                        font.pixelSize: 12
                                    }

                                    MouseArea {
                                        id: importDataArea
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            if (!keyboard) return
                                            var path = keyboard.pickImportPath()
                                            if (path === "") return
                                            var summary = keyboard.inspectUserExport(path)
                                            if (summary.ok) {
                                                dataBackupCol.pendingImportPath = path
                                                dataBackupCol.pendingImportSummary = summary
                                                dataBackupCol.statusMessage = ""
                                            } else {
                                                dataBackupCol.statusMessage = "Cannot use this file: " + summary.error
                                                dataBackupCol.statusColor = "#ffaaaa"
                                                dataBackupStatusTimer.restart()
                                            }
                                        }
                                    }
                                }

                                // Pending-import preview + confirm/cancel
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 4
                                    visible: dataBackupCol.pendingImportPath !== ""

                                    Rectangle {
                                        Layout.fillWidth: true
                                        radius: 5
                                        color: "#332a1a"
                                        border.color: "#aa6633"
                                        implicitHeight: importPreviewCol.implicitHeight + 12

                                        ColumnLayout {
                                            id: importPreviewCol
                                            anchors.left: parent.left
                                            anchors.right: parent.right
                                            anchors.verticalCenter: parent.verticalCenter
                                            anchors.leftMargin: 8
                                            anchors.rightMargin: 8
                                            spacing: 2

                                            Text {
                                                text: "About to replace your data"
                                                color: "#ffcc88"
                                                font.pixelSize: 11
                                                font.bold: true
                                            }
                                            Text {
                                                text: "Exported by Alpha-OSK v" + (dataBackupCol.pendingImportSummary ? dataBackupCol.pendingImportSummary.app_version : "")
                                                color: "#ccc"
                                                font.pixelSize: 10
                                            }
                                            Text {
                                                text: "Date: " + (dataBackupCol.pendingImportSummary ? dataBackupCol.pendingImportSummary.exported_at : "")
                                                color: "#ccc"
                                                font.pixelSize: 10
                                                Layout.fillWidth: true
                                                elide: Text.ElideRight
                                            }
                                            Text {
                                                text: (dataBackupCol.pendingImportSummary
                                                      ? dataBackupCol.pendingImportSummary.files.length + " files, "
                                                        + dataBackupCol.pendingImportSummary.pack_ids.length + " vocabulary packs"
                                                      : "")
                                                color: "#ccc"
                                                font.pixelSize: 10
                                            }
                                            Text {
                                                text: "Your current data will be saved to a rescue file in the exports folder before overwrite."
                                                color: "#999"
                                                font.pixelSize: 9
                                                wrapMode: Text.WordWrap
                                                Layout.fillWidth: true
                                            }
                                        }
                                    }

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 6

                                        Rectangle {
                                            Layout.fillWidth: true
                                            implicitHeight: 28
                                            radius: 5
                                            color: applyImportArea.containsMouse ? "#5a3a3a" : "#3a2a2a"
                                            border.color: "#aa6666"
                                            Text {
                                                anchors.centerIn: parent
                                                text: "Replace My Data"
                                                color: "#ffcccc"
                                                font.pixelSize: 11
                                                font.bold: true
                                            }
                                            MouseArea {
                                                id: applyImportArea
                                                anchors.fill: parent
                                                hoverEnabled: true
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: {
                                                    if (!keyboard || dataBackupCol.pendingImportPath === "") return
                                                    var err = keyboard.importUserData(dataBackupCol.pendingImportPath)
                                                    if (err === "") {
                                                        dataBackupCol.statusMessage = "Imported. Re-enable vocabulary packs from Your Language Model if needed."
                                                        dataBackupCol.statusColor = "#aaffaa"
                                                    } else {
                                                        dataBackupCol.statusMessage = "Import failed: " + err
                                                        dataBackupCol.statusColor = "#ffaaaa"
                                                    }
                                                    dataBackupCol.pendingImportPath = ""
                                                    dataBackupCol.pendingImportSummary = null
                                                    dataBackupStatusTimer.restart()
                                                }
                                            }
                                        }

                                        Rectangle {
                                            Layout.preferredWidth: 80
                                            implicitHeight: 28
                                            radius: 5
                                            color: cancelImportArea.containsMouse ? "#3a3a3a" : "#2a2a2a"
                                            border.color: "#4a4a4a"
                                            Text {
                                                anchors.centerIn: parent
                                                text: "Cancel"
                                                color: "#aaa"
                                                font.pixelSize: 11
                                            }
                                            MouseArea {
                                                id: cancelImportArea
                                                anchors.fill: parent
                                                hoverEnabled: true
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: {
                                                    dataBackupCol.pendingImportPath = ""
                                                    dataBackupCol.pendingImportSummary = null
                                                    dataBackupCol.statusMessage = ""
                                                }
                                            }
                                        }
                                    }
                                }

                                Timer {
                                    id: dataBackupStatusTimer
                                    interval: 6000
                                    onTriggered: dataBackupCol.statusMessage = ""
                                }
                            }
                        }

                        // -- Privacy --
                        // Opt-in usage telemetry. OFF by default. The
                        // toggle's persisted state lives in the
                        // TelemetryClient's own JSON file (telemetry.json),
                        // not in appSettings, because the bridge owns the
                        // anon_id lifecycle and we want a single source of
                        // truth. See docs/PRIVACY.md (user-facing) and
                        // docs/architecture/TELEMETRY.md (design).
                        SettingsSection {
                            title: "Privacy"
                            Layout.fillWidth: true

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 6

                                Text {
                                    text: "Alpha-OSK runs entirely on your computer. Nothing leaves your machine unless you opt in below."
                                    color: "#aaa"
                                    font.pixelSize: 10
                                    wrapMode: Text.WordWrap
                                    Layout.fillWidth: true
                                }

                                SettingsToggle {
                                    id: telemetryToggle
                                    Layout.fillWidth: true
                                    text: "Share anonymous usage stats"
                                    // Initial state queried once on mount.
                                    // No appSettings binding -- the
                                    // TelemetryClient is authoritative.
                                    checked: false
                                    Component.onCompleted: {
                                        if (keyboard) checked = keyboard.getTelemetryEnabled()
                                    }
                                    onToggled: function(c) {
                                        if (keyboard) keyboard.setTelemetryEnabled(c)
                                    }
                                }

                                Text {
                                    text: "Sends a small weekly report (lifetime keystroke totals only) so we can track total community impact. No content, no words, no per-key data. Off by default. See docs/PRIVACY.md for the full list."
                                    color: "#888"
                                    font.pixelSize: 9
                                    wrapMode: Text.WordWrap
                                    Layout.fillWidth: true
                                    visible: telemetryToggle.checked
                                }

                                // Right-to-be-forgotten button. Only shown
                                // when the toggle is on, since there's no
                                // contributed row to delete otherwise.
                                // Two-step confirmation matches the
                                // "Clear Learned Data" button's pattern.
                                Rectangle {
                                    id: forgetBtn
                                    Layout.fillWidth: true
                                    implicitHeight: 30
                                    radius: 5
                                    visible: telemetryToggle.checked

                                    property int confirmStep: 0  // 0=idle, 1=first click, 2=confirmed

                                    color: {
                                        if (confirmStep === 2) return "#2a3a2a"
                                        if (confirmStep === 1) return forgetArea.containsMouse ? "#7a2a2a" : "#5a2a2a"
                                        return forgetArea.containsMouse ? "#3a3a3a" : "#2a2a2a"
                                    }
                                    border.color: confirmStep === 1 ? "#ff4444" : "#4a4a4a"
                                    border.width: confirmStep === 1 ? 2 : 1

                                    Text {
                                        anchors.centerIn: parent
                                        text: {
                                            if (forgetBtn.confirmStep === 2) return "Deleted!"
                                            if (forgetBtn.confirmStep === 1) return "Click again to confirm"
                                            return "Delete my contributed data"
                                        }
                                        color: {
                                            if (forgetBtn.confirmStep === 2) return "#aaffaa"
                                            if (forgetBtn.confirmStep === 1) return "#ff6666"
                                            return "#ccc"
                                        }
                                        font.pixelSize: 11
                                        font.bold: forgetBtn.confirmStep === 1
                                    }

                                    Timer {
                                        id: forgetResetTimer
                                        interval: 3000
                                        onTriggered: forgetBtn.confirmStep = 0
                                    }

                                    MouseArea {
                                        id: forgetArea
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            if (forgetBtn.confirmStep === 0) {
                                                forgetBtn.confirmStep = 1
                                                forgetResetTimer.restart()
                                            } else if (forgetBtn.confirmStep === 1) {
                                                if (keyboard) keyboard.forgetTelemetryData()
                                                forgetBtn.confirmStep = 2
                                                forgetResetTimer.restart()
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        // -- Updates --
                        SettingsSection {
                            title: "Updates"
                            Layout.fillWidth: true

                            // Running version -- read straight from the bridge
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

                            // "Check now" row -- kicks the bridge and shows
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
                                                case "available": return "Update available -- see banner."
                                                case "failed":   return "Check failed -- try again later."
                                                default: return ""
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        // -- Developer --
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
                    }

                    // Bottom spacer
                    Item { height: 4 }
                }
            }
        }
    }
}
