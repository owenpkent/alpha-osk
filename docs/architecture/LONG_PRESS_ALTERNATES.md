# Long-Press Alternates (design doc — not implemented)

Gboard-style "press and hold a key to pick an accented variant" affordance. Lets users type `é`, `ñ`, `ü`, `—`, `…`, currency symbols, etc. without leaving the OSK or changing layout.

**Status:** designed, deferred. The companion right-click feature (right-click → shifted variant) ships in v1.0.14. Long-press is paused because it requires changing char-key timing semantics across the whole keyboard (press-on-release instead of press-on-press), which is non-trivial and has UX risk for slow-motor users — the exact audience this OSK serves.

When picking this back up, the plan below is the starting point.

## UX

1. User presses and holds a char key on the OSK.
2. After ~400 ms, a small popup appears anchored above the source key (or below if near the top edge), showing alternate characters in a horizontal row.
3. User clicks an alternate → that character is typed, popup closes.
4. User releases on the original key (no alternate clicked) → the original character is typed.
5. User clicks outside / presses Escape → popup closes, nothing typed.

This is **not** Gboard's drag-to-select model. On a phone, you press-and-hold, the popup appears under your thumb, and you slide to the variant. On a mouse-driven OSK with a user who has limited motor control, drag-to-select is hostile. Click-to-pick is friendlier and matches every other interaction in Alpha-OSK.

## Settings

- **Toggle:** *Settings → Smart Typing → Input → "Long-press for accented characters"* (default **OFF** — the timing change is opt-in).
- **Delay slider:** 250–800 ms, default 400 ms.
- Both persist as `appSettings.savedLongPressAlternates` / `savedLongPressDelay`.

## Data

New file: `data/key_alternates.json`. Lookup is case-insensitive (the same table serves lowercase and uppercase keys; popup uppercases the variants when shift / caps is on at long-press time).

```json
{
  "a": ["à", "á", "â", "ä", "ã", "å", "æ", "ā"],
  "e": ["è", "é", "ê", "ë", "ē", "ė", "ę"],
  "i": ["ì", "í", "î", "ï", "ī"],
  "o": ["ò", "ó", "ô", "ö", "õ", "ø", "œ", "ō"],
  "u": ["ù", "ú", "û", "ü", "ū"],
  "n": ["ñ", "ń"],
  "c": ["ç", "ć", "č"],
  "s": ["ß", "ś", "š"],
  "y": ["ÿ", "ý"],
  "z": ["ž", "ź", "ż"],
  "-": ["–", "—", "·"],
  "'": ["‘", "’"],
  "\"": ["“", "”"],
  ".": ["…"],
  "?": ["¿"],
  "!": ["¡"],
  "$": ["€", "£", "¥", "¢", "₩", "₹"]
}
```

## Implementation

### `qml/components/KeyButton.qml`
- Add `signal keyLongPressed()`, `property bool enableLongPress: false`, `property int longPressDelay: 400`.
- Add a `Timer { id: longPressTimer; interval: longPressDelay; ... }` started on press, stopped on release / cancel / drag-off.
- A `longPressFired` flag set true when the timer fires.
- **Behaviour change:** when `enableLongPress` is true, char keys must fire on **release**, not press, and only if `longPressFired` is false. Otherwise the user would type the original character immediately and *then* see the picker appear — confusing.
- Special / modifier keys: `enableLongPress` is always false for them (no alternates make sense for Tab, Shift, Enter).

### New: `qml/components/AlternatesPopup.qml`
- A `Popup` with `modal: false`, `closePolicy: Popup.CloseOnPressOutsideParent | Popup.CloseOnEscape`.
- Anchors above the source key with a fallback to below (use `mapToItem` to compute screen position).
- A `Row` of mini `Comp.KeyButton`s built from the `alternates` list. Click → `alternateChosen(char)` signal → close.
- Theme-aware (pass through colors from root).

### `qml/Main.qml`
- One `AlternatesPopup` instance at root.
- Helper `function showAlternates(keyBtn, kd)`: lookup alternates for `kd.key.toLowerCase()`, uppercase them if `shiftOn || capsOn`, position over `keyBtn`, open.
- On `KeyButton.onKeyLongPressed`: if `root.longPressAlternates` and the key has alternates, call `showAlternates`.
- On popup `alternateChosen`: `keyboard.pressKey(char)`.
- Pass `enableLongPress: root.longPressAlternates && kd.type === "char"` to each `KeyButton` in the Repeater.

### `keyboard_bridge.py`
- Add `_load_alternates()` to read `data/key_alternates.json` once at startup.
- Add `@Slot(str, result="QVariant") def getAlternates(self, key: str) -> list[str]` returning the list (or `[]`).

## Open questions for when this resumes

1. **Press-on-release timing:** acceptable for slow-motor users when long-press is on? Possibly add a *second* sub-toggle "Long-press alternates: hold-to-preview" that keeps press-on-press but shows the picker over the typed character — user backspaces and picks from the popup if they wanted a variant. Less elegant, no timing change.
2. **Auto-repeat interaction:** Backspace / arrow keys auto-repeat on hold; they have no alternates and `enableLongPress` will be false for them, so this is fine. But if a future alternate set covers e.g. `,` with `;`, watch out for repeat-eligible char keys. Currently no char key opts into repeat.
3. **What does long-press do when `enableLongPress` is on but the key has no alternates?** Plain typing — fall through to the normal release path. The `longPressFired` flag still gates against, so an empty popup never appears.
4. **Localization of alternates:** future i18n work probably wants per-language alternate maps. Keep `key_alternates.json` as the English default; a future "language pack" can ship its own.

## Why this is paused

The user picks deliberate, slow keystrokes. Press-on-release adds latency to every char key whenever the toggle is on, even on keys that have no alternates. Until we have a clean way to either (a) only delay keys that have alternates, or (b) confirm the latency is acceptable in real use, the right-click affordance covers the most-asked-for case (`A` from `a`, `!` from `1`) without any timing change.

Right-click ships in v1.0.14. Revisit this doc when adding a non-English layout or when a user explicitly asks for diacritics.
