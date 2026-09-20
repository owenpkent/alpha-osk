# Symbols & Emoji window

Moved verbatim from `CLAUDE.md` on 2026-09-20, which keeps a summary of the load-bearing rules. This is the full reasoning.

## Symbols & Emoji window

The only route to a glyph outside a physical keyboard's printing, on every
layout, since the full-size symbol layer above was removed. Categories, a
Recent page and several hundred glyphs do not fit on a key grid at a size an
imprecise pointer can hit, which is why this is a window and not a layer. Opened from a smile button in the suggestion bar,
immediately left of the Snippets bookmark, with a title-bar twin
(`symbolsTitleBarButton`) visible only when `suggestionsEnabled` is false, for
the reason the Snippets pair documents: the suggestion bar collapses to zero
height with that setting and an unrelated toggle must not be the only thing
between the user and a feature.

Catalogue in `src/glyphs.py`, bridge slots `getGlyphCategories()`,
`insertGlyph(str) -> bool` and `getRecentGlyphLimit()`, UI is the floating
`symbolsWindow` in `qml/components/SymbolsWindow.qml`, instantiated from
`Main.qml` the same way as `SnippetsWindow.qml`: required properties for
theme colours and the helper functions it needs, and a `problem` signal so
the `snippetProblemToast` it fires on a failed insert can stay on the
keyboard window.

### A tap types, it does not copy

This is the one place the window deliberately diverges from Snippets, which
copies to the clipboard and offers no way to type at all. The argument there
is that a long address is expensive to retype, only lands when the caret is
already in the right field, and **misses silently** when it does not. A glyph
is a keystroke. Every key on the keyboard has exactly the same focus
exposure, so paying two clicks and a clipboard round trip to type one
character would make the window worse than the keys beside it.

`insertGlyph` calls the **same `_commit_verbatim_insert` method `insertSnippet`
calls**, rather than merely mirroring its shape, because a pair of
nearly-identical blocks drifting apart is this codebase's recurring failure:
one call runs the verbatim-insert prologue (`_release_sticky_modifiers()`, a
deferred auto-space settled as prose, `_consume_auto_cap()`), sends through
`_send_literal_text` so a held modifier cannot rewrite it, then clears
`_current_word` / `_raw_token` / the pill row so the glyph cannot corrupt the
next prefix match. Not gated on privacy mode, same rule as every other
insert: the user tapped it, so it has to reach the app.

**It returns a bool and QML honours it.** The bridge refuses while an edit
field owns the keystrokes (the prediction-edit popup, the snippets editor).
The window is somewhere else on the desktop, so a tap that silently did
nothing is indistinguishable from one that missed and the user taps again;
a refused tap flashes the problem toast and is **not** written to Recent,
which would otherwise fill with glyphs that never reached anything.

### Recent lives in the settings layer, not in a file

It is a convenience the user rebuilds by tapping four glyphs. A file would
have needed a loader, a size cap and a Data Backup decision for something
worth none of the three, so it is `appSettings.savedRecentGlyphs`, a JSON
array of strings, capped at `glyphs.MAX_RECENT` and deduplicated most-recent
first. The cap lives in Python and QML asks for it, the same split
`getSnippetLimit` uses: QML owns the storage, the bridge owns the bound.

A malformed value is dropped rather than reported. The catalogue underneath
it is intact and the user is one tap from refilling the list, so a dialog
would cost more than the thing it is about.

The window **opens on Recent once there is something in it**, and on the
first catalogue tab before that: opening on an empty page teaches the user
the window is empty.

### The shell is the Snippets window's

Same flags, same manual drag (never `startSystemMove()`), same
desktop-wide `clampedWindowPos` restore, same write-position-on-release. It
is found by `objectName` from `keyboard_app.py::_wire_floating_windows`,
which now loops over both windows and re-applies `WS_EX_NOACTIVATE` on every
`visibleChanged`; the per-window handler binds its target as a **default
argument** rather than closing over the loop variable, which would otherwise
leave both handlers styling whichever window was found last.

The grid pages 8 x 4 and the Repeater's model is the **page size, not the
glyphs remaining**, so a short last page keeps its empty cells and the pager
underneath cannot walk up the window into a pointer already travelling toward
it. An empty cell's MouseArea is disabled, so it is not a tap that silently
does nothing. Switching category resets to page 0, or a tab switch lands on
page 3 of a category with two.

Category tabs are a **wrapping `Flow`, not a scrolling strip**: a scroll
strip would put half the categories behind a drag gesture, which is the input
this keyboard's users can least rely on. Three rows of chips is the cost.

### Two font rules, and they pull in opposite directions

The **chrome** obeys the project's usual rule: the smile and the close cross
are `StrokeIcon` path data, never typeset, because Segoe UI Emoji renders a
glyph in colour and ignores the ink it is given.

The **cells** do the opposite and name no font family at all. Colour is the
content here rather than chrome that has to obey an ink colour, and Qt's own
fallback is what reaches the host emoji font. `font.families` (a list) does
not exist on this Qt's grouped font property, and naming a single family
would pin one platform's font and lose the glyph on the other two.

`tests/test_qml_symbols.py::TestTheEntryButtonIcon` asserts the smile paints
ink at all, which is the property its one hand-converted path can break:
invalid path data paints **nothing** through `ctx.path` (measured: zero lit
pixels), so a bad conversion ships as a blank circle on the suggestion bar
rather than as an error.

### The suggestion bar's button reserve is derived

`predBar.predButtonCount` is now the number the reserve is computed from
(`predPillHeight * count + predButtonGap * (count - 1) + 16`). It went from
one button to two to three and each time the formula was re-derived by hand;
the reserve is what stops a prediction pill rendering underneath a control,
so a reserve that is merely near the truth is the bug the property exists to
prevent. The new button is the **first** child of the right-anchored Row, so
Snippets and the clear-context ring do not move.

Guarded by `tests/test_qml_symbols.py`, `tests/test_glyphs.py`, and
`tests/test_keyboard_bridge.py::TestTypingAGlyphFromThePicker`.
