import QtQuick 2.15
import QtQuick.Layouts 1.15
import QtQuick.Controls 2.15
import QtQuick.Window 2.15

Item {
    id: unifiedSettings

    // Layout properties
    property bool showFunctionRow: false
    property bool showNavigation: false
    property bool showNumpad: false
    property string currentTheme: "dark"

    // Suggestions
    property bool suggestionsEnabled: true
    property int predictionCount: 8

    // Debug
    property bool debugMode: false

    signal settingChanged(string setting, var value)
    signal closeRequested()

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

                    // -- ACCESSIBILITY --
                    SettingsSection {
                        title: "Accessibility Profile"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 3

                            property string currentProfile: keyboard ? keyboard.getCurrentProfile() : "normal"

                            Repeater {
                                model: [
                                    { id: "precise",          label: "Precise" },
                                    { id: "normal",           label: "Normal" },
                                    { id: "mild_tremor",      label: "Mild Tremor" },
                                    { id: "moderate_tremor",  label: "Moderate Tremor" },
                                    { id: "severe_tremor",    label: "Severe Tremor" },
                                    { id: "limited_mobility", label: "Limited Mobility" }
                                ]

                                Item {
                                    Layout.fillWidth: true
                                    implicitHeight: 26

                                    Rectangle {
                                        anchors.fill: parent
                                        radius: 4
                                        color: profArea.containsMouse ? "#333" : "transparent"

                                        RowLayout {
                                            anchors.fill: parent
                                            anchors.leftMargin: 4
                                            anchors.rightMargin: 4

                                            Text {
                                                text: modelData.label
                                                color: "#c0c0c0"
                                                font.pixelSize: 12
                                                Layout.fillWidth: true
                                            }

                                            Rectangle {
                                                width: 16; height: 16; radius: 8
                                                border.color: parent.parent.parent.parent.currentProfile === modelData.id ? "#4a9eff" : "#666"
                                                border.width: 1.5
                                                color: "transparent"

                                                Rectangle {
                                                    anchors.centerIn: parent
                                                    width: 8; height: 8; radius: 4
                                                    color: "#4a9eff"
                                                    visible: parent.parent.parent.parent.parent.parent.currentProfile === modelData.id
                                                }
                                            }
                                        }

                                        MouseArea {
                                            id: profArea
                                            anchors.fill: parent
                                            hoverEnabled: true
                                            onClicked: {
                                                if (keyboard) {
                                                    keyboard.setAccessibilityProfile(modelData.id)
                                                    parent.parent.parent.currentProfile = modelData.id
                                                    unifiedSettings.settingChanged("accessibilityProfile", modelData.id)
                                                }
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
                                    { id: "business",     label: "Business" }
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
                        }
                    }

                    // -- THEME --
                    SettingsSection {
                        title: "Theme"
                        Layout.fillWidth: true

                        Row {
                            spacing: 8

                            Repeater {
                                model: [
                                    { name: "dark",   color: "#1a1a1a", accent: "#4a9eff" },
                                    { name: "light",  color: "#e8e8e8", accent: "#0078d4" },
                                    { name: "blue",   color: "#1a2a3a", accent: "#4a9eff" },
                                    { name: "green",  color: "#1a2a1a", accent: "#4aff4a" },
                                    { name: "purple", color: "#2a1a3a", accent: "#bb66ff" }
                                ]

                                Rectangle {
                                    width: 34
                                    height: 34
                                    radius: 7
                                    color: modelData.color
                                    border.color: unifiedSettings.currentTheme === modelData.name
                                                  ? modelData.accent : "#555"
                                    border.width: unifiedSettings.currentTheme === modelData.name ? 2 : 1

                                    Text {
                                        anchors.centerIn: parent
                                        text: unifiedSettings.currentTheme === modelData.name ? "\u2713" : ""
                                        color: modelData.name === "light" ? "#333" : "#fff"
                                        font.pixelSize: 14
                                        font.bold: true
                                    }

                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: unifiedSettings.settingChanged("theme", modelData.name)
                                    }
                                }
                            }
                        }
                    }

                    // -- DATA --
                    SettingsSection {
                        title: "Data"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            // Save model button
                            Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: 30
                                radius: 5
                                color: saveArea.containsMouse ? "#3a5a3a" : "#2a3a2a"
                                border.color: "#4a6a4a"

                                Text {
                                    anchors.centerIn: parent
                                    text: "Save Prediction Model"
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

                            // Clear user data button
                            Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: 30
                                radius: 5
                                color: clearArea.containsMouse ? "#5a2a2a" : "#3a2222"
                                border.color: "#6a4444"

                                Text {
                                    anchors.centerIn: parent
                                    text: "Clear Learned Data"
                                    color: "#ffaaaa"
                                    font.pixelSize: 12
                                }

                                MouseArea {
                                    id: clearArea
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: { if (keyboard) keyboard.clearUserData() }
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
