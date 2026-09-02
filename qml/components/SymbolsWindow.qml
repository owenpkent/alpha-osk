import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Window 2.15

// ===== Symbols & Emoji =====
//
// The long tail behind the keyboard's own symbol layer. That layer
// carries the 34 glyphs worth a single click; the full catalogue,
// the accented letters and every emoji live here, because
// categories, a Recent page and hundreds of glyphs do not fit on a
// key grid at a size an imprecise pointer can hit.
//
// A separate top-level Window rather than a Popup, for the same
// reason the Snippets window is one: a Popup is clipped to its
// parent window's overlay, so it could never be dragged clear of
// the field being filled in. The whole shell here is deliberately
// the Snippets window's, down to the drag handle, the desktop-wide
// position clamp and the full-page grid, so the two read as
// siblings rather than as two people's guesses at the same thing.
Window {
    id: symbolsWindow

    // The only things this window needs from the keyboard window:
    // required properties bound in Main.qml, and a signal so the
    // problem toast (which must stay on the keyboard window) can
    // still fire from here.
    required property var clampedWindowPos
    required property color themeAccent
    required property color themeBackground
    required property color themeKeyColor
    required property color themeKeyPressed
    required property color themeTextColor
    required property real keyboardWidth
    required property real keyboardX
    required property real keyboardY
    required property var settings

    signal problem(string message)

    // Found by objectName from keyboard_app.py, which re-applies
    // WS_EX_NOACTIVATE on every show: on Windows the Qt flag alone
    // does not stop click-activation, and this window must never
    // take focus from the app being typed into.
    objectName: "symbolsWindow"
    width: 460
    height: Math.max(240, symContent.implicitHeight + 24)
    minimumWidth: 460
    minimumHeight: 240
    color: "transparent"
    title: "Alpha-OSK Symbols"
    flags: Qt.Window | Qt.FramelessWindowHint
           | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus

    // [{id, label, glyphs}] from src/glyphs.py, fetched once on the
    // first open. It is static data, so re-querying it per open
    // would rebuild several hundred strings for no reason.
    property var catalogue: []
    // 0 is the Recent tab; 1 and up index into `catalogue`.
    property int categoryIndex: 1
    property int page: 0
    // Most-recent-first, persisted in the Qt settings layer rather
    // than in a file of its own. It is a convenience the user can
    // rebuild by tapping four glyphs, which is exactly the kind of
    // state the Data Backup archive deliberately does not carry, so
    // giving it a file would have meant a loader, a size cap and an
    // export decision for something worth none of them.
    property var recent: []
    readonly property int recentLimit: keyboard ? keyboard.getRecentGlyphLimit() : 24

    // 8 x 4. The grid is paged rather than scrolled for the reason
    // the Snippets grid is: this window floats over whatever is
    // being typed into, so a list that grew downward would end up
    // covering the target app.
    readonly property int columns: 8
    readonly property int rowCount: 4
    readonly property int pageSize: columns * rowCount

    readonly property var tabLabels: {
        var out = [qsTr("Recent")]
        for (var i = 0; i < catalogue.length; i++)
            out.push(catalogue[i].label)
        return out
    }

    readonly property var activeGlyphs: {
        if (categoryIndex <= 0) return recent
        var c = catalogue[categoryIndex - 1]
        return c ? c.glyphs : []
    }
    readonly property int pageCount:
        Math.max(1, Math.ceil(activeGlyphs.length / pageSize))

    // Theme-derived, the same set the Snippets window derives and
    // for the same reason: hardcoded colours read as foreign on the
    // dark themes and break outright on Typewriter.
    readonly property color surface: themeKeyColor
    readonly property color surfaceHi: themeKeyPressed
    readonly property color txt: themeTextColor
    readonly property color muted: Qt.rgba(txt.r, txt.g, txt.b, 0.58)
    readonly property color faint: Qt.rgba(txt.r, txt.g, txt.b, 0.42)

    // Derived from the layout properties rather than restated as
    // literals, the rule the Snippets grid had to learn: a copied
    // margin is flush only until someone edits the margin.
    readonly property real cellW:
        (width - 2 * symContent.anchors.margins
         - (columns - 1) * symGrid.spacing) / columns

    property bool _positioned: false

    // Wired to the page count rather than to each mutation of the
    // glyph list, so any future way the list shrinks under the
    // current page is caught without a second call site.
    onPageCountChanged: clampPage()

    function clampPage() {
        if (page > pageCount - 1) page = pageCount - 1
        if (page < 0) page = 0
    }

    function selectCategory(idx) {
        categoryIndex = idx
        page = 0
    }

    function loadRecent() {
        var out = []
        try {
            var parsed = JSON.parse(settings.savedRecentGlyphs || "[]")
            if (parsed && parsed.length !== undefined) {
                for (var i = 0; i < parsed.length && out.length < recentLimit; i++) {
                    if (typeof parsed[i] === "string" && parsed[i].length > 0)
                        out.push(parsed[i])
                }
            }
        } catch (e) {
            // A malformed value costs the Recent tab and nothing
            // else, so it is dropped rather than reported: the
            // catalogue underneath it is intact and the user is one
            // tap from refilling this.
        }
        recent = out
    }

    function remember(glyph) {
        var out = [glyph]
        for (var i = 0; i < recent.length && out.length < recentLimit; i++) {
            if (recent[i] !== glyph) out.push(recent[i])
        }
        recent = out
        settings.savedRecentGlyphs = JSON.stringify(out)
    }

    function typeGlyph(glyph) {
        if (!glyph || !keyboard) return
        // A false return means nothing reached the app: the bridge
        // refuses while an edit field owns the keystrokes. Saying so
        // matters more here than it would on a keycap, because this
        // window sits somewhere else on the desktop, so a tap that
        // silently did nothing is indistinguishable from one that
        // missed, and the user taps again.
        if (keyboard.insertGlyph(glyph)) remember(glyph)
        else problem(qsTr("Could not type that here"))
    }

    function openPicker() {
        if (catalogue.length === 0 && keyboard)
            catalogue = keyboard.getGlyphCategories()
        loadRecent()
        // Recent once there is something in it, the first catalogue
        // tab before that: opening on an empty page teaches the
        // user the window is empty.
        selectCategory(recent.length > 0 ? 0 : 1)
        // Restore where it was last left, clamped back on-screen
        // over the whole virtual desktop rather than the primary
        // screen. Only the first open of a session positions it.
        if (!_positioned) {
            if (settings.savedSymbolsX > -1000000
                    && settings.savedSymbolsY > -1000000) {
                var pos = clampedWindowPos(settings.savedSymbolsX,
                                            settings.savedSymbolsY,
                                            symbolsWindow.width,
                                            symbolsWindow.height)
                symbolsWindow.x = pos.x
                symbolsWindow.y = pos.y
            } else {
                symbolsWindow.x = keyboardX + (keyboardWidth - symbolsWindow.width) / 2
                symbolsWindow.y = Math.max(0, keyboardY - symbolsWindow.height - 8)
            }
            _positioned = true
        }
        symbolsWindow.show()
        symbolsWindow.raise()
    }

    Rectangle {
        anchors.fill: parent
        color: themeBackground
        radius: 8
        border.color: themeAccent
        border.width: 1

        ColumnLayout {
            id: symContent
            anchors.fill: parent
            anchors.margins: 12
            spacing: 8

            // --- header: title, drag handle, close ---
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 26

                Text {
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    text: qsTr("Symbols & Emoji")
                    textFormat: Text.PlainText
                    color: symbolsWindow.txt
                    font.pixelSize: 14
                    font.bold: true
                }

                MouseArea {
                    id: symDragArea
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    anchors.right: symCloseBtn.left
                    cursorShape: Qt.SizeAllCursor
                    property real startMx
                    property real startMy
                    property real startX
                    property real startY
                    // Manual x/y drag, never startSystemMove(), the
                    // same rule the other two draggable windows
                    // follow: this window cannot accept focus, so a
                    // WM-driven move is unreliable on X11/Mutter and
                    // its true-on-send return value used to suppress
                    // the fallback and kill the drag outright.
                    onPressed: function(mouse) {
                        var g = mapToGlobal(mouse.x, mouse.y)
                        startMx = g.x; startMy = g.y
                        startX = symbolsWindow.x; startY = symbolsWindow.y
                    }
                    onPositionChanged: function(mouse) {
                        if (!pressed) return
                        var g = mapToGlobal(mouse.x, mouse.y)
                        symbolsWindow.x = startX + (g.x - startMx)
                        symbolsWindow.y = startY + (g.y - startMy)
                    }
                    // One write per drag rather than hundreds.
                    onReleased: {
                        settings.savedSymbolsX = Math.round(symbolsWindow.x)
                        settings.savedSymbolsY = Math.round(symbolsWindow.y)
                    }
                }

                Rectangle {
                    id: symCloseBtn
                    objectName: "symbolsCloseButton"
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    width: 26
                    height: 26
                    radius: 4
                    color: symCloseArea.containsMouse ? symbolsWindow.surfaceHi
                                                      : "transparent"

                    // Feather's "x", MIT, (c) 2013-2023 Cole Bemis.
                    // Drawn rather than typeset, the rule this file
                    // documents three times over: a cross in a Text
                    // resolves through Segoe UI Emoji on Windows and
                    // comes out as a colour glyph that ignores `ink`.
                    StrokeIcon {
                        anchors.centerIn: parent
                        width: 14
                        height: 14
                        paths: ["M18 6L6 18", "M6 6l12 12"]
                        ink: symCloseArea.containsMouse ? symbolsWindow.txt
                                                        : symbolsWindow.muted
                    }

                    MouseArea {
                        id: symCloseArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: symbolsWindow.hide()
                    }
                }
            }

            // --- category tabs ---
            //
            // A Flow that wraps onto two or three lines, not a
            // horizontally scrolling strip. Every chip stays a full
            // target that way; a scroll strip would put half the
            // categories behind a drag gesture, which is the one
            // input this keyboard's users can least rely on.
            Flow {
                id: symTabs
                Layout.fillWidth: true
                spacing: 5

                Repeater {
                    model: symbolsWindow.tabLabels

                    Rectangle {
                        property bool selected: index === symbolsWindow.categoryIndex
                        width: symTabText.implicitWidth + 16
                        height: 26
                        radius: 4
                        color: selected ? symbolsWindow.surfaceHi
                               : (symTabArea.containsMouse ? symbolsWindow.surface
                                                           : "transparent")
                        border.color: selected ? themeAccent : symbolsWindow.faint
                        border.width: 1

                        Text {
                            id: symTabText
                            anchors.centerIn: parent
                            text: modelData
                            textFormat: Text.PlainText
                            color: parent.selected ? symbolsWindow.txt
                                                   : symbolsWindow.muted
                            font.pixelSize: 12
                        }

                        MouseArea {
                            id: symTabArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: symbolsWindow.selectCategory(index)
                        }
                    }
                }
            }

            // --- glyph grid ---
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: symGrid.implicitHeight

                Grid {
                    id: symGrid
                    objectName: "symbolsGrid"
                    anchors.horizontalCenter: parent.horizontalCenter
                    columns: symbolsWindow.columns
                    spacing: 5

                    Repeater {
                        // The model is the page size, not the number
                        // of glyphs left, so a short last page keeps
                        // its empty cells and the pager underneath it
                        // cannot walk up the window into a pointer
                        // already travelling toward it. Same rule as
                        // the Snippets grid.
                        model: symbolsWindow.pageSize

                        Rectangle {
                            property int glyphIndex:
                                symbolsWindow.page * symbolsWindow.pageSize + index
                            property string glyph:
                                glyphIndex < symbolsWindow.activeGlyphs.length
                                ? symbolsWindow.activeGlyphs[glyphIndex] : ""
                            width: symbolsWindow.cellW
                            height: 42
                            radius: 5
                            color: glyph.length === 0 ? "transparent"
                                   : (symCellArea.containsMouse ? symbolsWindow.surfaceHi
                                                                : symbolsWindow.surface)
                            border.color: glyph.length > 0 && symCellArea.containsMouse
                                          ? themeAccent : "transparent"
                            border.width: 1

                            Text {
                                anchors.centerIn: parent
                                text: parent.glyph
                                textFormat: Text.PlainText
                                color: symbolsWindow.txt
                                font.pixelSize: 21
                                // No family is named, and that is the
                                // decision rather than the default.
                                // This is the one surface in the app
                                // that *wants* the host emoji font
                                // (colour is the content here, not
                                // chrome that has to obey an ink
                                // colour), and Qt's own fallback is
                                // what picks it: `font.families` does
                                // not exist on this Qt's grouped font
                                // property, and naming a single family
                                // would pin one platform's font and
                                // lose the glyph on the other two.
                            }

                            MouseArea {
                                id: symCellArea
                                anchors.fill: parent
                                hoverEnabled: true
                                enabled: parent.glyph.length > 0
                                cursorShape: Qt.PointingHandCursor
                                onClicked: symbolsWindow.typeGlyph(parent.glyph)
                            }
                        }
                    }
                }

                Text {
                    anchors.centerIn: parent
                    width: parent.width - 40
                    visible: symbolsWindow.categoryIndex === 0
                             && symbolsWindow.recent.length === 0
                    text: qsTr("Symbols you tap turn up here, newest first.")
                    textFormat: Text.PlainText
                    color: symbolsWindow.muted
                    font.pixelSize: 12
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }
            }

            // --- pager ---
            //
            // Always present, with its arrows disabled at the ends
            // rather than hidden. A control that disappears when it
            // has nothing to do takes the row's height with it and
            // moves everything below.
            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Rectangle {
                    objectName: "symbolsPrevPage"
                    Layout.preferredWidth: 60
                    Layout.preferredHeight: 26
                    radius: 4
                    enabled: symbolsWindow.page > 0
                    opacity: enabled ? 1.0 : 0.35
                    color: symPrevArea.containsMouse && enabled
                           ? symbolsWindow.surfaceHi : symbolsWindow.surface
                    Text {
                        anchors.centerIn: parent
                        text: qsTr("Back")
                        textFormat: Text.PlainText
                        color: symbolsWindow.txt
                        font.pixelSize: 12
                    }
                    MouseArea {
                        id: symPrevArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: if (symbolsWindow.page > 0) symbolsWindow.page--
                    }
                }

                Text {
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    text: qsTr("Page %1 of %2").arg(symbolsWindow.page + 1)
                                               .arg(symbolsWindow.pageCount)
                    textFormat: Text.PlainText
                    color: symbolsWindow.muted
                    font.pixelSize: 11
                }

                Rectangle {
                    objectName: "symbolsNextPage"
                    Layout.preferredWidth: 60
                    Layout.preferredHeight: 26
                    radius: 4
                    enabled: symbolsWindow.page < symbolsWindow.pageCount - 1
                    opacity: enabled ? 1.0 : 0.35
                    color: symNextArea.containsMouse && enabled
                           ? symbolsWindow.surfaceHi : symbolsWindow.surface
                    Text {
                        anchors.centerIn: parent
                        text: qsTr("More")
                        textFormat: Text.PlainText
                        color: symbolsWindow.txt
                        font.pixelSize: 12
                    }
                    MouseArea {
                        id: symNextArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (symbolsWindow.page < symbolsWindow.pageCount - 1)
                                symbolsWindow.page++
                        }
                    }
                }
            }
        }
    }
}
