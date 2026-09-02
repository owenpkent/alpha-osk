import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Window 2.15

// Snippets popup: the user's saved quick text (name, email, phone,
// address, canned phrases). Tapping a tile copies it to the
// clipboard -- see the "Three views share the window" comment
// below for the grid / actions sheet / editor split, and
// KeyboardBridge.copySnippet for why a tap copies rather than
// types.
//
// This is a SEPARATE top-level Window, not a Popup. A Popup is
// clipped to its parent window's overlay, so it can't be dragged
// outside the keyboard. A standalone Window can float anywhere on
// the desktop. It carries the same OSK window flags as the main
// window (frameless, stays-on-top, does-not-accept-focus) so it
// never steals focus from the app the user is typing into; the
// Python side applies WS_EX_NOACTIVATE to it too (see
// _apply_window_flags / the snippetsWindowReady signal). The
// header is a drag handle.
Window {
    id: snippetsWindow

    // The only things this window needs from the keyboard window:
    // required properties bound in Main.qml, and signals so the
    // toasts (which must stay on the keyboard window, see below) can
    // still fire from here.
    required property var applyEditChord
    required property var clampedWindowPos
    required property var inkOn
    required property var luminance
    required property bool shiftOn
    required property color themeAccent
    required property color themeBackground
    required property color themeBorder
    required property color themeKeyColor
    required property color themeKeyPressed
    required property color themeTextColor
    required property real keyboardWidth
    required property real keyboardX
    required property real keyboardY
    required property var settings

    signal copied(string label)
    signal problem(string message)
    signal saved()

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

    // Three views share the window, in priority order: the editor
    // (editingIndex >= 0), the per-snippet actions sheet
    // (menuIndex >= 0), otherwise the tile grid.
    //
    // The actions sheet is a *view*, not a floating menu, and that
    // is deliberate. A popup anchored to a 165 px tile inside a
    // 360 px window has to be clamped away from two edges, and it
    // puts every management action on a target smaller than the
    // tile it came from. Taking over the window instead gives each
    // action the full width, which is the whole reason this
    // keyboard exists.
    property int editingIndex: -1
    property int menuIndex: -1
    property bool confirmingDelete: false
    // Which editor field OSK keys flow to while editing.
    property string editTarget: "value"
    property var snippetList: []

    // Grid paging. Six tiles (3 rows x 2) per page keeps the window
    // a fixed height no matter how many snippets exist, which
    // matters more here than on an ordinary list: this window
    // floats over whatever the user is typing into, so a list that
    // grew downward would eventually cover the target app, and at
    // the 50-snippet cap it would run off the screen entirely.
    property int page: 0
    readonly property int pageSize: 6
    readonly property int pageCount: Math.max(1, Math.ceil(snippetList.length / pageSize))
    readonly property int snippetLimit: keyboard ? keyboard.getSnippetLimit() : 50
    // Derived from the actual layout properties rather than
    // restated as literals: it used to hardcode 24 (2x
    // snipContent's 12 px anchors.margins) and 6 (the Grid's own
    // spacing), so the tiles sat flush only because those numbers
    // happened to still agree with the properties they were
    // copied from. A future margin or spacing change would have
    // overhung the window or left a gap, silently.
    readonly property real cellW:
        (width - 2 * snipContent.anchors.margins
         - (snipGrid.columns - 1) * snipGrid.spacing) / snipGrid.columns

    readonly property var menuSnip: menuIndex >= 0 ? snippetList[menuIndex] : undefined
    readonly property bool menuHasValue:
        menuSnip !== undefined && menuSnip.value && menuSnip.value.length > 0
    readonly property bool canMoveEarlier: menuIndex > 0
    readonly property bool canMoveLater: menuIndex >= 0 && menuIndex < snippetList.length - 1

    // Theme-derived chrome. Everything in this window used to be
    // hardcoded blues, reds and greens, which read as foreign on
    // the dark themes and broke outright on Typewriter (a light
    // theme with near-black text).
    readonly property color surface: themeKeyColor
    readonly property color surfaceHi: themeKeyPressed
    readonly property color txt: themeTextColor
    readonly property color muted: Qt.rgba(txt.r, txt.g, txt.b, 0.58)
    readonly property color faint: Qt.rgba(txt.r, txt.g, txt.b, 0.42)
    readonly property bool lightTheme: luminance(themeBackground) > 0.5
    // One destructive colour, picked per theme rather than fixed:
    // a dark red is illegible on Typewriter's cream and a bright
    // one glares on Spaceship's near-black.
    readonly property color danger: lightTheme ? "#a3271c" : "#ef8b80"

    // Tag name -> ink. The names come from the store's allow-list
    // (getSnippetColors), the hexes live here, because they have to
    // stay readable on all nine themes and the store has no idea
    // what is behind them. Mid-tone and mildly desaturated for that
    // reason: a fully saturated tag disappears into Vaporwave and
    // burns out on Typewriter.
    readonly property var tagInks: ({
        "":       "transparent",
        "red":    "#e0574f",
        "amber":  "#d3902c",
        "green":  "#4fa855",
        "blue":   "#4a9eff",
        "purple": "#a274ef"
    })
    // The untagged default is grey, and the grey is the theme's own
    // key colour on the tile (nothing is drawn over it). The swatch
    // needs an actual fill though, so it reads as "grey" rather than
    // as a hole in the row.
    readonly property color defaultTagInk: "#8b9098"
    property var tagNames: keyboard ? keyboard.getSnippetColors() : [""]

    function tagColor(name) {
        var c = tagInks[name === undefined || name === null ? "" : name]
        return c ? c : "transparent"
    }

    function clampPage() {
        if (page > pageCount - 1) page = pageCount - 1
        if (page < 0) page = 0
    }

    function refresh() {
        snippetList = keyboard ? keyboard.getSnippets() : []
        clampPage()
    }

    // Identity of the snippet the sheet (or the editor) is about,
    // so a list replaced underneath it can be noticed. A Data
    // Backup import swaps the whole list while the sheet is open,
    // and an index is not an identity: staying on it silently
    // retargets Delete at whatever landed there, and an index past
    // the new end opens a blank editor whose save is a no-op while
    // the toast still says "Saved". Label and value only, so a
    // colour set from the sheet itself does not read as a
    // different snippet.
    function identityOf(idx) {
        var s = snippetList[idx]
        return s ? s.label + "\u0000" + s.value : ""
    }
    property string menuIdentity: ""
    property string editIdentity: ""

    function openMenu(idx) {
        confirmingDelete = false
        menuIndex = idx
        menuIdentity = identityOf(idx)
    }

    function closeMenu() {
        confirmingDelete = false
        menuIndex = -1
        menuIdentity = ""
    }

    // Left-click on a tile: copy it to the clipboard, or open the
    // editor when the slot is still empty so a seeded slot is never
    // a dead tap.
    //
    // Copy rather than type, and typing is not offered anywhere in
    // this window (the sheet carries Copy / Edit / colours / Move /
    // Delete and no Type row). Typing is one click against a
    // paste's two, but it only lands correctly when the caret is
    // already in the right field and the app does not intercept
    // synthetic keystrokes. The clipboard has no focus race, and a
    // toast is proof it worked: an insert into the wrong window is
    // silent and invisible.
    // Which button did what. Kept out of the delegate so it can be
    // driven directly by the headless tests: every other path in
    // this window is a named function they can call, and this one
    // -- the interaction the window is built around -- was the only
    // logic reachable solely through a real mouse event.
    function tileClicked(idx, button) {
        if (button === Qt.RightButton || manageMode)
            openMenu(idx)
        else
            primaryTap(idx)
    }

    // Left-click-only route to the management actions.
    //
    // Right-click opens the actions sheet and press-and-hold
    // deliberately does not (see the tile's MouseArea), so without
    // this a pointer that can only left-click could copy a snippet
    // and nothing else: never edit, recolour, reorder or delete
    // one, and never free a slot once at the 50 cap. Dwell-click,
    // switch access and head/eye trackers are exactly the software
    // this keyboard is run alongside, and the previous list at
    // least put a pencil and a cross on every row.
    //
    // A *mode* rather than a per-tile control, for the reason the
    // grid exists at all: a second target on a 165x58 tile sits a
    // few pixels from the one pressed every day, which is the
    // arrangement this window was rebuilt to remove. In manage
    // mode the whole tile is the target and copy is unreachable,
    // so the worst a mis-tap can do is open a sheet.
    property bool manageMode: false

    function toggleManage() {
        manageMode = !manageMode
    }

    function primaryTap(idx) {
        var s = snippetList[idx]
        if (s && s.value && s.value.length > 0) {
            if (keyboard && keyboard.copySnippet(idx)) {
                copied(s.label)
                snippetsWindow.hide()
            } else {
                // A clipboard write is invisible, so the toast is
                // the only evidence it happened -- which makes the
                // failure branch the one that most needs its own.
                // Silently doing nothing here is indistinguishable
                // from a tap that did not register, so the user
                // taps again and again.
                problem(qsTr("Could not copy to the clipboard"))
            }
        } else {
            beginEdit(idx)
        }
    }

    function moveSnippet(dir) {
        if (menuIndex < 0 || !keyboard) return
        var from = menuIndex
        var target = from + dir
        if (target < 0 || target >= snippetList.length) return
        // Follow the snippet rather than the slot: the sheet is
        // about one snippet, and staying put would silently retarget
        // it at whichever one swapped into this index. Pointed at
        // the destination *before* the mutation, because
        // moveSnippet emits snippetsChanged synchronously and the
        // handler reads menuIndex to check the sheet is still on
        // the snippet it opened for.
        menuIndex = target
        page = Math.floor(target / pageSize)
        keyboard.moveSnippet(from, dir)
    }

    function activeField() {
        return editTarget === "label" ? snipLabelField : snipValueField
    }

    // Tab between the two fields, selecting what is there.  Selecting
    // matches every other form on this OS, and it earns its keep here
    // specifically: replacing a value wholesale is the common edit,
    // and the alternative is holding Backspace over an address.
    // `onEditKeyTyped` already replaces a selection on the next
    // character, so nothing else has to know about this.
    function focusOtherField() {
        editTarget = (editTarget === "label") ? "value" : "label"
        var f = activeField()
        f.forceActiveFocus()
        f.selectAll()
    }

    // Arrow / Home / End with Shift held extends the selection
    // instead of dropping it, the way it does in any text box.  The
    // caller passes where the caret is going; whether that is a move
    // or a selection is this one decision, made once.
    //
    // `shiftOn` is still true here: the bridge's edit-mode
    // intercept emits and returns before the auto-release block that
    // ordinarily clears a sticky modifier after a keystroke.
    function moveCaret(f, to) {
        if (shiftOn) f.moveCursorSelection(to)
        else f.cursorPosition = to
    }

    function openList() {
        editingIndex = -1
        closeMenu()
        page = 0
        // Copying is what this window is for, so every open starts
        // there; manage mode is an errand, not a preference.
        manageMode = false
        if (keyboard) keyboard.setEditMode(false)
        refresh()
        // Restore where the user last left it, clamped back
        // on-screen in case the display layout changed since. Only
        // the first open of a session positions the window; after
        // that x/y persist with the object.
        if (!_positioned) {
            if (settings.savedSnippetsX > -1000000
                    && settings.savedSnippetsY > -1000000) {
                var pos = clampedWindowPos(settings.savedSnippetsX,
                                            settings.savedSnippetsY,
                                            snippetsWindow.width,
                                            snippetsWindow.height)
                snippetsWindow.x = pos.x
                snippetsWindow.y = pos.y
            } else {
                // Centred just above the keyboard on a fresh install.
                snippetsWindow.x = keyboardX + (keyboardWidth - snippetsWindow.width) / 2
                snippetsWindow.y = Math.max(0, keyboardY - snippetsWindow.height - 8)
            }
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
        // Saving returns to the grid, not to the sheet the user may
        // have come from: the edit is the errand, and one Back is
        // less work than two.
        closeMenu()
        editingIndex = idx
        editIdentity = identityOf(idx)
        if (keyboard) keyboard.setEditMode(true)
        snipValueField.forceActiveFocus()
    }

    function endEdit() {
        if (keyboard) keyboard.setEditMode(false)
        editingIndex = -1
        editIdentity = ""
    }

    function saveEdit() {
        if (editingIndex >= 0 && keyboard) {
            // Only claim it saved if it did. SnippetStore.set
            // refuses an out-of-range index and reports it by
            // returning False, and this editor is reachable from
            // the sheet, whose index an import can invalidate --
            // so the flash was capable of confirming a write that
            // never happened, the exact failure acceptSnippetOffer
            // was given a bool return for.
            if (keyboard.setSnippet(editingIndex, snipLabelField.text.trim(),
                                    snipValueField.text))
                saved()
            else
                problem(qsTr("Could not save that snippet"))
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
            // Deleting the only snippet on the last page would
            // otherwise strand the grid on a page that no longer
            // exists, showing six empty cells and a pager that
            // counts past its own end.
            snippetsWindow.clampPage()
            // The sheet and the editor are each about one snippet.
            // If the list was replaced underneath them (a Data
            // Backup import is the case that bites) the index they
            // hold now points at a different snippet, or past the
            // end. Close rather than act on the wrong one.
            if (snippetsWindow.menuIndex >= 0
                    && snippetsWindow.identityOf(snippetsWindow.menuIndex)
                       !== snippetsWindow.menuIdentity)
                snippetsWindow.closeMenu()
            if (snippetsWindow.editingIndex >= 0
                    && snippetsWindow.identityOf(snippetsWindow.editingIndex)
                       !== snippetsWindow.editIdentity)
                snippetsWindow.endEdit()
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
            if (applyEditChord(f, name)) return
            var pos = f.cursorPosition
            var len = f.length
            if (name === "backspace") {
                if (f.selectedText) f.remove(f.selectionStart, f.selectionEnd)
                else if (pos > 0) f.remove(pos - 1, pos)
            } else if (name === "delete") {
                if (f.selectedText) f.remove(f.selectionStart, f.selectionEnd)
                else if (pos < len) f.remove(pos, pos + 1)
            } else if (name === "tab") {
                // Tab moves between the two fields. Without this it
                // did nothing at all, which on a keyboard whose
                // window cannot hold OS focus means there was no
                // key-driven way to change field: the only route was
                // landing a click on the other box.
                snippetsWindow.focusOtherField()
            } else if (name === "left") {
                snippetsWindow.moveCaret(f, Math.max(0, pos - 1))
            } else if (name === "right") {
                snippetsWindow.moveCaret(f, Math.min(len, pos + 1))
            } else if (name === "home") {
                snippetsWindow.moveCaret(f, 0)
            } else if (name === "end") {
                snippetsWindow.moveCaret(f, len)
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
        color: themeBackground
        border.color: themeAccent
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

            // Back out of the editor / actions sheet. Absent in the
            // grid, where there is nothing to go back to. It sits
            // outside the drag Item on purpose: the drag MouseArea
            // fills that Item and would swallow the clicks.
            Rectangle {
                visible: snippetsWindow.editingIndex >= 0 || snippetsWindow.menuIndex >= 0
                Layout.preferredWidth: 28
                Layout.preferredHeight: 28
                radius: 4
                color: snipBackMa.containsMouse
                       ? snippetsWindow.surfaceHi : "transparent"
                StrokeIcon {
                    anchors.centerIn: parent
                    width: 16; height: 16
                    ink: snipBackMa.containsMouse
                         ? snippetsWindow.txt : snippetsWindow.muted
                    paths: ["M15 5 L8 12 L15 19"]
                }
                MouseArea {
                    id: snipBackMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        if (snippetsWindow.editingIndex >= 0) snippetsWindow.endEdit()
                        else snippetsWindow.closeMenu()
                    }
                }
            }

            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 28

                Row {
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 6
                    Row {
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 3
                        visible: snippetsWindow.editingIndex < 0
                                 && snippetsWindow.menuIndex < 0
                        Repeater {
                            model: 4
                            Rectangle {
                                width: 3; height: 3; radius: 1.5
                                color: snippetsWindow.faint
                            }
                        }
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: snippetsWindow.editingIndex >= 0
                              ? qsTr("Edit snippet")
                              : (snippetsWindow.menuIndex >= 0
                                 ? (snippetsWindow.menuSnip && snippetsWindow.menuSnip.label
                                    ? snippetsWindow.menuSnip.label : qsTr("Snippet"))
                                 : qsTr("Snippets"))
                        // The sheet title is a user-supplied label,
                        // and snippets round-trip through the Data
                        // Backup import.
                        textFormat: Text.PlainText
                        color: themeTextColor
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                        width: Math.min(implicitWidth,
                                        snippetsWindow.width - 130
                                        - (snipManageBtn.visible
                                           ? snipManageBtn.width + 6 : 0))
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
                    // Persist on release rather than on every motion
                    // event: one write per drag instead of hundreds.
                    onReleased: {
                        settings.savedSnippetsX = Math.round(snippetsWindow.x)
                        settings.savedSnippetsY = Math.round(snippetsWindow.y)
                    }
                }
            }

            // Manage-mode toggle. A word, not an icon: this window
            // typesets no glyphs (Segoe UI Emoji renders them in
            // colour and ignores the colour they are given), and a
            // drawn icon for "manage" is a guess the user has to
            // decode, which is what the sheet's word-only rows
            // already avoid.
            Rectangle {
                id: snipManageBtn
                objectName: "snipManageButton"
                visible: snippetsWindow.editingIndex < 0
                         && snippetsWindow.menuIndex < 0
                // Sized to the wider of the two labels, not to the
                // current one. Driven by the live text it shrank by
                // 24 px on entering manage mode, which slides the
                // close button along the header and under a pointer
                // that may already be travelling toward it. Same
                // rule as the pager: the controls being aimed at do
                // not move.
                TextMetrics {
                    id: snipManageMetrics
                    font: snipManageLabel.font
                    text: qsTr("Manage")
                }
                Layout.preferredWidth: Math.ceil(snipManageMetrics.width) + 16
                Layout.preferredHeight: 28
                radius: 4
                color: snippetsWindow.manageMode
                       ? Qt.rgba(themeAccent.r, themeAccent.g,
                                 themeAccent.b, 0.22)
                       : (snipManageMa.containsMouse
                          ? snippetsWindow.surfaceHi : "transparent")
                border.width: 1
                border.color: snippetsWindow.manageMode
                              ? themeAccent : "transparent"

                Text {
                    id: snipManageLabel
                    anchors.centerIn: parent
                    text: snippetsWindow.manageMode ? qsTr("Done") : qsTr("Manage")
                    textFormat: Text.PlainText
                    color: snippetsWindow.manageMode
                           ? themeTextColor : snippetsWindow.muted
                    font.pixelSize: 12
                }

                MouseArea {
                    id: snipManageMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: snippetsWindow.toggleManage()
                }
            }

            Rectangle {
                Layout.preferredWidth: 28
                Layout.preferredHeight: 28
                radius: 4
                color: snipCloseMa.containsMouse
                       ? Qt.rgba(snippetsWindow.danger.r, snippetsWindow.danger.g,
                                 snippetsWindow.danger.b, 0.22)
                       : "transparent"
                StrokeIcon {
                    anchors.centerIn: parent
                    width: 13; height: 13
                    strokeWidth: 2.4
                    ink: snipCloseMa.containsMouse
                         ? snippetsWindow.danger : snippetsWindow.muted
                    paths: ["M5 5 L19 19", "M19 5 L5 19"]
                }
                MouseArea {
                    id: snipCloseMa; anchors.fill: parent; hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: snippetsWindow.hide()
                }
            }
        }

        // ---- Grid view ----
        ColumnLayout {
            id: snipGridView
            // Named so the headless QML tests can assert on real
            // visibility rather than restating the binding.
            objectName: "snipGridView"
            Layout.fillWidth: true
            spacing: 6
            visible: snippetsWindow.editingIndex < 0 && snippetsWindow.menuIndex < 0

            Grid {
                id: snipGrid
                Layout.alignment: Qt.AlignHCenter
                columns: 2
                spacing: 6

                Repeater {
                    // A page keeps its full cell count as soon as
                    // there is more than one page, so a short last
                    // page does not pull the pager and the Add
                    // button up the window: the two controls the
                    // user is aiming at while paging are exactly the
                    // ones that must not move underneath them.
                    model: snippetsWindow.pageCount > 1
                           ? snippetsWindow.pageSize
                           : snippetsWindow.snippetList.length

                    delegate: Item {
                        id: snipCell
                        objectName: "snippetCell"
                        width: snippetsWindow.cellW
                        height: 58

                        readonly property int snipIndex:
                            snippetsWindow.page * snippetsWindow.pageSize + index
                        readonly property var snip: snippetsWindow.snippetList[snipIndex]
                        readonly property string tagName:
                            snip && snip.color ? snip.color : ""
                        readonly property bool filled:
                            snip !== undefined && snip.value && snip.value.length > 0

                        Rectangle {
                            id: snipTile
                            objectName: "snippetTile"
                            anchors.fill: parent
                            visible: snipCell.snip !== undefined
                            radius: 6

                            readonly property color tag:
                                snippetsWindow.tagColor(snipCell.tagName)
                            readonly property bool tagged: snipCell.tagName !== ""

                            // The tag tints the whole tile as well as
                            // inking the bar: at 165 px a 4 px stripe
                            // alone is easy to miss, and the point of
                            // tagging is to find a snippet without
                            // reading every label.
                            color: tileMa.containsMouse
                                   ? snippetsWindow.surfaceHi
                                   : (tagged
                                      ? Qt.tint(snippetsWindow.surface,
                                                Qt.rgba(tag.r, tag.g, tag.b, 0.18))
                                      : snippetsWindow.surface)
                            border.width: 1
                            // Accented in manage mode as well as on
                            // hover: the mode changes what a tap
                            // does, so it has to be visible on the
                            // thing being tapped and not only in the
                            // header. A border rather than a fill,
                            // for the reason the compact accent keys
                            // use one: it sits beside the label
                            // instead of behind it, so it costs no
                            // contrast on any of the nine themes.
                            border.color: (tileMa.containsMouse
                                           || snippetsWindow.manageMode)
                                          ? themeAccent : themeBorder

                            // Inset rather than full-bleed: `clip`
                            // clips to the bounding rect, not the
                            // rounded shape, so a flush bar pokes out
                            // past the corner curve (the same trap
                            // KeyButton's lock bar documents).
                            Rectangle {
                                visible: snipTile.tagged
                                x: 6
                                y: 9
                                width: 4
                                height: parent.height - 18
                                radius: 2
                                color: snipTile.tag
                            }

                            Column {
                                anchors.fill: parent
                                anchors.leftMargin: snipTile.tagged ? 17 : 10
                                anchors.rightMargin: 9
                                anchors.topMargin: 10
                                spacing: 2

                                Text {
                                    objectName: "snippetTileLabel"
                                    width: parent.width
                                    text: (snipCell.snip && snipCell.snip.label
                                           && snipCell.snip.label.length)
                                          ? snipCell.snip.label : qsTr("(unnamed)")
                                    // Snippets round-trip through Data
                                    // Backup import (replace-on-import
                                    // from a file the user picked), so
                                    // treat them as untrusted the same
                                    // as pack data.
                                    textFormat: Text.PlainText
                                    color: themeTextColor
                                    font.pixelSize: 13
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideRight
                                }
                                Text {
                                    width: parent.width
                                    text: snipCell.filled
                                          ? snipCell.snip.value : qsTr("empty, tap to fill in")
                                    textFormat: Text.PlainText
                                    color: snippetsWindow.muted
                                    font.pixelSize: 11
                                    font.italic: !snipCell.filled
                                    elide: Text.ElideRight
                                }
                            }

                            MouseArea {
                                id: tileMa
                                objectName: "snippetTileMouse"
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                // Right-click opens the actions
                                // sheet. Press-and-hold deliberately
                                // does not: a click held a beat too
                                // long is ordinary on this keyboard,
                                // and it must never turn typing a
                                // snippet into opening a menu.
                                acceptedButtons: Qt.LeftButton | Qt.RightButton
                                // A one-line pass-through, because a
                                // synthetic click cannot be delivered
                                // to a delegate reliably in the
                                // headless tests: the branch itself
                                // lives in tileClicked, where it can
                                // be driven directly.
                                onClicked: function(mouse) {
                                    snippetsWindow.tileClicked(snipCell.snipIndex,
                                                               mouse.button)
                                }
                            }
                        }
                    }
                }
            }

            // Pager, shown only once there is somewhere to page to.
            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 2
                spacing: 6
                visible: snippetsWindow.pageCount > 1

                Rectangle {
                    Layout.preferredWidth: 44
                    Layout.preferredHeight: 30
                    radius: 6
                    enabled: snippetsWindow.page > 0
                    opacity: enabled ? 1.0 : 0.35
                    color: prevPageMa.containsMouse
                           ? snippetsWindow.surfaceHi : snippetsWindow.surface
                    border.width: 1
                    border.color: themeBorder
                    StrokeIcon {
                        anchors.centerIn: parent
                        width: 14; height: 14
                        ink: snippetsWindow.txt
                        paths: ["M15 5 L8 12 L15 19"]
                    }
                    MouseArea {
                        id: prevPageMa
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: snippetsWindow.page = Math.max(0, snippetsWindow.page - 1)
                    }
                }

                Text {
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    text: qsTr("Page %1 of %2")
                          .arg(snippetsWindow.page + 1).arg(snippetsWindow.pageCount)
                    color: snippetsWindow.muted
                    font.pixelSize: 11
                }

                Rectangle {
                    Layout.preferredWidth: 44
                    Layout.preferredHeight: 30
                    radius: 6
                    enabled: snippetsWindow.page < snippetsWindow.pageCount - 1
                    opacity: enabled ? 1.0 : 0.35
                    color: nextPageMa.containsMouse
                           ? snippetsWindow.surfaceHi : snippetsWindow.surface
                    border.width: 1
                    border.color: themeBorder
                    StrokeIcon {
                        anchors.centerIn: parent
                        width: 14; height: 14
                        ink: snippetsWindow.txt
                        paths: ["M9 5 L16 12 L9 19"]
                    }
                    MouseArea {
                        id: nextPageMa
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: snippetsWindow.page =
                            Math.min(snippetsWindow.pageCount - 1, snippetsWindow.page + 1)
                    }
                }
            }

            // Add: a dashed outline rather than a filled button, so
            // the one control that is not a snippet does not compete
            // with the six that are.
            Rectangle {
                id: snipAddBtn
                objectName: "snipAddButton"
                Layout.fillWidth: true
                Layout.preferredHeight: 38
                radius: 6
                readonly property bool atCap:
                    snippetsWindow.snippetList.length >= snippetsWindow.snippetLimit
                color: addMa.containsMouse && !snipAddBtn.atCap
                       ? Qt.rgba(themeAccent.r, themeAccent.g,
                                 themeAccent.b, 0.16)
                       : "transparent"
                border.width: 1
                border.color: snipAddBtn.atCap
                              ? themeBorder
                              : Qt.rgba(themeAccent.r, themeAccent.g,
                                        themeAccent.b, 0.6)

                Row {
                    anchors.centerIn: parent
                    spacing: 7
                    StrokeIcon {
                        anchors.verticalCenter: parent.verticalCenter
                        visible: !snipAddBtn.atCap
                        width: 13; height: 13
                        strokeWidth: 2.2
                        ink: themeAccent
                        paths: ["M12 5 L12 19", "M5 12 L19 12"]
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: snipAddBtn.atCap
                              ? qsTr("Full, %1 snippets").arg(snippetsWindow.snippetLimit)
                              : qsTr("Add snippet")
                        color: snipAddBtn.atCap
                               ? snippetsWindow.faint : themeAccent
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                    }
                }

                MouseArea {
                    id: addMa
                    anchors.fill: parent
                    hoverEnabled: true
                    // At the cap SnippetStore.add refuses and the
                    // list does not grow. Editing "the last snippet"
                    // regardless would open somebody else's snippet
                    // for editing, so the button goes inert instead.
                    enabled: !snipAddBtn.atCap
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        if (!keyboard) return
                        var before = snippetsWindow.snippetList.length
                        keyboard.addSnippet()
                        snippetsWindow.refresh()
                        if (snippetsWindow.snippetList.length > before) {
                            var added = snippetsWindow.snippetList.length - 1
                            snippetsWindow.page =
                                Math.floor(added / snippetsWindow.pageSize)
                            snippetsWindow.beginEdit(added)
                        }
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                text: qsTr("Tap a snippet to copy it. Right-click one for edit, colour, reorder and delete.")
                color: snippetsWindow.faint
                font.pixelSize: 10
                wrapMode: Text.WordWrap
            }
        }

        // ---- Actions sheet (right-click a tile) ----
        ColumnLayout {
            objectName: "snipSheetView"
            Layout.fillWidth: true
            spacing: 6
            visible: snippetsWindow.menuIndex >= 0 && snippetsWindow.editingIndex < 0

            Text {
                Layout.fillWidth: true
                text: (snippetsWindow.menuSnip && snippetsWindow.menuSnip.value
                       && snippetsWindow.menuSnip.value.length)
                      ? snippetsWindow.menuSnip.value : qsTr("empty, nothing to copy yet")
                textFormat: Text.PlainText
                color: snippetsWindow.muted
                font.pixelSize: 11
                font.italic: !(snippetsWindow.menuSnip && snippetsWindow.menuSnip.value
                               && snippetsWindow.menuSnip.value.length)
                wrapMode: Text.Wrap
                maximumLineCount: 2
                elide: Text.ElideRight
            }

            SheetRow {
                Layout.fillWidth: true
                label: qsTr("Copy to clipboard")
                enabled: snippetsWindow.menuHasValue
                ink: themeAccent
                surface: snippetsWindow.surface
                hover: snippetsWindow.surfaceHi
                hairline: themeBorder
                onActivated: {
                    var s = snippetsWindow.menuSnip
                    if (keyboard && keyboard.copySnippet(snippetsWindow.menuIndex)) {
                        copied(s ? s.label : "")
                        snippetsWindow.closeMenu()
                        snippetsWindow.hide()
                    }
                }
            }

            SheetRow {
                Layout.fillWidth: true
                label: qsTr("Edit label and text")
                ink: snippetsWindow.txt
                surface: snippetsWindow.surface
                hover: snippetsWindow.surfaceHi
                hairline: themeBorder
                onActivated: snippetsWindow.beginEdit(snippetsWindow.menuIndex)
            }

            Text {
                Layout.topMargin: 2
                text: qsTr("Colour tag")
                color: snippetsWindow.faint
                font.pixelSize: 10
            }

            Row {
                Layout.fillWidth: true
                spacing: 6

                Repeater {
                    // Straight from the store's allow-list, so a
                    // swatch can never offer a tag the store would
                    // drop back to untagged.
                    model: snippetsWindow.tagNames

                    delegate: Rectangle {
                        readonly property bool isClear: modelData === ""
                        readonly property bool isCurrent:
                            snippetsWindow.menuSnip !== undefined
                            && (snippetsWindow.menuSnip.color || "") === modelData

                        width: 34
                        height: 34
                        radius: 17
                        color: isClear ? snippetsWindow.defaultTagInk
                                       : snippetsWindow.tagColor(modelData)
                        border.width: isCurrent ? 3 : 0
                        border.color: snippetsWindow.txt

                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (keyboard)
                                    keyboard.setSnippetColor(snippetsWindow.menuIndex,
                                                             modelData)
                            }
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 2
                spacing: 6

                SheetRow {
                    Layout.fillWidth: true
                    label: qsTr("Move earlier")
                    enabled: snippetsWindow.canMoveEarlier
                    ink: snippetsWindow.txt
                    surface: snippetsWindow.surface
                    hover: snippetsWindow.surfaceHi
                    hairline: themeBorder
                    onActivated: snippetsWindow.moveSnippet(-1)
                }
                SheetRow {
                    Layout.fillWidth: true
                    label: qsTr("Move later")
                    enabled: snippetsWindow.canMoveLater
                    ink: snippetsWindow.txt
                    surface: snippetsWindow.surface
                    hover: snippetsWindow.surfaceHi
                    hairline: themeBorder
                    onActivated: snippetsWindow.moveSnippet(1)
                }
            }

            // Delete, in two steps always. A snippet is typed-once,
            // kept-forever data with no undo behind it, and this
            // window is operated with an imprecise pointer.
            SheetRow {
                Layout.fillWidth: true
                visible: !snippetsWindow.confirmingDelete
                label: qsTr("Delete")
                ink: snippetsWindow.danger
                surface: snippetsWindow.surface
                hover: Qt.rgba(snippetsWindow.danger.r, snippetsWindow.danger.g,
                               snippetsWindow.danger.b, 0.18)
                hairline: themeBorder
                onActivated: snippetsWindow.confirmingDelete = true
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 6
                visible: snippetsWindow.confirmingDelete

                Text {
                    Layout.fillWidth: true
                    text: qsTr("Delete this snippet? This cannot be undone.")
                    color: snippetsWindow.danger
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    // Keep sits first and wider: the safe answer
                    // should be the easier target.
                    SheetRow {
                        Layout.fillWidth: true
                        label: qsTr("Keep")
                        ink: snippetsWindow.txt
                        surface: snippetsWindow.surface
                        hover: snippetsWindow.surfaceHi
                        hairline: themeBorder
                        onActivated: snippetsWindow.confirmingDelete = false
                    }
                    SheetRow {
                        Layout.preferredWidth: 110
                        label: qsTr("Delete")
                        ink: inkOn(snippetsWindow.danger)
                        surface: snippetsWindow.danger
                        hover: Qt.lighter(snippetsWindow.danger, 1.12)
                        hairline: snippetsWindow.danger
                        onActivated: {
                            if (keyboard) keyboard.deleteSnippet(snippetsWindow.menuIndex)
                            snippetsWindow.closeMenu()
                        }
                    }
                }
            }
        }

        // ---- Edit view ----
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6
            visible: snippetsWindow.editingIndex >= 0

            Text {
                text: qsTr("Label (shown on the tile)")
                color: snippetsWindow.muted; font.pixelSize: 11
            }
            TextField {
                id: snipLabelField
                objectName: "snipLabelField"
                Layout.fillWidth: true
                Layout.preferredHeight: 36
                color: snippetsWindow.txt; font.pixelSize: 14
                selectionColor: themeAccent
                selectedTextColor: inkOn(themeAccent)
                leftPadding: 10; rightPadding: 10
                background: Rectangle {
                    color: snippetsWindow.surface; radius: 6
                    border.color: snippetsWindow.editTarget === "label"
                                  ? themeAccent : themeBorder
                    border.width: snippetsWindow.editTarget === "label" ? 2 : 1
                }
                // Records which field the OSK types into, then gets
                // out of the way.  Accepting the press swallowed it,
                // and the field underneath is what places the caret,
                // selects a word on a double click, selects a line on
                // a triple click and drag-selects.  All four were
                // dead: the whole point of a MouseArea is that it
                // takes the event, and this one covered the text.
                // `selectByMouse` was true the entire time and
                // `selectWord()` worked when called directly, so
                // nothing was missing except the events themselves.
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.IBeamCursor
                    onPressed: function(mouse) {
                        snippetsWindow.editTarget = "label"
                        snipLabelField.forceActiveFocus()
                        mouse.accepted = false
                    }
                }
            }

            Text {
                text: qsTr("Text to type")
                color: snippetsWindow.muted; font.pixelSize: 11
            }
            TextField {
                id: snipValueField
                objectName: "snipValueField"
                Layout.fillWidth: true
                Layout.preferredHeight: 36
                color: snippetsWindow.txt; font.pixelSize: 14
                selectionColor: themeAccent
                selectedTextColor: inkOn(themeAccent)
                leftPadding: 10; rightPadding: 10
                background: Rectangle {
                    color: snippetsWindow.surface; radius: 6
                    border.color: snippetsWindow.editTarget === "value"
                                  ? themeAccent : themeBorder
                    border.width: snippetsWindow.editTarget === "value" ? 2 : 1
                }
                // Pass-through, exactly as on the label field above.
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.IBeamCursor
                    onPressed: function(mouse) {
                        snippetsWindow.editTarget = "value"
                        snipValueField.forceActiveFocus()
                        mouse.accepted = false
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                text: qsTr("Type with the keyboard below. The highlighted box is where text goes. Tab or tap the other box to switch. Double-click a word to select it.")
                color: snippetsWindow.faint; font.pixelSize: 10
                wrapMode: Text.WordWrap
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                Item { Layout.fillWidth: true }
                Rectangle {
                    width: 92; height: 36; radius: 6
                    color: snipCancelMa.containsMouse
                           ? snippetsWindow.surfaceHi : snippetsWindow.surface
                    border.color: themeBorder; border.width: 1
                    Text {
                        anchors.centerIn: parent; text: qsTr("Cancel")
                        color: snippetsWindow.txt; font.pixelSize: 13
                    }
                    MouseArea {
                        id: snipCancelMa; anchors.fill: parent; hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: snippetsWindow.endEdit()
                    }
                }
                Rectangle {
                    width: 92; height: 36; radius: 6
                    // The one filled control in the window: Save is
                    // the action the editor exists for.
                    color: snipSaveMa.containsMouse
                           ? Qt.lighter(themeAccent, 1.15) : themeAccent
                    border.color: themeAccent; border.width: 1
                    Text {
                        anchors.centerIn: parent; text: qsTr("Save")
                        // Same luminance rule KeyButton uses on an
                        // accent fill: several themes ship a pale
                        // accent, where white text is unreadable.
                        color: inkOn(themeAccent)
                        font.pixelSize: 13; font.weight: Font.Bold
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
