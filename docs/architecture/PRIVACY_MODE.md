# Privacy mode and password detection

This document holds the full write-up behind the CLAUDE.md section *Privacy Mode & Password Detection*, including its subsection on clearing stale typing context (which has its own deeper doc, `CONTEXT_RESET.md`).

## Privacy Mode & Password Detection

Protects sensitive input (passwords, PINs) from leaking into the prediction model.

### How it works
- **Auto-detection** (Windows): Two complementary paths call `is_password_field()` from `src/platform/password_detect.py`:
  1. A background `QTimer` polls every 200ms (`_check_password_field`). Catches focus changes that happen between keystrokes.
  2. **Every keystroke** (`pressKey`/`pressSpecialKey`) also calls `_check_password_field_sync()`, rate-limited to ~50ms via `_last_sync_password_check`. Closes the race window where the first characters after focus lands on a password field would otherwise reach the prediction cache before the timer fires.
- Detection uses Windows UI Automation COM (`IUIAutomation::GetFocusedElement` -> `UIA_IsPasswordPropertyId`) in native apps and browsers. Falls back to Win32 `EM_GETPASSWORDCHAR` if UIA fails.
- **Manual toggle**: the **Learning** switch in the title bar (static label + a sliding knob; accent when on, red when paused). Overrides auto-detection. Two earlier designs (a play/pause Canvas icon, then a text label that swapped between "Learning" and "Paused") both had to be read and interpreted; see the title-bar entries in `docs/architecture/GOTCHAS.md` for the full rationale.
- **When auto-detection has no working backend this session** (`detection_available()` in `password_detect.py`, surfaced read-only as `KeyboardBridge.passwordDetectionAvailable`): the null-detector fallback now logs a WARNING at startup instead of failing silently, and the UI says so in two low-key places rather than a dialog to dismiss: the Learning switch's tooltip appends "(auto-detect unavailable this session: this is your only protection)", and *Settings -> Data & Privacy -> Privacy* shows an amber note pointing at the switch. Detection fails open by design, so a backend that can never turn on still lets typing proceed; the manual toggle is the only thing between a password field and the model until it's fixed.
- **When active**: Keystrokes still reach the OS, but `_current_word`, predictions, and learning are all suppressed. The prediction bar shows "Learning paused".

### Clearing stale context when focus or the caret moves

Full write-up: `docs/architecture/CONTEXT_RESET.md` (section of the same name). Read it before changing this area.

Six signals reset the typing context, each catching what the others cannot: (1) foreground window, (2) focused element (UIA RuntimeId), (3) caret position, all polled at 4 Hz by `_check_foreground_window`; (4) a click outside our own process (`src/platform/pointer.py`, its own 50 ms poll); (5) Tab; (6) the `_NAV_KEYS`. **Stale `_current_word` / `_context_buffer` is not a bad suggestion, it is a pill tap that eats text elsewhere.**

- `None` from a token means "don't know" and leaves state untouched.
- **The outside click (4) resets mid-word; `_check_caret_moved` keeps its only-between-words guard. Don't re-unify them.** The old `_external_click_pending` deferral is deleted.
- Where a caret is published, a click is settled over `_CLICK_SETTLE_MS` (200 ms) against `_caret_before_click` (the previous tick's reading, never `_last_caret_token`); an unreadable caret resets. `_note_own_keystroke` sets both `_keystroke_since_poll` and `_keystroke_since_click_poll`, and it is set in the synth wrappers (`_send_key` / `_send_text` / `_replace_text`), not the keystroke entry points. `_reset_typing_context` clears a pending settle. A scrollbar click still resets.
- All three within-window resets pass `keep_snippet_offer=True`; an app switch and privacy mode withdraw the offer.
- **The tail of an interrupted word is never learned** (`_word_prefix_lost`, `_take_lost_prefix`). Tests for it must type the word three times.
- Own-window clicks are filtered by process id, not geometry. It polls rather than hooking, and reads **only the high bit** of `GetAsyncKeyState` (the low bit is system-wide and the read clears it for other assistive tools).
- Tab does not learn the word in progress (it is the accept-completion key). Delete and Escape are excluded from the nav-key reset. A held arrow resets once.
- `resetContext` (the clear-context ring) delegates to `_reset_typing_context`.
- Tests: `TestTabClearsContext`, `TestCursorMotionClearsContext`, `TestOutsideClickClearsContext`, `TestAnOutsideClickThatMovesNoCaretIsLeftAlone`, `TestCaretMoveClearsContext`, `TestAnInterruptedWordIsNotLearned`, `tests/test_pointer.py`.

### Key files
- `src/platform/password_detect.py` - platform-specific detection (UIA COM via ctypes), plus `focused_element_token` / `caret_position_token`
- `src/platform/pointer.py` - `external_click_detected()`, the outside-click probe behind signal (4) above (Windows only, no-op elsewhere)
- `src/keyboard_bridge.py` - `_privacy_mode` flag, `_check_password_field()` timer, `_check_password_field_sync()` per-keystroke, `setPrivacyMode()` slot, `passwordDetectionAvailable` read-only property

### Linux
Auto-detection uses AT-SPI 2 via `gi.repository.Atspi`. A daemon thread owns a GLib event loop and listens for `object:state-changed:focused`; whenever focus lands on an accessible whose state set contains `STATE_PASSWORD_TEXT`, the shared `_is_password` flag flips on. Works for GTK (`GtkEntry` with `visibility=false`), Qt (`QLineEdit` in Password echo mode), and browsers that expose accessibility metadata. Requires `gir1.2-atspi-2.0` + a working at-spi bus on the host. If `gi` fails to import or `Atspi.init()` fails, falls back to the null detector and now logs a WARNING once at startup instead of failing silently (`detection_available()` surfaces this to the UI, see *How it works* above) - users can still toggle privacy mode manually.
