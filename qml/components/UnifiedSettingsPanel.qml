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

            // ── Header / drag handle ──────────────────────────────────────
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
                        text: "⚙ Settings"
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

            // ── Scrollable content ────────────────────────────────────────
            ScrollView {
                id: scrollArea
                Layout.fillWidth: true
                Layout.fillHeight: true
                // Don't clip here; the parent Rectangle clips the window edges
                clip: false
                // Fix content width so horizontal scroll never activates
                contentWidth: availableWidth

                ScrollBar.vertical: ScrollBar {
                    policy: ScrollBar.AlwaysOn
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
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                ColumnLayout {
                    id: contentColumn
                    width: scrollArea.availableWidth
                    spacing: 16

                    // ── LAYOUT ───────────────────────────────────────────
                    SettingsSection {
                        title: "Layout"
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

                    // ── THEME ────────────────────────────────────────────
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
                                        text: unifiedSettings.currentTheme === modelData.name ? "✓" : ""
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

                    // ── DEBUG ────────────────────────────────────────────
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
