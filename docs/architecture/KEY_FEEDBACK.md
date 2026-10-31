# Key feedback: right-click shifted character and key preview bubble

The full write-up behind the CLAUDE.md sections *Right-Click for Shifted Character* and *Key Preview Bubble*.

## Right-Click for Shifted Character

Right-click on a char key types its shifted variant without flipping the sticky shift state - `1` -> `!`, `,` -> `<`, `a` -> `A`. Modifier and special keys are deliberate no-ops. Toggle in *Settings -> Smart Typing -> Input -> "Right-Click for Shifted Character"* (default ON; left-click is unaffected whether on or off). Implementation:
- `KeyButton.qml` exposes a `keyRightPressed` signal. The `MouseArea` accepts both buttons; the right-button branch in `onPressed` returns *before* the auto-repeat timer starts so right-click is always a one-shot. Press visuals + ripple still fire - same tactile feedback as a left-click.
- `Main.qml` per-key `onKeyRightPressed` resolves the output: prefer `kd.shifted` from the layout JSON (covers `1`->`!`, `,`->`<`); fall back to `kd.key.toUpperCase()` for letters; otherwise no-op.
- The handler routes through `keyboard.pressKeyLiteral(rch)`, **not** `pressKey` - the latter would lowercase the chosen `'A'` back to `'a'` (see the `pressKey` watch-out above).

The companion long-press -> accents feature is **not** implemented - see `docs/architecture/LONG_PRESS_ALTERNATES.md` for the design and the reason it's deferred (press-on-release timing change is hostile to slow-motor users until we have a way to scope the latency to keys with alternates).



## Key Preview Bubble

A small bubble floats just above a key showing the character that was actually typed, the same "key preview" pattern phone keyboards use. It fires on **both** left- and right-click. The motivating case is right-click (it sends the shifted variant, and that glyph isn't always the one drawn on the key, so the preview confirms what reached the OS), but left-click previews every typed character too. Toggle in *Settings -> Smart Typing -> Input -> "Show Key Preview Popup"* (default ON). It's a pure visual: there is no Python bridge, the setting is `appSettings.savedKeyPreview` mirrored into `root.keyPreviewEnabled` and restored on launch like any other Qt setting.

### Phone-style press/release timing
The bubble shows on press and hides on release, so during normal typing it's visible only for the tap duration (down to a floor), exactly like Gboard/iOS. It is **not** a fixed-dwell toast. The mechanics:
- `KeyButton.qml` emits a new `keyReleased()` signal from all three "press ended" paths: `onReleased`, `onCanceled`, and `onContainsMouseChanged` when the cursor drags off while pressed (the drag-off case is sometimes the only release signal we get under `WS_EX_NOACTIVATE`). The release emit in `onContainsMouseChanged` is guarded on `_visualPressed` so a pure hover-out doesn't fire it.
- `Main.qml` `showKeyPreview(item, ch)` maps the key's top-center into the overlay and calls `keyPreviewBubble.show()`; the per-key `onKeyReleased: root.hideKeyPreview()` dismisses it.
- The bubble (`keyPreviewBubble`, a `Popup` parented to `Overlay.overlay`, fixed 40x40 so the first show centers before content is measured) has two guard timers: `keyPreviewMinTimer` (110 ms visibility floor so a lightning-fast click still flashes long enough to register instead of opening and closing in the same frame) and `keyPreviewSafetyTimer` (1500 ms force-close in case a release event is dropped and `keyReleased` never arrives). `hide()` defers the close to the min timer when the press was shorter than the floor (via the `pendingHide` flag); otherwise it closes immediately.

Left-click previews use `keyBtn.displayText` (which already reflects shift/caps casing, so it matches what `pressKey` sends); right-click previews use the resolved `rch`. The same press also publishes `keyBtn.pressDx` / `pressDy`, the click's position inside the key, which the three `pressKey*` calls pass to the bridge (see *Click position and the learned pointer bias*). Both call sites are gated on `root.keyPreviewEnabled`. Modifier and special keys do not preview (a bubble over Shift or Backspace isn't "what it typed").

