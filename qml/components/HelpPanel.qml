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
                        text: "Help & Shortcuts"
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
                            text: "✕"
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
                    // Drive visibility off an explicit overflow check.
                    // ScrollBar.AsNeeded shows the bar when contentHeight
                    // equals height in Qt 6, which reads as a bug here.
                    policy: helpFlick.contentHeight > helpFlick.height + 1
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
                            HelpText { text: "<b>Drag</b> the title bar to move the keyboard. <b>Drag the left or right edge</b> to resize. Keys scale automatically, height follows content." }
                            HelpText { text: "Click <b>⚙</b> in the title bar to open Settings: Appearance, Smart Typing, Your Language Model, and Data & Privacy." }
                        }
                    }

                    // -- TITLE BAR --
                    SettingsSection {
                        title: "Title Bar"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            HelpText { text: "<b>Learning / Paused</b> · Privacy mode. When it reads <b>Paused</b>, the keyboard still types but won't learn from what you type or show predictions. Auto-flips to Paused when a password field has focus (Windows + Linux with AT-SPI)." }
                            HelpText { text: "<b>↻</b> · Clear the prediction context. Useful when you switch tabs inside one app and pills still reflect the last sentence." }
                            HelpText { text: "<b>⚙</b> · Settings." }
                            HelpText { text: "<b>↓</b> · Appears only when an update is pending. Click to install. The keyboard restarts itself after the new version is in place." }
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
                            HelpText { text: "  • <b>N-gram model</b> · learns which words and word pairs you use most" }
                            HelpText { text: "  • <b>PPM model</b> · character-level prediction (inspired by the Dasher project)" }
                            HelpText { text: "  • <b>Fuzzy recognition</b> · corrects nearby key presses based on spatial proximity, tuned with one generous default" }
                            HelpText { text: "The more you type, the better predictions get. Toggle suggestions on or off in <b>Settings → Smart Typing → Suggestions</b>." }
                            HelpText { text: "<b>Right-click a suggestion</b> to <b>Show more</b> (boost it), <b>Show less</b> (downweight), <b>Remove</b> (blacklist), or <b>Edit</b> (fix casing or spelling, e.g. <code>iphone</code> → <code>iPhone</code>). Removed words come back automatically if you type them three times manually." }
                            HelpText { text: "Only <b>I</b>, <b>I'm</b>, <b>I'll</b>, <b>I'd</b>, <b>I've</b> auto-capitalize. Everything else follows what you typed · use Shift or Caps Lock to capitalize anything else." }
                        }
                    }

                    // -- KEYBOARD FEATURES --
                    SettingsSection {
                        title: "Keyboard Features"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            HelpText { text: "<b>Modifier keys</b> (Shift, Ctrl, Alt, Win) are <b>sticky</b>. Click once to activate, click again to deactivate. They auto-release after one keystroke (except Caps Lock, which stays until you toggle it off)." }
                            HelpText { text: "<b>Shift + click</b> and <b>Ctrl + click</b> work too · activate the modifier, then click in the target app to extend selection, open a link, etc." }
                            HelpText { text: "<b>Caps Lock and Shift are independent.</b> Toggling Caps does not also flip Shift. Caps uppercases letters; Shift also picks shifted glyphs (<code>!</code> on <code>1</code>)." }
                            HelpText { text: "<b>Right-click a character key</b> to type its shifted variant once without flipping sticky Shift. Letters become uppercase, symbols use their shifted glyph (<code>1</code> → <code>!</code>, <code>,</code> → <code>&lt;</code>). Toggle in <b>Settings → Smart Typing → Input</b>." }
                            HelpText { text: "<b>Hold to repeat</b> on Backspace, Delete, arrow keys, and Page Up / Page Down. Character keys do not repeat · a slow click won't accidentally produce two letters." }
                            HelpText { text: "<b>Optional panels</b> · enable Function Row (F1–F12), Navigation keys, or Numpad in <b>Settings → Appearance → Panels</b>." }
                            HelpText { text: "<b>Layouts</b> · QWERTY, Dvorak, Colemak in <b>Settings → Appearance → Keyboard Layout</b>." }
                            HelpText { text: "<b>9 themes</b> in <b>Settings → Appearance → Theme</b> · Dark, Light, Ocean, Forest, Amethyst, Vaporwave, Blackboard, Typewriter, Spaceship." }
                        }
                    }

                    // -- COMPATIBILITY MODE --
                    SettingsSection {
                        title: "Compatibility Mode"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            HelpText { text: "Some apps interfere with the normal prediction-insertion path: remote-desktop clients (TeamViewer, RDP, VNC, AnyDesk) drop and reorder keystrokes, and IDEs (VS Code, Cursor, Windsurf, the JetBrains family) intercept keys for autocomplete and snippets." }
                            HelpText { text: "Compatibility Mode rewires prediction clicks to BackSpace + retype the full word, which survives those pipelines. <b>Auto-detect is on by default</b> · it switches on automatically when one of those apps is focused. Force it always-on for any app in <b>Settings → Smart Typing → Input → Compatibility Mode</b>." }
                        }
                    }

                    // -- SWIPE TYPING --
                    SettingsSection {
                        title: "Swipe Typing"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            HelpText { text: "Drag the mouse across letters to type a whole word in one gesture (like Gboard). Press, drag past about 60 pixels, release. Alternates appear in the prediction bar so you can pick a different word." }
                            HelpText { text: "Off by default. Enable in <b>Settings → Smart Typing → Suggestions → Swipe Typing</b>." }
                            HelpText { text: "Tap-through still works · a normal click on a key types that key as usual." }
                        }
                    }

                    // -- ANALYTICS & DASHBOARD --
                    SettingsSection {
                        title: "Analytics & Dashboard"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            HelpText { text: "Open <b>Settings → Your Language Model → Open Dashboard</b> to see:" }
                            HelpText { text: "  • <b>Word Cloud</b> · your most-used words sized by frequency. Click any bubble to drill in." }
                            HelpText { text: "  • <b>Word Flow</b> · network graph of word → word transitions. Click a node to see predecessors, successors, and trigram windows." }
                            HelpText { text: "  • <b>Dashboard</b> · lifetime/session stats (keystrokes saved, time saved, effort saved, acceptance), top words, and clickable tags for boosted, blocked, and downweighted words · click any tag to roll the adjustment back." }
                        }
                    }

                    // -- YOUR DATA --
                    SettingsSection {
                        title: "Your Data"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            HelpText { text: "<b>Settings</b> (layout, theme, toggles) save automatically whenever you change them." }
                            HelpText { text: "<b>Prediction model</b> (learned words and phrases) saves on quit if Auto-Save is on, or via <b>Settings → Your Language Model → Prediction Model → Save Now</b>. <b>Clear Learned Data</b> resets your learned vocabulary without touching settings or the base dictionary." }
                            HelpText {
                                text: "<b>Model location:</b>"
                            }
                            HelpText {
                                text: "  • Windows: <code>%APPDATA%\\alpha-osk\\models\\</code>"
                            }
                            HelpText {
                                text: "  • Linux: <code>~/.config/alpha-osk/models/</code>"
                            }
                            HelpText {
                                text: "Files: <code>ngram_model.json</code> (word frequencies, bigrams, blacklist, boosts) and <code>ppm_model.json</code> (character patterns). Analytics live next to them in <code>analytics.json</code>."
                                color: "#888"
                            }
                            HelpText { text: "<b>Telemetry is off by default.</b> Opt in at <b>Settings → Data & Privacy → Privacy</b> to share nine anonymous integers (no content, no word lists) once a week. <b>Delete my contributed data</b> in the same panel removes anything you've sent." }
                        }
                    }

                    // -- VOCABULARY PACKS --
                    SettingsSection {
                        title: "Vocabulary Packs"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            HelpText { text: "Vocabulary packs seed predictions for a domain (medical terms, programming keywords, etc.). <b>No packs ship by default</b> · personal learning catches up fast, and curated 300-word lists weren't worth the upkeep." }
                            HelpText { text: "<b>To import a pack:</b> use <b>Settings → Your Language Model → Vocabulary Packs → Import Custom Pack</b>." }
                            HelpText { text: "<b>Pack format</b> · a folder containing:" }
                            HelpText { text: "  • <code>dictionary.txt</code> (required) · one word per line, lowercase" }
                            HelpText { text: "  • <code>bigrams.txt</code> (optional) · two words per line" }
                            HelpText { text: "  • <code>trigrams.txt</code> (optional) · three words per line" }
                            HelpText { text: "  • <code>pack.json</code> (optional, auto-generated on import) · <code>{\"name\": \"…\", \"description\": \"…\", \"version\": 1}</code>" }
                            HelpText { text: "Imported packs are stored at:" }
                            HelpText { text: "  • Windows: <code>%APPDATA%\\alpha-osk\\packs\\</code>" }
                            HelpText { text: "  • Linux: <code>~/.config/alpha-osk/packs/</code>" }
                        }
                    }

                    // -- UAC AND ADMIN PROMPTS --
                    SettingsSection {
                        title: "UAC and Admin Prompts"
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            HelpText { text: "When Windows asks for an administrator password (a UAC prompt), Alpha-OSK doesn't appear by default. Only Microsoft's built-in on-screen keyboard does." }
                            HelpText { text: "UAC prompts run on the Windows <b>Secure Desktop</b>, an isolated session that only allows specific Microsoft-signed processes. No third-party app, no matter how it's signed, can appear there. This is a security feature · it stops malware from faking the prompt or stealing your password." }
                            HelpText { text: "<b>Workaround:</b> you can tell Windows to put UAC prompts on the regular desktop instead. Once it's there, Alpha-OSK can type into it normally." }
                            HelpText { text: "<b>How to enable:</b>" }
                            HelpText { text: "1. Press <b>Win + R</b>, type <code>secpol.msc</code>, and press Enter (requires admin)." }
                            HelpText { text: "2. Go to <b>Local Policies → Security Options</b>." }
                            HelpText { text: "3. Find <b>\"User Account Control: Switch to the secure desktop when prompting for elevation\"</b> and set it to <b>Disabled</b>." }
                            HelpText { text: "4. Reboot." }

                            HelpText {
                                text: "<b>Trade-off:</b> with the Secure Desktop disabled, any program running as you could in theory see or interact with a UAC prompt. Only do this if you understand and accept that risk."
                                color: "#cc9966"
                            }
                            HelpText { text: "<b>Login screen, lock screen, and Ctrl+Alt+Del</b> are always on the Secure Desktop and there's no override. Use Windows' built-in on-screen keyboard from the Ease of Access menu (the wheelchair icon) for those." }
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
