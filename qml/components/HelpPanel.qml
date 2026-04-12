import QtQuick 2.15
import QtQuick.Layouts 1.15
import QtQuick.Controls 2.15
import QtQuick.Window 2.15

Item {
    id: helpPanel

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
                        text: "? Help"
                        color: "#fff"
                        font.pixelSize: 16
                        font.bold: true
                    }

                    Item { Layout.fillWidth: true }

                    Rectangle {
                        width: 26
                        height: 26
                        radius: 5
                        color: helpCloseArea.containsMouse ? "#5a2020" : "transparent"

                        Text {
                            anchors.centerIn: parent
                            text: "\u2715"
                            color: helpCloseArea.containsMouse ? "#ff6666" : "#888"
                            font.pixelSize: 14
                        }

                        MouseArea {
                            id: helpCloseArea
                            anchors.fill: parent
                            hoverEnabled: true
                            onClicked: helpPanel.closeRequested()
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
                id: helpFlick
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentWidth: width
                contentHeight: helpContent.implicitHeight
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
                    id: helpContent
                    width: helpFlick.width - 12
                    spacing: 16

                    // -- GETTING STARTED --
                    SettingsSection {
                        title: "Getting Started"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            HelpText { text: "Alpha-OSK is an AI-powered on-screen keyboard designed for accessibility. Click keys to type into any application." }
                            HelpText { text: "<b>Drag</b> the title bar to move the keyboard. <b>Drag edges</b> to resize. Keys scale automatically." }
                            HelpText { text: "Click <b>\u2699 Settings</b> to customize layout, theme, predictions, and accessibility." }
                        }
                    }

                    // -- WORD PREDICTIONS --
                    SettingsSection {
                        title: "Word Predictions"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            HelpText { text: "As you type, word suggestions appear above the keys. Click a suggestion to complete the word and auto-insert a space." }
                            HelpText { text: "Predictions use a <b>hybrid engine</b> combining:" }
                            HelpText { text: "  \u2022 <b>N-gram model</b> \u2014 learns which words and word pairs you use most" }
                            HelpText { text: "  \u2022 <b>PPM model</b> \u2014 character-level prediction (inspired by the Dasher project)" }
                            HelpText { text: "  \u2022 <b>Fuzzy recognition</b> \u2014 corrects nearby key presses based on spatial proximity" }
                            HelpText { text: "The more you type, the better predictions get. Toggle suggestions on/off in Settings > Suggestions." }
                            HelpText { text: "<b>Right-click</b> a suggestion to remove it from your vocabulary or mark it as a bad suggestion (shown less often)." }
                            HelpText { text: "<b>Auto-Capitalize</b> \u2014 optionally capitalizes the first letter after . ! ? (enable in Settings > Suggestions)." }
                        }
                    }

                    // -- ACCESSIBILITY PROFILES --
                    SettingsSection {
                        title: "Accessibility Profiles"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            HelpText { text: "Profiles adjust how forgiving the keyboard is with key targeting and corrections. Choose one in Settings > Accessibility Profile." }

                            HelpText { text: "<b>Precise</b> \u2014 Strict targeting, no autocorrect. For users with fine motor control who want exact input." }
                            HelpText { text: "<b>Normal</b> \u2014 Balanced accuracy with light autocorrect. Good default for most users." }
                            HelpText { text: "<b>Mild Tremor</b> \u2014 Slightly wider key targets, more forgiving of off-center presses, moderate autocorrect." }
                            HelpText { text: "<b>Moderate Tremor</b> \u2014 Wider key targets, stronger autocorrect, 200ms key hold delay to filter jitter." }
                            HelpText { text: "<b>Severe Tremor</b> \u2014 Widest key targets, aggressive autocorrect, 300ms hold delay. Maximum typing assistance." }
                            HelpText { text: "<b>Limited Mobility</b> \u2014 Wider targets for reduced range of motion, moderate hold delay, strong autocorrect." }

                            HelpText {
                                text: "<i>Each profile adjusts: spatial tolerance (how far off-center a press can be), autocorrect aggressiveness, prediction weighting, and key hold delay (to ignore tremor double-taps).</i>"
                                color: "#888"
                            }
                        }
                    }

                    // -- YOUR DATA --
                    SettingsSection {
                        title: "Your Data"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            HelpText { text: "<b>Settings</b> (layout, theme, toggles) save automatically whenever you change them. They persist across sessions." }
                            HelpText { text: "<b>Prediction model</b> (learned words and phrases) is separate. Use <b>Settings > Data > Save Prediction Model</b> to save it to disk, or enable <b>Auto-Save on Exit</b> so it saves when you close the keyboard." }
                            HelpText { text: "<b>Clear Learned Data</b> resets only your learned vocabulary \u2014 it does not affect settings or the base dictionary." }

                            HelpText {
                                text: "<b>Model location:</b>"
                            }
                            HelpText {
                                text: "  \u2022 Windows: <code>%APPDATA%\\alpha-osk\\models\\</code>"
                            }
                            HelpText {
                                text: "  \u2022 Linux: <code>~/.config/alpha-osk/models/</code>"
                            }
                            HelpText {
                                text: "Files: <code>ngram_model.json</code> (word frequencies, bigrams, blacklist) and <code>ppm_model.json</code> (character patterns)"
                                color: "#888"
                            }
                            HelpText { text: "<b>Analytics</b> (keystrokes saved, words typed, quality score) also persist across sessions in <code>analytics.json</code> in the same config directory." }
                        }
                    }

                    // -- KEYBOARD SHORTCUTS --
                    SettingsSection {
                        title: "Keyboard Features"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            HelpText { text: "<b>Modifier keys</b> (Shift, Ctrl, Alt, Win) are sticky \u2014 click once to activate, click again to deactivate. Combine with other keys for shortcuts like Ctrl+C." }
                            HelpText { text: "<b>Hold a key</b> to repeat it (works for letters, backspace, arrows, etc.)." }
                            HelpText { text: "<b>Smart punctuation</b> \u2014 typing . ! ? , ; : after a space automatically removes the extra space before the punctuation." }
                            HelpText { text: "<b>Optional panels</b> \u2014 enable Function Row (F1\u2013F12), Navigation keys, or Numpad in Settings > Layout." }
                        }
                    }

                    // -- VOCABULARY PACKS --
                    SettingsSection {
                        title: "Vocabulary Packs"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            HelpText { text: "Enable domain-specific vocabulary in Settings to boost predictions for specialized fields:" }
                            HelpText { text: "  \u2022 <b>Medical</b> \u2014 clinical terms, medications, anatomy" }
                            HelpText { text: "  \u2022 <b>Programming</b> \u2014 code keywords, API terms" }
                            HelpText { text: "  \u2022 <b>Academic</b> \u2014 research and scholarly language" }
                            HelpText { text: "  \u2022 <b>Gaming</b> \u2014 gaming terminology" }
                            HelpText { text: "  \u2022 <b>Business</b> \u2014 corporate and financial terms" }

                            HelpText { text: "<b>Built-in packs</b> are stored in the <code>data/packs/</code> folder inside the application directory." }

                            HelpText { text: "<b>Custom packs</b> are stored in your user data directory:" }
                            HelpText { text: "  \u2022 Windows: <code>%APPDATA%\\alpha-osk\\packs\\</code>" }
                            HelpText { text: "  \u2022 Linux: <code>~/.config/alpha-osk/packs/</code>" }

                            HelpText { text: "<b>To create a custom pack:</b>" }
                            HelpText { text: "1. Create a folder with the pack name (e.g. <code>my_pack/</code>)" }
                            HelpText { text: "2. Add a <code>dictionary.txt</code> file \u2014 one word per line, lowercase" }
                            HelpText { text: "3. Optionally add <code>bigrams.txt</code> \u2014 two words per line (e.g. <code>machine learning</code>)" }
                            HelpText { text: "4. Optionally add <code>trigrams.txt</code> \u2014 three words per line" }
                            HelpText { text: "5. Optionally add <code>pack.json</code> with name and description:" }
                            HelpText {
                                text: "<code>{\"name\": \"My Pack\", \"description\": \"Custom vocabulary\", \"version\": 1}</code>"
                                color: "#888"
                            }
                            HelpText { text: "6. Use <b>Settings > Vocabulary Packs > Import Custom Pack</b> to import the folder, or copy it directly into the packs directory above." }
                        }
                    }

                    // Bottom spacer
                    Item { height: 4 }
                }
            }
        }
    }

    // Reusable help text component
    component HelpText: Text {
        Layout.fillWidth: true
        color: "#c0c0c0"
        font.pixelSize: 12
        wrapMode: Text.WordWrap
        textFormat: Text.RichText
        lineHeight: 1.3
    }
}
