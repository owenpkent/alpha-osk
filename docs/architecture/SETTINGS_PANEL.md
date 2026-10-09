# Settings panel structure

The full write-up behind the CLAUDE.md section *Settings Panel Structure*, including the table of where each setting lives.

## Settings Panel Structure

`UnifiedSettingsPanel.qml` is a drill-down menu, not a long scrolling list. The home view shows six category cards; clicking a card swaps the body to that category's sub-view. The header swaps in a back arrow (<) and the category title; the close X stays put.

State is held in a single string property: `currentView` is one of {`"home"`, `"appearance"`, `"typing"`, `"fkeys"`, `"dictation"`, `"model"`, `"data"`}. The Flickable contains seven sibling `ColumnLayout`s, each with `visible: unifiedSettings.currentView === "<id>"`; only one renders at a time. Scroll position is reset to the top on every view change (a `Connections` block on `currentView`) so a drilled-in view never opens mid-section.

The parent (`Main.qml`'s settings popup window) calls `settingsPanel.resetToHome()` in `onVisibleChanged` so re-opening Settings always lands on the home grid, not whatever sub-page the user last visited. Don't break that - landing on a deep page reads as "the menu changed."

**Where it opens is `root.safePanelPos(w, h)`, shared with Help and the Dashboard.** All three used to open at `Screen.width / 2 - width / 2`, which gets three things wrong at once: it centres on the **primary** screen whatever screen the keyboard is on (a monitor to the left has negative coordinates a primary-screen centre cannot even reach, the same bug the snippets restore documents one window over); it can land on top of the keyboard, which is what the user types into these windows with; and none of the three has an OS title bar to drag it back by, while Settings cannot take focus either, so a window that opens somewhere unreachable stays unreachable. `safePanelPos` puts the panel above the keyboard where there is room, below it where there is not, centred on the keyboard's own screen when it fits neither, and clamped on that screen in every case; `screenBoundsAt(px, py)` is the screen lookup, walking `Qt.application.screens` because `Screen` inside a Window is not knowable before the window is placed and `Screen.width` is a size rather than a position. Guarded by `tests/test_qml_panel_placement.py`, which cannot exercise the multi-monitor half (the offscreen plugin gives one screen) and so pins the half a single screen can prove: that the position is derived from the keyboard's geometry rather than the screen's centre, which is exactly the property the old code lacked.

**The one exception is `root.settingsReturnView`, and it is a return rather than a re-open.** Tapping a key in *Function Keys* hides the settings window and opens the key editor, which lives on the **keyboard** window because it is typed into with the OSK's own keys and the settings window cannot hold OS focus (the Deepgram key field carries the same note). Leaving a 360x540 window parked mid-screen would cover the editor, the letter grid it is typed with, or both. `settingsWindow.onVisibleChanged` consumes `settingsReturnView` when it is set and calls `resetToHome()` otherwise, so only that hand-off lands deep; coming back to the home grid there would lose the user's place in a list of twenty-four. Guarded by `tests/test_qml_function_row.py::TestTheSettingsListIsTheLeftClickRoute`, whose inverse half asserts an editor opened any other way does **not** pop the settings window open behind it.

### Where each section lives

| Top-level | Section | What's inside |
|-----------|---------|---------------|
| **Appearance** | Panels | Compact View / Navigation / Numpad toggles. The two function-row toggles are deliberately **not** here: they moved to the Function Keys category, which owns the whole feature (showing a row and deciding what is on it are one job). Compact View leads the section because it gates the two below it: it forces Navigation + Numpad off (restoring them on exit) and renders their toggles disabled. There is no Number Row toggle - `Main.qml::showNumberRow` derives from whether the active layout JSON already carries a `number` row, so the standalone panel appears exactly on the compact layouts, which lack one. |
| | Keyboard Layout | qwerty / dvorak / colemak picker (compact variants are filtered out - see *Compact View*) |
| | Theme | 9-theme color picker |
| | Key Colours | Six-scheme picker, **Monochrome by default** (Default / Monochrome / Two-Tone / Function / Ink / Signal). Directly under Theme because every colour it offers is derived from the theme |
| | Sound & Opacity | Key click sound, opacity slider |
| | Window | Snap to Screen Edges (magnetic edges while the window is being moved, default ON) and Always on Top (default ON; off lets other apps cover the keyboard, see *Title-bar window menu*). Its own section because placement is neither a panel, a layout, a theme nor a sound, and the alternative was hiding a window-behaviour toggle under "Sound & Opacity" |
| **Smart Typing** | Suggestions | Show suggestions, auto-space, intelligent spacing, auto-cap, max count, filter explicit words |
| | Suggestion Engine | Merge strategy 4-card picker (rank / rrf / linear / loglinear) |
| | Input | Right-click shift, key preview popup, Compatibility Mode picker, repeat delay & interval |
| **Function Keys** | Show | Function Keys (F1-F12) and Extra Function Keys (F13-F24) row toggles, moved here from Appearance -> Panels |
| | F13-F24 | One row per key: the label on its cap, a one-line description of what tapping it does, and a tap to program it. Listed **before** F1-F12 because these are the keys the feature is for, and scrolling past twelve keys nobody should reassign on every visit is the wrong default |
| | F1-F12 | The same list for the standard keys, under a note that reassigning F5 costs the user refresh in every app |
| **Dictation** | Voice Input | Enable Dictation, Type As You Speak |
| | Transcription Service | Deepgram API key, model, language |
| | Microphone | Input device picker |
| | Stop Listening | Silence timeout, maximum run length |
| | Custom Words | Boosted vocabulary (Deepgram `keyterm`) |
| **Your Language Model** | (top button) | Open Dashboard -> opens ModelVisualization |
| | Vocabulary Packs | Toggles for any imported packs + Import Custom Pack (no built-ins ship) |
| | Prediction Model | Auto-save toggle, Save Now, Clear Learned Data |
| **Data & Privacy** | (top button) | Help & Shortcuts |
| | Data Backup | Export / Import, with a preview before an import replaces anything (see *Data Backup*) |
| | Privacy | Snippet auto-detection opt-out, telemetry opt-in + Delete contributed data |
| | Updates | Installed version, auto-check toggle, Check Now |
| | Diagnostics | Open Log Folder, Copy Path (see *Where User Data Lives*) |
| | Developer | Debug Mode |

Old labels and their new homes (for backwards-compat references in code comments / docs you might see): the standalone "Layout" section was renamed to "Panels" (the parent category is "Appearance", reusing the name was confusing); the standalone "Appearance" section was renamed to "Sound & Opacity" for the same reason; the old "Tools" section was split - its **Help & Shortcuts** button is now a standalone tile at the top of Data & Privacy, and its **Your Language Model** button moved to be the top-of-page tile in the Your Language Model view.

### Adding a New Setting

**Step 0: does it hold a secret or a device identity?** If so it does not belong in `Settings {}` at all. Qt's settings layer is a registry key on Windows, it is not covered by the size caps and validation every other loader here has, and it is the wrong place for a credential. Dictation is the worked example: only `savedDictationEnabled` lives in `appSettings`, because QML has to decide whether to reserve the suggestion bar's left edge before it has asked the bridge anything, and everything else (key, model, language, device, timeouts, custom words) lives in `dictation.json` behind `DictationConfig`, read back through `Main.qml`'s `refreshDictation()`. Splitting a record across two stores is how the halves drift, so split only on that boundary and say why. The eight steps below are for an ordinary setting.

1. Add `property bool savedFoo: defaultValue` to `Settings {}` in `Main.qml`
2. Add `property bool foo: appSettings.savedFoo` to root in `Main.qml`
3. Add `property bool foo: defaultValue` to `UnifiedSettingsPanel.qml`
4. Add `SettingsToggle` to the **right sub-view** in `UnifiedSettingsPanel.qml` - pick the category from the table above. Toggles go inside an existing `SettingsSection` block; if no section fits, add a new `SettingsSection { title: "..." }` to that view.
5. Pass property through: `foo: root.foo` in the `Comp.UnifiedSettingsPanel {}` block
6. Handle in `onSettingChanged`: update root, save to appSettings, call bridge if needed
7. If Python needs it: add `@Slot(bool) def setFoo()` to `keyboard_bridge.py`
8. Load on startup in `Component.onCompleted` if it needs to be sent to the bridge

If you can't decide which category a new setting belongs to, that's a sign the UX is fuzzy - push back on the requirement before adding the setting.


