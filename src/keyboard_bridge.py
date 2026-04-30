"""
Keyboard Bridge - Python backend exposed to QML.

Handles key synthesis (sending keystrokes to the focused application)
using the platform abstraction layer:

- **Linux**: xdotool (X11) or ydotool (Wayland) via subprocess.
- **Windows**: Win32 SendInput API via ctypes, with optional UIAccess
  for elevated-window support (requires EV code-signed binary).

The bridge is platform-agnostic — all OS-specific logic lives in
``src/platform/``.  See ``docs/PLATFORM_ARCHITECTURE.md``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot

# Audio feedback — optional, gracefully degrades if QtMultimedia unavailable
try:
    from PySide6.QtMultimedia import QSoundEffect
    _HAS_AUDIO = True
except ImportError:
    _HAS_AUDIO = False

from .__version__ import __version__ as APP_VERSION
from .analytics import TypingAnalytics
from .platform import create_key_synthesizer
from .platform.base import KeySynthesizerBase
from .platform.password_detect import is_password_field
from .prediction import HybridPredictor, SwipeRecognizer
from .updater import UpdateInfo, check_for_update, download_and_install

_logger = logging.getLogger("KeyboardBridge")


class KeyboardBridge(QObject):
    """
    QObject bridge that connects QML keyboard UI to platform key synthesis.

    This class is exposed to QML as the context property ``"keyboard"``
    (see ``keyboard_app.py``).  It translates UI events into:

    1. **Key synthesis** — delegated to the platform layer
       (``src/platform/``) which handles Linux xdotool/ydotool or
       Windows SendInput transparently.
    2. **Prediction updates** — delegated to the hybrid prediction
       engine (``src/prediction/``).
    3. **Modifier state management** — Shift, Caps, Ctrl, Alt, Win
       with sticky/auto-release behaviour.
    """

    shiftActiveChanged = Signal(bool)
    capsLockActiveChanged = Signal(bool)
    ctrlActiveChanged = Signal(bool)
    altActiveChanged = Signal(bool)
    winActiveChanged = Signal(bool)
    currentLayerChanged = Signal(str)

    # Prediction signals
    predictionsChanged = Signal(list)     # Instant predictions
    predictionsRefined = Signal(list)     # LLM-refined predictions
    predictionLoading = Signal(bool)      # LLM loading state
    llmEnabledChanged = Signal(bool)      # LLM enabled state
    llmAvailableChanged = Signal(bool)    # LLM available state
    predictionCountChanged = Signal(int)  # Prediction count changed
    predictionStatsChanged = Signal()     # Stats updated

    # Audio signals
    audioEnabledChanged = Signal(bool)

    # Layout signals
    layoutChanged = Signal(str)
    layoutDataChanged = Signal(list)

    # Debug signals
    debugModeChanged = Signal(bool)
    debugLogChanged = Signal(list)

    # Privacy signals
    privacyModeChanged = Signal(bool)

    # Auto-update signals — version, asset_name, notes (release-notes
    # markdown, already sanitised by the updater).  ``updateUnavailable``
    # fires after a manual "Check now" that found nothing — it lets the
    # UI distinguish "no newer version" from "still checking".
    updateAvailable = Signal(str, str, str)
    updateUnavailable = Signal()
    updateInstallStarted = Signal()
    updateInstallFailed = Signal(str)

    # Edit-mode signals — when the prediction-edit popup is open, OSK
    # keystrokes must target its TextField, not the OS-focused app
    # behind us (we can't steal OS focus without breaking the rest of
    # the keyboard). QML calls setEditMode(True) when the popup opens,
    # we short-circuit pressKey/pressSpecialKey to emit these signals
    # instead, and QML mutates the TextField directly.
    editKeyTyped = Signal(str)          # char to insert at cursor
    editSpecialPressed = Signal(str)    # special key name (backspace, left, etc.)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._shift_active = False
        self._caps_lock_active = False
        self._ctrl_active = False
        self._alt_active = False
        self._win_active = False
        self._current_layer = "lower"  # "lower", "upper", "numbers", "symbols"
        self._edit_mode_active = False  # prediction-edit popup open → redirect OSK keys

        # Create platform-appropriate key synthesizer
        self._synth: KeySynthesizerBase = create_key_synthesizer()
        if self._synth.is_available():
            _logger.info("Key synthesis backend: %s", self._synth.backend_name())
        else:
            _logger.warning(
                "Key synthesis not available (%s). "
                "Keystrokes will not be sent to other applications.",
                self._synth.backend_name(),
            )

        # Defensive: clear any modifier left stuck at the OS level by a
        # previous alpha-osk that crashed or was killed mid-chord. A
        # stuck Ctrl/Alt silently breaks clicks in other apps (e.g. the
        # browser starts treating every click as Ctrl+click / Alt+click)
        # and the OSK button wouldn't show it active because Python
        # tracks its own flag, not the server's. Safe here — the user
        # hasn't started interacting yet.
        self._synth.reset_modifier_state()

        # Initialize prediction engine (LLM disabled by default - overkill for keyboard)
        self._predictor = HybridPredictor(enable_llm=False, parent=self)
        self._predictor.predictionsReady.connect(self._on_predictions_ready)
        self._predictor.predictionsRefined.connect(self._on_predictions_refined)
        self._predictor.modelLoading.connect(self.predictionLoading.emit)
        self._predictor.llmAvailableChanged.connect(self.llmAvailableChanged.emit)
        _logger.info("Prediction engine initialized")

        # Prediction settings
        self._prediction_count = 8
        self._debug_mode = False
        self._debug_log: List[str] = []

        # Keyboard layout
        self._layouts: Dict[str, Any] = {}
        self._current_layout = "qwerty"
        self._load_layouts()

        # Audio feedback
        self._audio_enabled = False
        self._click_sound: Optional[Any] = None
        if _HAS_AUDIO:
            sound_path = Path(__file__).parent.parent / "data" / "sounds" / "click.wav"
            if sound_path.exists():
                self._click_sound = QSoundEffect(self)
                self._click_sound.setSource(QUrl.fromLocalFile(str(sound_path)))
                self._click_sound.setVolume(0.3)
                _logger.info("Audio feedback available")
            else:
                _logger.info("Click sound not found: %s", sound_path)
        else:
            _logger.info("QtMultimedia not available, audio feedback disabled")

        # Analytics
        self._analytics = TypingAnalytics(parent=self)

        # Context tracking for predictions
        self._context_buffer = ""
        self._current_word = ""
        self._sentence_buffer = ""  # Accumulates words for sentence-level learning
        self._predictions: List[str] = []
        self._auto_space_after_punctuation = True
        self._auto_capitalize_after_punctuation = False
        self._auto_save_on_exit = True
        # True iff the most recent character sent to the OS was a space
        # that *we* auto-inserted (after a prediction click or after
        # punctuation).  Used to decide whether the punctuation-spacing
        # cleanup ("hello " + "." → "hello.") should fire: only clean up
        # our own auto-space, never the user's manually-typed space.
        # Reset on any subsequent keystroke.
        self._auto_space_pending = False
        # Space-time autocorrect — replace the typed word with a known
        # correction when space lands.  Falls through silently if the
        # word is in the dictionary (no false positives) or no good
        # correction is available.  See HybridPredictor.check_autocorrect.
        self._autocorrect_enabled = True

        # Swipe / glide typing — off by default, toggled in settings.
        # The recognizer needs the keyboard layout (key centres) before it
        # can decode anything; QML pushes that via setSwipeLayout().
        self._swipe_enabled = False
        self._swipe = SwipeRecognizer()

        # Privacy mode — suppresses prediction and learning
        self._privacy_mode = False
        self._privacy_mode_manual = False   # User toggled manually
        self._password_detect_enabled = True
        # Last synchronous is_password_field() call, to rate-limit the
        # sync check fired on every keystroke (COM calls are cheap but
        # not free; ~50 ms between calls stops thrashing).
        self._last_sync_password_check: float = 0.0

        # Poll for password fields every 200ms (fast detection reduces keystroke leakage)
        self._password_timer = QTimer(self)
        self._password_timer.setInterval(200)
        self._password_timer.timeout.connect(self._check_password_field)
        self._password_timer.start()

        # Monitor foreground window changes to clear predictions when user
        # switches apps. WS_EX_NOACTIVATE means onActiveChanged doesn't fire
        # reliably in QML, so we poll from Python instead.
        self._last_foreground_hwnd = 0
        self._foreground_timer = QTimer(self)
        self._foreground_timer.setInterval(250)
        self._foreground_timer.timeout.connect(self._check_foreground_window)
        self._foreground_timer.start()

        # Auto-update — last fetched UpdateInfo, used by installUpdate()
        # so the QML side doesn't have to round-trip the URL/asset name
        # back through Python (and so we never trust QML-supplied URLs).
        self._update_info: Optional[UpdateInfo] = None
        self._update_check_in_flight = False

    # --- Key synthesis (delegated to platform layer) ---

    def _send_key(self, key_name: str) -> None:
        """
        Send a single key event via the platform synthesizer.

        Automatically attaches any active sticky modifiers (Ctrl, Alt, Win)
        to the keystroke.
        """
        # Gather active modifiers
        modifiers = []
        if self._shift_active:
            modifiers.append("shift")
        if self._ctrl_active:
            modifiers.append("ctrl")
        if self._alt_active:
            modifiers.append("alt")
        if self._win_active:
            modifiers.append("win")

        self._synth.send_key(key_name, modifiers=modifiers if modifiers else None)

    def _send_text(self, text: str) -> None:
        """Send a string of text via the platform synthesizer."""
        self._synth.send_text(text)

    @staticmethod
    def _match_case(typed: str, replacement: str) -> str:
        """Return ``replacement`` cased to match the typed word.

        - All-uppercase typed → uppercase replacement.
        - Title-cased typed (first letter capital, rest lowercase) →
          title-cased replacement.
        - Otherwise → replacement as-is (preserves intentional internal
          capitals like "iPhone" coming out of the misspellings table).
        """
        if not typed:
            return replacement
        if typed.isupper():
            return replacement.upper()
        if typed[0].isupper() and typed[1:].islower():
            return replacement[:1].upper() + replacement[1:]
        return replacement

    # --- QML Slots ---

    # Punctuation that should not have a space before them
    _NO_SPACE_BEFORE = {"?", "!", ".", ",", ";", ":", ")", "]", "}"}

    @Slot(bool)
    def setEditMode(self, active: bool) -> None:
        """Route OSK keystrokes to the QML edit popup instead of the OS.

        Called from QML when the prediction-edit popup opens/closes.
        While active, pressKey/pressSpecialKey emit editKeyTyped /
        editSpecialPressed instead of synthesising to the OS, so the
        popup's TextField can insert them directly. Shift/caps still
        affect letter case; other sticky modifiers (ctrl/alt/win) are
        ignored while editing — chords make no sense inside a 30-char
        edit field, and leaking a Ctrl+V into the OS app behind us
        would be surprising.
        """
        self._edit_mode_active = active

    @Slot(str)
    def pressKey(self, key: str) -> None:
        """Called from QML when a character key is pressed.

        Applies shift / caps-lock case normalization to `key`. For a
        "type this character verbatim" path (e.g. right-click → shifted
        variant where QML has already picked the exact character to
        send), use :meth:`pressKeyLiteral` instead.
        """
        self._press_char(key, literal=False)

    @Slot(str)
    def pressKeyLiteral(self, char: str) -> None:
        """Type ``char`` exactly as-is, bypassing shift / caps-lock case
        normalization.

        Used by the right-click → shifted-variant feature: QML has
        already chosen the desired output (``"!"`` from ``"1"``, ``"A"``
        from ``"a"``) and we must not lowercase it back.  All other
        side effects (analytics, learning, predictions, modifier
        auto-release) match :meth:`pressKey`.
        """
        self._press_char(char, literal=True)

    def _press_char(self, key: str, literal: bool) -> None:
        # Edit-mode intercept: route the character to the popup's
        # TextField instead of the OS. Apply shift/caps for case but
        # skip everything else (password detection, analytics,
        # predictions) — the user is editing a word, not typing.
        if self._edit_mode_active:
            self._play_click()
            if literal:
                char = key
            elif self._shift_active or self._caps_lock_active:
                char = key.upper()
            else:
                char = key.lower()
            self.editKeyTyped.emit(char)
            # Auto-release shift after one keypress (caps lock persists).
            if self._shift_active and not self._caps_lock_active:
                self._shift_active = False
                self._synth.release_modifier("shift")
                self._update_layer()
                self.shiftActiveChanged.emit(False)
            return

        # Close the 200 ms race window: if focus has just landed on a
        # password field, flip privacy mode *before* we touch any
        # prediction state with this keystroke.
        self._check_password_field_sync()
        self._play_click()
        if not self._privacy_mode:
            self._analytics.record_keystroke(key)
        if literal:
            char = key
        elif self._shift_active or self._caps_lock_active:
            char = key.upper()
        else:
            char = key.lower()

        # Handle punctuation spacing — remove preceding space only if WE
        # auto-inserted it (after a prediction click or punctuation auto-
        # space).  Never undo a space the user typed manually: a visible
        # backspace flicker after their own keystroke is surprising and
        # in some apps (rich-text editors, web fields) has side effects
        # like clobbering selection state or undo history.
        if (char in self._NO_SPACE_BEFORE
                and self._auto_space_pending
                and self._context_buffer.endswith(" ")
                and not self._current_word):
            self._send_key("BackSpace")
            self._context_buffer = self._context_buffer[:-1]
            _logger.info("Removed auto-space before '%s'", char)

        # Any keystroke clears the flag — it tracks one specific window:
        # the moment between us inserting an auto-space and the user's
        # immediate next keystroke.  Set again below if this keystroke
        # itself adds an auto-space (after . , ; : ! ?).
        self._auto_space_pending = False

        # Use _send_key for modifier combos (Ctrl+C, Win+Shift+S, etc.)
        # Send the lowercase key — Shift is included as a modifier by _send_key
        if self._ctrl_active or self._alt_active or self._win_active:
            self._send_key(key.lower())
            # Don't update _current_word or predictions — this was a shortcut,
            # not text input. Skip the rest of character handling.
            # Auto-release shift after one keypress (not caps lock)
            if self._shift_active and not self._caps_lock_active:
                self._shift_active = False
                self._synth.release_modifier("shift")
                self._update_layer()
                self.shiftActiveChanged.emit(self._shift_active)
            # Auto-release ctrl/alt/win after one keypress
            if self._ctrl_active:
                self._synth.release_modifier("ctrl")
                self._ctrl_active = False
                self.ctrlActiveChanged.emit(self._ctrl_active)
            if self._alt_active:
                self._synth.release_modifier("alt")
                self._alt_active = False
                self.altActiveChanged.emit(self._alt_active)
            if self._win_active:
                self._synth.release_modifier("win")
                self._win_active = False
                self.winActiveChanged.emit(self._win_active)
            return
        else:
            self._send_text(char)

        # Privacy mode — send keystrokes but don't learn or predict
        if self._privacy_mode:
            # Still handle auto-release of modifiers below, but skip learning
            pass
        else:
            # Update context and get predictions
            self._current_word += char

            # Sentence-ending punctuation triggers sentence learning
            if char in (".", "!", "?"):
                sentence = self._sentence_buffer + self._current_word
                if sentence.strip():
                    new_words = self._predictor.learn(sentence.strip())
                    if new_words:
                        for nw in new_words:
                            self._add_debug_log(f"NEW WORD learned: \"{nw}\"")
                            _logger.info("New word learned: %s", nw)
                self._sentence_buffer = ""
                self._current_word = ""
                if self._auto_space_after_punctuation:
                    self._send_text(" ")
                    self._auto_space_pending = True
                self._context_buffer += char + " "
                # Auto-capitalize next letter
                if self._auto_capitalize_after_punctuation:
                    self._shift_active = True
                    self.shiftActiveChanged.emit(True)
                if len(self._context_buffer) > 200:
                    self._context_buffer = self._context_buffer[-200:]

            # Mid-sentence punctuation — auto-space but no learning/capitalize
            elif char in (",", ";", ":"):
                # Preserve the word before the comma in the sentence buffer
                # (_current_word includes the comma at this point, strip it)
                word_before = self._current_word[:-1]
                if word_before:
                    self._sentence_buffer += word_before + char + " "
                    self._context_buffer += word_before + char + " "
                else:
                    self._context_buffer += char + " "
                self._current_word = ""
                if self._auto_space_after_punctuation:
                    self._send_text(" ")
                    self._auto_space_pending = True
                if len(self._context_buffer) > 200:
                    self._context_buffer = self._context_buffer[-200:]

            # Only show predictions for alphabetic input
            if char.isalpha():
                self._update_predictions()
            else:
                self._predictions = []
                self.predictionsChanged.emit([])

        # Auto-release shift after one keypress (not caps lock)
        if self._shift_active and not self._caps_lock_active:
            self._shift_active = False
            self._synth.release_modifier("shift")
            self._update_layer()
            self.shiftActiveChanged.emit(self._shift_active)

        # Auto-release ctrl/alt/win after one keypress
        if self._ctrl_active:
            self._synth.release_modifier("ctrl")
            self._ctrl_active = False
            self.ctrlActiveChanged.emit(self._ctrl_active)
        if self._alt_active:
            self._synth.release_modifier("alt")
            self._alt_active = False
            self.altActiveChanged.emit(self._alt_active)
        if self._win_active:
            self._synth.release_modifier("win")
            self._win_active = False
            self.winActiveChanged.emit(self._win_active)

    @Slot(str)
    def pressSpecialKey(self, key_name: str) -> None:
        """Called from QML for special keys (Backspace, Return, etc.)."""
        # Edit-mode intercept: let the QML popup handle cursor motion,
        # backspace, return, etc. directly on the TextField instead of
        # sending the keystroke to the OS-focused app.
        if self._edit_mode_active:
            self._play_click()
            self.editSpecialPressed.emit(key_name.lower())
            return

        self._check_password_field_sync()
        self._play_click()
        # Any user-driven special key invalidates the auto-space window —
        # they pressed space themselves, or they're backspacing, or
        # navigating cursor; any subsequent punctuation should not undo
        # whatever space is on screen.
        self._auto_space_pending = False
        key_map = {
            "backspace": "BackSpace",
            "return": "Return",
            "space": "space",
            "tab": "Tab",
            "escape": "Escape",
            "left": "Left",
            "right": "Right",
            "up": "Up",
            "down": "Down",
            "delete": "Delete",
            "home": "Home",
            "end": "End",
            "pageup": "Page_Up",
            "pagedown": "Page_Down",
            "insert": "Insert",
            # Function keys
            "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4",
            "f5": "F5", "f6": "F6", "f7": "F7", "f8": "F8",
            "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
            # Other special keys
            "print": "Print",
            "scrolllock": "Scroll_Lock",
            "pause": "Pause",
            "numlock": "Num_Lock",
        }
        xdotool_key = key_map.get(key_name, key_name)

        # Space-time autocorrect runs *before* the space hits the wire:
        # if the typed word matches a known misspelling or has a high-
        # confidence fuzzy correction, atomically replace the typed
        # letters with the correction and the trailing space in one
        # SendInput call.  Doing it before the space-send avoids a
        # double space and keeps the visible output flicker-free.
        autocorrected = False
        if (
            key_name == "space"
            and self._current_word
            and not self._privacy_mode
            and self._autocorrect_enabled
        ):
            correction = self._predictor.check_autocorrect(
                self._current_word, self._context_buffer,
            )
            if correction and correction.lower() != self._current_word.lower():
                cased = self._match_case(self._current_word, correction)
                self._synth.replace_text(
                    len(self._current_word), cased + " ",
                )
                self._add_debug_log(
                    f"Autocorrected: {self._current_word!r} → {cased!r}"
                )
                _logger.info(
                    "Autocorrected: %r → %r", self._current_word, cased,
                )
                self._current_word = cased
                autocorrected = True

        if not autocorrected:
            self._send_key(xdotool_key)

        # Privacy mode — send the key but don't track context or learn
        if self._privacy_mode:
            pass
        elif key_name == "space":
            # Word completed - learn it and add to sentence
            if self._current_word:
                self._add_debug_log(f"Word completed: \"{self._current_word}\"")
                # Auto-rehabilitate blacklisted words typed repeatedly
                rehabilitated = self._predictor.record_typed_word(self._current_word)
                if rehabilitated:
                    self._add_debug_log(f"Auto-rehabilitated: {rehabilitated}")
                self._analytics.record_word_completed(self._current_word)
                # Learn capitalization from user typing
                if self._predictor.learn_capitalization(self._current_word):
                    self._add_debug_log(f"Learned capitalization: \"{self._current_word}\"")
                    _logger.info("Learned capitalization: %s", self._current_word)
                self._sentence_buffer += self._current_word + " "
                self._context_buffer += self._current_word + " "
                # Learn bigrams/trigrams from the running sentence
                new_words = self._predictor.learn(self._sentence_buffer.strip())
                if new_words:
                    for nw in new_words:
                        self._add_debug_log(f"NEW WORD learned: \"{nw}\"")
                        _logger.info("New word learned: %s", nw)
                # Keep context buffer bounded
                if len(self._context_buffer) > 200:
                    self._context_buffer = self._context_buffer[-200:]
            self._current_word = ""
            self._update_predictions()
        elif key_name == "backspace":
            self._analytics.record_backspace()
            if self._current_word:
                self._current_word = self._current_word[:-1]
                self._update_predictions()
            elif self._context_buffer:
                # Stay in sync with on-screen text: backspace pops one
                # char from the committed context too.  Without this, a
                # stale "." from an earlier sentence stays in the buffer
                # after the user wipes the screen, and the next prediction
                # fires with sentence_start=True (capitalized candidates)
                # on what looks like an empty document.
                self._context_buffer = self._context_buffer[:-1]
                # If the new tail is mid-word (no trailing whitespace),
                # the user has just backspaced *into* a previously-
                # committed word — they're now editing it, not typing a
                # fresh next word.  Move the trailing partial word back
                # into _current_word so the state matches the user's
                # mental model: "the word at the cursor is the one I'm
                # editing."  Without this, prediction clicks took the
                # "no current word" branch and typed the FULL word
                # alongside the on-screen partial, producing
                # "backspacbackspaces"-style duplicates.
                self._rehydrate_current_word_from_context()
                self._update_predictions()
        elif key_name == "return":
            # Sentence boundary - learn full sentence, then reset sentence buffer
            if self._current_word:
                self._add_debug_log(f"Word completed: \"{self._current_word}\"")
                self._analytics.record_word_completed(self._current_word)
                self._sentence_buffer += self._current_word
            if self._sentence_buffer.strip():
                new_words = self._predictor.learn(self._sentence_buffer.strip())
                if new_words:
                    for nw in new_words:
                        self._add_debug_log(f"NEW WORD learned: \"{nw}\"")
                        _logger.info("New word learned: %s", nw)
            self._sentence_buffer = ""
            # Preserve context across lines (don't wipe)
            if self._current_word:
                self._context_buffer += self._current_word + " "
            if len(self._context_buffer) > 200:
                self._context_buffer = self._context_buffer[-200:]
            self._current_word = ""
            self._update_predictions()

        # Auto-release ctrl/alt/win after special key too
        if self._ctrl_active:
            self._synth.release_modifier("ctrl")
            self._ctrl_active = False
            self.ctrlActiveChanged.emit(self._ctrl_active)
        if self._alt_active:
            self._synth.release_modifier("alt")
            self._alt_active = False
            self.altActiveChanged.emit(self._alt_active)
        if self._win_active:
            self._synth.release_modifier("win")
            self._win_active = False
            self.winActiveChanged.emit(self._win_active)

    @Slot()
    def toggleShift(self) -> None:
        """Toggle shift state and hold/release it at the OS level.

        Holding shift at the OS level (the same way Ctrl/Alt/Win work)
        is what makes Shift+click and Shift+drag in the target app
        extend the text selection — same behaviour as the Windows
        on-screen keyboard. Without `hold_modifier`, the OS only sees
        Shift when we attach it as a chord modifier on a synthesised
        keystroke, so a click between Shift-toggle and the next typed
        character lands without Shift held.

        The auto-release sites in `pressKey` mirror the OS-level
        release so a single character keystroke still drops Shift the
        same way it always did.
        """
        self._shift_active = not self._shift_active
        if self._shift_active:
            self._synth.hold_modifier("shift")
        else:
            self._synth.release_modifier("shift")
        self._update_layer()
        self.shiftActiveChanged.emit(self._shift_active)

    @Slot()
    def toggleCapsLock(self) -> None:
        """Toggle caps lock state.

        Caps Lock and Shift are independent — flipping caps no longer also
        toggles shift's visual/active state.  Uppercase output and the
        upper layer are driven by ``_shift_active OR _caps_lock_active``.
        """
        self._caps_lock_active = not self._caps_lock_active
        self._update_layer()
        self.capsLockActiveChanged.emit(self._caps_lock_active)
        # Re-query the engine so currently-visible pills flip case to
        # match the new mode. We can't just uppercase/lowercase the
        # stored list in-place — once predictions are uppercased we've
        # lost the original capitalisation the engine gave us
        # (e.g. "iPhone" vs "IPHONE"), so the engine is the source of
        # truth.
        if self._predictions:
            self._update_predictions()

    @Slot()
    def toggleCtrl(self) -> None:
        """Toggle ctrl modifier (sticky). Holds/releases at the OS level."""
        self._ctrl_active = not self._ctrl_active
        if self._ctrl_active:
            self._synth.hold_modifier("ctrl")
        else:
            self._synth.release_modifier("ctrl")
        self.ctrlActiveChanged.emit(self._ctrl_active)

    @Slot()
    def toggleAlt(self) -> None:
        """Toggle alt modifier (sticky). Holds/releases at the OS level."""
        self._alt_active = not self._alt_active
        if self._alt_active:
            self._synth.hold_modifier("alt")
        else:
            self._synth.release_modifier("alt")
        self.altActiveChanged.emit(self._alt_active)

    @Slot()
    def toggleWin(self) -> None:
        """Toggle Windows/Super modifier (sticky). Holds/releases at the OS level."""
        self._win_active = not self._win_active
        if self._win_active:
            self._synth.hold_modifier("win")
        else:
            self._synth.release_modifier("win")
        self.winActiveChanged.emit(self._win_active)

    @Slot(str)
    def switchLayer(self, layer: str) -> None:
        """Switch keyboard layer (lower, upper, numbers, symbols)."""
        self._current_layer = layer
        self.currentLayerChanged.emit(self._current_layer)

    @Slot(str)
    def pressPrediction(self, word: str) -> None:
        """Called when user taps a prediction suggestion."""
        _logger.info(
            "Prediction selected: '%s' | _current_word='%s' (len=%d) | select=%d",
            word, self._current_word, len(self._current_word), len(self._current_word),
        )

        # Track prediction usage — keystrokes saved = characters user didn't type + space
        rank = self._predictions.index(word) + 1 if word in self._predictions else 1
        saved = len(word) - len(self._current_word) + 1  # +1 for auto-space
        self._analytics.record_prediction_selected(word, rank, keystrokes_saved=max(0, saved))

        # Complete the word by typing only the suffix (characters the user
        # hasn't typed yet) plus a space.  This avoids Backspace and
        # Shift+Left selection, which both break in certain apps:
        # - Backspace empties the field in Slack/Teams/Discord → compose closes
        # - Shift+Left doesn't select text in terminals → leaves duplicates
        # Suffix-only typing works everywhere — but only when the prediction's
        # prefix matches what was typed CASE-SENSITIVELY.  Otherwise the typed
        # lowercase letters survive (e.g. "iph"+"iPhone" → "iphone"), so we
        # fall back to select-and-replace to honour the prediction's casing.
        if word.startswith(self._current_word) and self._current_word:
            # Prediction extends what was typed (same case) — type the rest
            suffix = word[len(self._current_word):] + " "
            self._send_text(suffix)
        elif not self._current_word:
            # Next-word prediction (nothing typed) — type the full word
            self._send_text(word + " ")
        else:
            # Casing differs (e.g. "iph"→"iPhone") or prefix mismatch —
            # select the typed letters and overwrite with the correct word.
            self._synth.replace_text(len(self._current_word), word + " ")
        # All three paths append an auto-space; flag it so the next
        # keystroke (if it's punctuation) can elide it cleanly.
        self._auto_space_pending = True

        # Learn from selection — use context_buffer only, not the typed
        # fragment (_current_word) which is being *replaced* by the prediction.
        self._predictor.learn_from_selection(self._context_buffer, word)

        # Update context - add the completed word
        self._context_buffer += word + " "
        if len(self._context_buffer) > 100:
            self._context_buffer = self._context_buffer[-100:]
        self._current_word = ""

        # IMPORTANT: Clear predictions first, then get next-word predictions
        self._predictions = []
        self.predictionsChanged.emit([])

        # Get next-word predictions immediately
        # Context should end with space to signal "predict next word, not complete current"
        context_for_prediction = self._context_buffer
        _logger.info("Context for next-word prediction: '%s' (ends_with_space=%s)",
                     context_for_prediction, context_for_prediction.endswith(" "))

        next_preds = self._predictor.predict(context_for_prediction, n=self._prediction_count)
        _logger.info("Next-word predictions: %s", next_preds)

        # Update with next-word predictions
        display = self._display_cased(next_preds)
        self._predictions = display
        self.predictionsChanged.emit(display)
        self._add_debug_log(f"Next-word after '{word}': {display}")

    @Slot()
    def clearPredictions(self) -> None:
        """Clear visible predictions when the keyboard loses focus.

        Only clears the displayed predictions, not the typing state
        (_current_word, _context_buffer, _sentence_buffer).  Some apps
        (Slack, browsers) cause rapid focus flickers that would wipe
        tracking state and break the next prediction selection.  The
        predictions will refresh naturally on the next keypress.
        """
        self._predictions = []
        self.predictionsChanged.emit([])

    @Slot()
    def resetContext(self) -> None:
        """Full reset of typing state — for explicit user action only."""
        self._predictions = []
        self._current_word = ""
        self._sentence_buffer = ""
        self._context_buffer = ""
        self.predictionsChanged.emit([])

    # --- Properties for QML ---

    def _get_shift_active(self) -> bool:
        return self._shift_active

    def _get_caps_lock_active(self) -> bool:
        return self._caps_lock_active

    def _get_ctrl_active(self) -> bool:
        return self._ctrl_active

    def _get_alt_active(self) -> bool:
        return self._alt_active

    def _get_win_active(self) -> bool:
        return self._win_active

    def _get_current_layer(self) -> str:
        return self._current_layer

    def _get_synth_available(self) -> bool:
        return self._synth.is_available()

    shiftActive = Property(bool, _get_shift_active, notify=shiftActiveChanged)
    capsLockActive = Property(bool, _get_caps_lock_active, notify=capsLockActiveChanged)
    ctrlActive = Property(bool, _get_ctrl_active, notify=ctrlActiveChanged)
    altActive = Property(bool, _get_alt_active, notify=altActiveChanged)
    winActive = Property(bool, _get_win_active, notify=winActiveChanged)
    currentLayer = Property(str, _get_current_layer, notify=currentLayerChanged)
    synthAvailable = Property(bool, _get_synth_available, constant=True)
    # Exposed so the Settings panel can show the running version next to
    # the auto-update controls — easiest sanity-check that an upgrade
    # actually landed.  Sourced from src/__version__.py at import time.
    appVersion = Property(str, lambda self: APP_VERSION, constant=True)

    # --- Internal ---

    def _update_layer(self) -> None:
        """Update the current layer based on shift/caps state."""
        if self._current_layer in ("numbers", "symbols"):
            return  # Don't change layer if user is on numbers/symbols
        new_layer = "upper" if (self._shift_active or self._caps_lock_active) else "lower"
        if new_layer != self._current_layer:
            self._current_layer = new_layer
            self.currentLayerChanged.emit(self._current_layer)

    def _update_predictions(self) -> None:
        """Request updated predictions from the engine."""
        context = self._context_buffer + self._current_word
        self._predictor.predict_with_refinement(context, n=self._prediction_count)

    def _rehydrate_current_word_from_context(self) -> None:
        """Move a mid-edit partial word from context back into _current_word.

        When Backspace pops a whitespace char off ``_context_buffer``,
        the user has backspaced into a previously-completed word.  The
        invariant the rest of the code relies on — "the word being
        currently edited lives in ``_current_word``" — is broken until
        we rebalance.  This walks the trailing characters of
        ``_context_buffer`` back to the last whitespace and moves them
        to ``_current_word``.  No-op when the tail is already whitespace
        (the user is between words) or when the buffer is empty.
        """
        if not self._context_buffer:
            return
        # Last char is whitespace → already at a word boundary, nothing
        # to rehydrate.
        if self._context_buffer[-1] in (" ", "\n", "\t"):
            return
        # Find the last whitespace.  rfind returns -1 if not found,
        # which is the right pivot for "everything is the partial word."
        last_ws = max(
            self._context_buffer.rfind(" "),
            self._context_buffer.rfind("\n"),
            self._context_buffer.rfind("\t"),
        )
        self._current_word = self._context_buffer[last_ws + 1:]
        self._context_buffer = (
            self._context_buffer[:last_ws + 1] if last_ws >= 0 else ""
        )

    def _display_cased(self, predictions: List[str]) -> List[str]:
        """Transform predictions to match the user's active case mode.

        Two cases:

        1. Caps Lock on — every character the user types is being sent
           uppercase, and `_current_word` accumulates uppercase too.
           Pills must match: showing "hello" while the user typed
           "HELL" misleads about which pill matches the prefix, and
           clicking sends the lowercase form next to an uppercase
           prefix.
        2. One-shot Shift on the first letter — the user typed e.g.
           "Hel" and wants "Hello", not "hello". The display has to
           match for two reasons: (a) the user expects what they see
           to match what they typed, and (b) the suffix-only insert
           path uses a case-sensitive `startswith`, so "hello".
           startswith("Hel") is False and the click would fall through
           to a full replace, clobbering the user's capital H.

        Sentence-start and proper-noun capitalisation are handled
        upstream by :func:`NgramPredictor.get_capitalized`; this layer
        only mirrors the *typed* prefix back into the displayed form.
        """
        if not predictions:
            return predictions
        if self._caps_lock_active:
            return [w.upper() for w in predictions]
        cw = self._current_word
        if cw and cw[0].isupper():
            prefix_lower = cw.lower()
            result = []
            for w in predictions:
                if w and w[0].islower() and w.lower().startswith(prefix_lower):
                    result.append(w[0].upper() + w[1:])
                else:
                    result.append(w)
            return result
        return predictions

    def _on_predictions_ready(self, predictions: List[str]) -> None:
        """Handle instant n-gram predictions."""
        display = self._display_cased(predictions)
        self._predictions = display
        if display:
            self._analytics.record_prediction_offered()
        self.predictionsChanged.emit(display)

    def _on_predictions_refined(self, predictions: List[str]) -> None:
        """Handle LLM-refined predictions."""
        display = self._display_cased(predictions)
        self._predictions = display
        self.predictionsRefined.emit(display)

    @Slot()
    def savePredictionModel(self) -> None:
        """Save the prediction model to disk."""
        self._predictor.save()

    # ------------------------------------------------------------------
    #  Auto-update (see src/updater.py for the security model)
    # ------------------------------------------------------------------

    @Slot()
    def checkForUpdate(self) -> None:
        """Run the GitHub Releases check on a background thread.

        Emits ``updateAvailable(version, asset_name, notes)`` if a newer
        signed installer exists, ``updateUnavailable()`` otherwise.  Both
        signals always fire — the UI uses them to clear a "checking…"
        indicator without polling.

        We deliberately never expose the download URL to QML — QML only
        sees the version + notes, and ``installUpdate`` consults the
        Python-side ``self._update_info`` so a compromised QML can't
        substitute an attacker URL into the install path.
        """
        if self._update_check_in_flight:
            _logger.debug("Update check already running; ignoring duplicate")
            return
        self._update_check_in_flight = True

        import threading

        def _worker() -> None:
            try:
                info = check_for_update()
            except Exception as e:                       # noqa: BLE001
                _logger.warning("Update check raised: %s", e)
                info = None
            finally:
                self._update_check_in_flight = False

            # Qt signals are thread-safe; auto-connection delivers them
            # to the receiver's thread via a queued connection.
            if info is None:
                self._update_info = None
                self.updateUnavailable.emit()
                return
            self._update_info = info
            self.updateAvailable.emit(info.version, info.asset_name, info.notes)

        threading.Thread(target=_worker, name="alpha-osk-update-check",
                         daemon=True).start()

    @Slot()
    def installUpdate(self) -> None:
        """Download + verify + launch the most recently announced update.

        Idempotent — does nothing if no update has been announced yet
        (the QML side should disable the button until ``updateAvailable``
        fires, but we double-check here).
        """
        info = self._update_info
        if info is None:
            _logger.info("installUpdate called with no pending update; ignoring")
            return

        import threading

        def _worker(info: UpdateInfo) -> None:
            self.updateInstallStarted.emit()
            try:
                ok, err = download_and_install(info)
            except Exception as e:                       # noqa: BLE001
                _logger.error("Install raised: %s", e)
                self.updateInstallFailed.emit(str(e))
                return
            if not ok:
                # err is a short, step-specific message ("Download
                # failed", "Signature check failed", ...) so the banner
                # actually tells the user something useful.
                self.updateInstallFailed.emit(err or "Update failed")

        threading.Thread(target=_worker, args=(info,),
                         name="alpha-osk-update-install", daemon=True).start()

    @Slot()
    def dismissUpdate(self) -> None:
        """Forget the pending update without installing.

        Clears the in-memory ``_update_info`` so the install button is
        a no-op until the next ``checkForUpdate()`` finds the release
        again.  Cheap state — we don't bother persisting "dismissed"
        across restarts.
        """
        self._update_info = None

    def shutdown(self) -> None:
        """Stop background timers cleanly before the app tears down.

        Qt can deliver a final ``timeout`` signal on a running ``QTimer``
        while the owning ``KeyboardBridge`` is being destroyed; that
        slot would then run against half-collected attributes (notably
        ``self._predictor``) and crash the exit path.  Calling
        ``shutdown`` from ``QApplication.aboutToQuit`` guarantees the
        timers are stopped while the bridge is still intact.

        Also releases any modifier keys that were held at the OS level
        via sticky toggles (Shift, Ctrl, Alt, Win). Without this,
        quitting with one "active" leaves the X server / Wayland
        compositor thinking it's physically held — so the user's real
        keyboard behaves as though the modifier is stuck until they
        press and release it manually.
        """
        for timer in (
            getattr(self, "_password_timer", None),
            getattr(self, "_foreground_timer", None),
        ):
            if timer is not None:
                try:
                    timer.stop()
                except RuntimeError:
                    pass  # already deleted by Qt; harmless

        if self._shift_active:
            self._synth.release_modifier("shift")
            self._shift_active = False
        if self._ctrl_active:
            self._synth.release_modifier("ctrl")
            self._ctrl_active = False
        if self._alt_active:
            self._synth.release_modifier("alt")
            self._alt_active = False
        if self._win_active:
            self._synth.release_modifier("win")
            self._win_active = False

        # Release the password detector's COM interface + CoInitializeEx
        # token.  Negligible at process exit (the OS reaps it anyway) but
        # makes the lifecycle explicit and lets a hot-reload path tear
        # things down cleanly without leaking COM apartments.
        try:
            from .platform import password_detect
            password_detect.shutdown()
        except Exception:
            pass

    @Slot()
    def clearUserData(self) -> None:
        """Clear user-learned vocabulary and overwrite saved models on disk."""
        self._predictor.clear_user_data()
        # Save immediately so stale model files don't restore old data on restart
        self._predictor.save()
        _logger.info("User data cleared and model files overwritten")

    @Slot()
    def reloadDictionary(self) -> None:
        """Reload the base dictionary."""
        self._predictor.reload_dictionary()
        _logger.info("Dictionary reloaded")

    @Slot(bool)
    def setLlmEnabled(self, enabled: bool) -> None:
        """Enable/disable LLM predictions."""
        self._predictor.enable_llm = enabled
        self.llmEnabledChanged.emit(enabled)
        _logger.info("LLM enabled: %s", enabled)

    @Slot(int)
    def setPredictionCount(self, count: int) -> None:
        """Set number of predictions to show."""
        self._prediction_count = max(1, min(10, count))
        self.predictionCountChanged.emit(self._prediction_count)

    @Slot(bool)
    def setAutoSpaceAfterPunctuation(self, enabled: bool) -> None:
        """Toggle automatic space insertion after sentence-ending punctuation."""
        self._auto_space_after_punctuation = enabled
        _logger.info("Auto-space after punctuation: %s", enabled)

    @Slot(bool)
    def setAutoCapitalizeAfterPunctuation(self, enabled: bool) -> None:
        """Toggle auto-capitalize after sentence-ending punctuation."""
        self._auto_capitalize_after_punctuation = enabled
        _logger.info("Auto-capitalize after punctuation: %s", enabled)

    @Slot(bool)
    def setAutoSaveOnExit(self, enabled: bool) -> None:
        """Toggle auto-save of prediction model when app closes."""
        self._auto_save_on_exit = enabled
        _logger.info("Auto-save on exit: %s", enabled)

    @Slot(bool)
    def setAutocorrectEnabled(self, enabled: bool) -> None:
        """Toggle space-time autocorrect (misspellings + fuzzy)."""
        self._autocorrect_enabled = enabled
        _logger.info("Autocorrect: %s", enabled)

    @property
    def autoSaveOnExit(self) -> bool:
        """Whether to auto-save prediction model on exit."""
        return self._auto_save_on_exit

    # --- Privacy Mode ---

    def _check_foreground_window(self) -> None:
        """Detect when the user switches to a different application.

        Clears predictions and resets typing state since the context is
        now stale for the new window.  On Windows the check is a
        near-free ``GetForegroundWindow()`` call; on X11 it shells out
        to ``xdotool getactivewindow`` (~5 ms at 4 Hz).  Wayland doesn't
        expose the focused window to unprivileged clients, so we skip.
        """
        hwnd = self._get_foreground_window_id()
        if hwnd == 0:
            return  # detection unavailable on this platform
        if hwnd != self._last_foreground_hwnd and self._last_foreground_hwnd != 0:
            # Foreground window changed — user switched apps
            self._predictions = []
            self._current_word = ""
            self._sentence_buffer = ""
            self._context_buffer = ""
            self.predictionsChanged.emit([])
            _logger.debug("Foreground window changed — predictions cleared")
        self._last_foreground_hwnd = hwnd

    def _get_foreground_window_id(self) -> int:
        """Return the focused-window ID, or 0 if unavailable.

        Windows: ``GetForegroundWindow()`` via ctypes.
        X11:    ``xdotool getactivewindow`` subprocess (~5 ms).
        Wayland / other: returns 0 (no supported API).

        Errors are logged once per unique exception type so a recurring
        platform issue (xdotool missing, ACCESS_DENIED, etc.) shows up
        in logs without spamming at the 4 Hz poll cadence.
        """
        import sys
        try:
            if sys.platform == "win32":
                import ctypes
                return int(
                    ctypes.windll.user32.GetForegroundWindow()  # type: ignore[attr-defined]
                )
            if sys.platform.startswith("linux"):
                import os
                import subprocess
                if os.environ.get("WAYLAND_DISPLAY"):
                    return 0
                result = subprocess.run(
                    ["xdotool", "getactivewindow"],
                    capture_output=True, text=True, timeout=0.5, check=False,
                )
                out = result.stdout.strip()
                return int(out) if result.returncode == 0 and out else 0
        except Exception as exc:
            # Dedupe by exception type so a missing xdotool or a transient
            # Win32 access denial doesn't flood logs at 4 Hz.
            seen = getattr(self, "_fg_logged_errors", None)
            if seen is None:
                seen = set()
                self._fg_logged_errors = seen
            key = type(exc).__name__
            if key not in seen:
                seen.add(key)
                _logger.warning("Foreground-window detection failed (%s): %s",
                                key, exc)
            return 0
        return 0

    def _check_password_field_sync(self) -> None:
        """Synchronous password check for keystroke paths.

        The 200 ms background timer alone leaves a leak window where the
        first characters after focus lands on a password field go into
        ``_current_word`` and the prediction cache before privacy mode
        flips.  This wrapper fires on every keystroke but caches the
        result for ~50 ms so the UI Automation COM call doesn't thrash
        under rapid repeats.
        """
        import time
        now = time.monotonic()
        if now - self._last_sync_password_check < 0.05:
            return
        self._last_sync_password_check = now
        self._check_password_field()

    def _check_password_field(self) -> None:
        """Periodic check for password field focus (called by QTimer)."""
        if not self._password_detect_enabled or self._privacy_mode_manual:
            return

        detected = is_password_field()
        if detected != self._privacy_mode:
            self._privacy_mode = detected
            self.privacyModeChanged.emit(detected)
            if detected:
                self._enter_privacy_mode()
                _logger.info("Password field detected — privacy mode ON")
            else:
                _logger.info("Password field cleared — privacy mode OFF")

    def _enter_privacy_mode(self) -> None:
        """Scrub all buffers to prevent sensitive data from leaking to the model."""
        self._predictions = []
        self.predictionsChanged.emit([])
        self._current_word = ""
        self._context_buffer = ""
        self._sentence_buffer = ""

    @Slot(bool)
    def setPrivacyMode(self, enabled: bool) -> None:
        """Manually toggle privacy mode (overrides auto-detection)."""
        self._privacy_mode_manual = enabled
        self._privacy_mode = enabled
        self.privacyModeChanged.emit(enabled)
        if enabled:
            self._enter_privacy_mode()
        _logger.info("Privacy mode manually set: %s", enabled)

    @Slot(bool)
    def setPasswordDetectionEnabled(self, enabled: bool) -> None:
        """Enable/disable automatic password field detection."""
        self._password_detect_enabled = enabled
        _logger.info("Password field detection: %s", enabled)

    def _get_privacy_mode(self) -> bool:
        return self._privacy_mode

    privacyMode = Property(bool, _get_privacy_mode, notify=privacyModeChanged)

    @Slot(result=dict)
    def getPredictionStats(self) -> dict:
        """Get prediction engine statistics."""
        return self._predictor.get_stats()

    @Slot(str, result=bool)
    def importTextFile(self, file_path: str) -> bool:
        """Import a text file to train the prediction model."""
        from pathlib import Path
        path = Path(file_path)
        if not path.exists():
            self._add_debug_log(f"File not found: {file_path}")
            return False
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            self._predictor._ngram.learn(text)
            word_count = len(text.split())
            self._add_debug_log(f"Imported {word_count} words from {path.name}")
            _logger.info("Imported %d words from %s", word_count, file_path)
            return True
        except Exception as e:
            self._add_debug_log(f"Import failed: {e}")
            _logger.error("Failed to import file %s: %s", file_path, e)
            return False

    @Slot(str, result=int)
    def importFolder(self, folder_path: str) -> int:
        """Import all text files from a folder."""
        from pathlib import Path
        path = Path(folder_path)
        if not path.is_dir():
            self._add_debug_log(f"Folder not found: {folder_path}")
            return 0

        count = 0
        extensions = [".txt", ".md", ".py", ".js", ".html", ".css", ".json"]
        for ext in extensions:
            for file in path.glob(f"**/*{ext}"):
                if self.importTextFile(str(file)):
                    count += 1

        self._add_debug_log(f"Imported {count} files from {path.name}")
        return count

    @Slot(bool)
    def setDebugMode(self, enabled: bool) -> None:
        """Enable/disable debug mode."""
        self._debug_mode = enabled
        self.debugModeChanged.emit(enabled)
        self._add_debug_log(f"Debug mode: {'ON' if enabled else 'OFF'}")

    @Slot(result=list)
    def getDebugLog(self) -> List[str]:
        """Get recent debug log entries."""
        return self._debug_log[-50:]  # Last 50 entries

    @Slot()
    def clearDebugLog(self) -> None:
        """Clear the debug log."""
        self._debug_log.clear()
        self.debugLogChanged.emit([])

    def _add_debug_log(self, message: str) -> None:
        """Add a message to the debug log (only when debug mode is active)."""
        if not self._debug_mode:
            return
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}"
        self._debug_log.append(entry)
        if len(self._debug_log) > 100:
            self._debug_log = self._debug_log[-100:]
        self.debugLogChanged.emit(self._debug_log)

    @Slot(str, result=str)
    def checkAutocorrect(self, typed_word: str) -> str:
        """
        Check if a word should be autocorrected.

        Returns corrected word or empty string if no correction.
        """
        correction = self._predictor.check_autocorrect(typed_word, self._context_buffer)
        if correction:
            self._add_debug_log(f"Autocorrect: {typed_word} -> {correction}")
            return correction
        return ""

    @Slot(str, result=list)
    def getKeyAlternatives(self, key: str) -> list:
        """
        Get probability distribution over intended keys.

        Returns list of [key, probability] pairs.
        """
        probs = self._predictor.get_key_alternatives(key)
        return [[k, v] for k, v in sorted(probs.items(), key=lambda x: -x[1])[:5]]

    # --- Vocabulary Packs ---

    @Slot(result=list)
    def getAvailablePacks(self) -> list:
        """Get metadata for all available vocabulary packs."""
        return self._predictor.get_available_packs()

    @Slot(result=list)
    def getEnabledPacks(self) -> list:
        """Get list of enabled pack IDs."""
        return self._predictor.get_enabled_packs()

    @Slot(str, result=bool)
    def enableVocabularyPack(self, pack_id: str) -> bool:
        """Enable a vocabulary pack by ID (e.g., 'medical', 'programming')."""
        result = self._predictor.enable_vocabulary_pack(pack_id)
        if result:
            self._add_debug_log(f"Vocabulary pack enabled: {pack_id}")
        return result

    @Slot(str, result=bool)
    def disableVocabularyPack(self, pack_id: str) -> bool:
        """Disable a vocabulary pack by ID."""
        result = self._predictor.disable_vocabulary_pack(pack_id)
        if result:
            self._add_debug_log(f"Vocabulary pack disabled: {pack_id}")
        return result

    @Slot(str, result=str)
    def importVocabularyPack(self, folder_path: str) -> str:
        """Import a custom vocabulary pack from a folder. Returns pack ID or empty."""
        pack_id = self._predictor.import_vocabulary_pack(folder_path)
        if pack_id:
            self._add_debug_log(f"Imported vocabulary pack: {pack_id}")
        else:
            self._add_debug_log(f"Failed to import pack from: {folder_path}")
        return pack_id

    @Slot(result=str)
    def getUserPacksDir(self) -> str:
        """Get the user custom packs directory path."""
        return self._predictor.get_user_packs_dir()

    # --- Word Suppression ---

    @Slot(str)
    def blacklistWord(self, word: str) -> None:
        """Remove a word from all future predictions."""
        self._predictor.blacklist_word(word)
        # Refresh predictions to remove it immediately
        self._predictions = [w for w in self._predictions if w.lower() != word.lower()]
        self.predictionsChanged.emit(self._predictions)
        self._add_debug_log(f"Blacklisted: {word}")

    @Slot(str)
    def markBadSuggestion(self, word: str) -> None:
        """Downweight a word in future predictions."""
        self._predictor.mark_bad_suggestion(word)
        self._add_debug_log(f"Marked bad: {word}")

    @Slot(str)
    def unblacklistWord(self, word: str) -> None:
        """Restore a previously blacklisted word to predictions."""
        self._predictor.unblacklist_word(word)
        self._add_debug_log(f"Unblacklisted: {word}")

    @Slot(str)
    def undisprefer(self, word: str) -> None:
        """Remove dispreference penalty from a word."""
        self._predictor.remove_dispreference(word)
        self._add_debug_log(f"Removed dispreference: {word}")

    # Maximum length for a user-edited prediction.  Well above any real
    # word; the cap exists to stop a malformed QML call from persisting
    # a 10 KB string into the capitalisation table.
    _MAX_EDIT_LEN = 64

    @staticmethod
    def _sanitize_edit(value: str) -> str:
        """Clean a user-typed prediction edit before it reaches the model.

        Strips surrounding whitespace and control characters (NUL,
        newlines, other C0/C1), caps the length, and returns '' if
        nothing survives.  Called from :meth:`editPrediction` — the
        edited text is persisted into ``capitalization`` and surfaces
        in every future prediction, so garbage must be rejected here
        rather than downstream.
        """
        if not isinstance(value, str):
            return ""
        cleaned = "".join(ch for ch in value if ch == " " or (ch.isprintable() and ord(ch) >= 0x20))
        cleaned = cleaned.strip()
        if len(cleaned) > KeyboardBridge._MAX_EDIT_LEN:
            cleaned = cleaned[: KeyboardBridge._MAX_EDIT_LEN].rstrip()
        return cleaned

    @Slot(str, str)
    def editPrediction(self, original: str, edited: str) -> None:
        """User edited a prediction (e.g. to fix capitalization). Insert it and learn."""
        edited = self._sanitize_edit(edited)
        if not edited:
            return

        # Learn the preferred capitalization
        self._predictor.set_capitalization(edited, edited)

        # Insert the edited word (same as pressPrediction but with edited text)
        self._synth.replace_text(len(self._current_word), edited + " ")

        # Update context
        self._context_buffer += edited + " "
        if len(self._context_buffer) > 100:
            self._context_buffer = self._context_buffer[-100:]
        self._current_word = ""

        # Refresh predictions
        self._predictions = []
        self.predictionsChanged.emit([])
        next_preds = self._predictor.predict(self._context_buffer, n=self._prediction_count)
        display = self._display_cased(next_preds)
        self._predictions = display
        self.predictionsChanged.emit(display)

        self._add_debug_log(f"Edited prediction: {original} → {edited}")
        _logger.info("Prediction edited: %s → %s", original, edited)

    # --- Swipe / Glide Typing ---

    swipeEnabledChanged = Signal(bool)

    @Slot(bool)
    def setSwipeEnabled(self, enabled: bool) -> None:
        """Toggle swipe / glide typing globally."""
        self._swipe_enabled = enabled
        self.swipeEnabledChanged.emit(enabled)
        _logger.info("Swipe typing: %s", enabled)

    def _get_swipe_enabled(self) -> bool:
        return self._swipe_enabled

    swipeEnabled = Property(bool, _get_swipe_enabled, notify=swipeEnabledChanged)

    @Slot("QVariant")
    def setSwipeLayout(self, key_centers: Any) -> None:
        """Push the current keyboard layout to the swipe recognizer.

        QML supplies a ``{letter: [x, y]}`` map of key-centre coordinates
        in any consistent unit (window-local pixels work fine — the
        recognizer normalises internally).
        """
        try:
            # PySide6 hands JS objects across as QJSValue; convert to a
            # native Python dict before iterating.
            try:
                from PySide6.QtQml import QJSValue
                if isinstance(key_centers, QJSValue):
                    key_centers = key_centers.toVariant()
            except ImportError:
                pass

            mapping: Dict[str, tuple] = {}
            items = key_centers.items() if hasattr(key_centers, "items") else key_centers
            for key, value in items:
                if value is None:
                    continue
                if isinstance(value, dict):
                    x, y = value.get("x", 0.0), value.get("y", 0.0)
                else:
                    x, y = value[0], value[1]
                mapping[str(key)] = (float(x), float(y))
            self._swipe.set_layout(mapping)
        except Exception as e:
            _logger.warning("setSwipeLayout failed: %s", e)

    @Slot("QVariant")
    def processSwipe(self, points: Any) -> None:
        """Decode a swipe trace and insert the top candidate.

        Args:
            points: List of ``[x, y]`` pairs from QML, in the same
                    coordinate space as the layout pushed via
                    :meth:`setSwipeLayout`.
        """
        if not self._swipe_enabled or self._privacy_mode:
            return

        # PySide6 may hand JS arrays across as QJSValue; convert to native
        # Python before iterating.
        try:
            from PySide6.QtQml import QJSValue
            if isinstance(points, QJSValue):
                points = points.toVariant()
        except ImportError:
            pass

        try:
            trace = [(float(p[0]), float(p[1])) for p in points]
        except (TypeError, ValueError, IndexError):
            return
        if len(trace) < 4:
            return

        unigrams = self._predictor.get_unigram_freqs()
        results = self._swipe.decode(
            trace,
            unigrams.keys(),
            word_freq=dict(unigrams),
            top_k=self._prediction_count,
        )
        if not results:
            self._add_debug_log("Swipe: no candidates matched")
            return

        # Apply learned/built-in capitalisation to each candidate so that
        # picking the top word respects "iPhone" vs. "iphone".  Sentence
        # start fires only when the trimmed context actually ends with
        # .!? — empty context is *not* a sentence start (matches the
        # n-gram path in HybridPredictor._merge_predictions; see
        # CLAUDE.md "Auto-Capitalization & Proper Nouns" for the why).
        trimmed = self._context_buffer.rstrip()
        sentence_start = bool(trimmed) and trimmed.endswith((".", "!", "?"))
        capitalised = [
            self._predictor.get_capitalized(w, sentence_start) for w in results
        ]

        display = self._display_cased(capitalised)
        top = display[0]
        self._send_text(top + " ")
        self._context_buffer += top + " "
        self._sentence_buffer += top + " "
        if len(self._context_buffer) > 200:
            self._context_buffer = self._context_buffer[-200:]
        self._current_word = ""

        # Show the rest as alternative predictions in case the top guess is wrong.
        self._predictions = display
        self.predictionsChanged.emit(display)
        self._analytics.record_word_completed(top)
        self._add_debug_log(f"Swipe → {top} (alts: {display[1:4]})")
        _logger.info("Swipe decoded: %s (alts: %s)", top, display[1:4])

    # --- Audio Feedback ---

    def _play_click(self) -> None:
        """Play key click sound if audio is enabled."""
        if self._audio_enabled and self._click_sound is not None:
            self._click_sound.play()

    @Slot(bool)
    def setAudioEnabled(self, enabled: bool) -> None:
        """Enable or disable audio feedback."""
        self._audio_enabled = enabled
        self.audioEnabledChanged.emit(enabled)

    def _get_audio_enabled(self) -> bool:
        return self._audio_enabled

    audioEnabled = Property(bool, _get_audio_enabled, notify=audioEnabledChanged)

    @Slot(result=bool)
    def isAudioAvailable(self) -> bool:
        """Check if audio feedback hardware is available."""
        return self._click_sound is not None

    # --- Keyboard Layouts ---

    def _load_layouts(self) -> None:
        """Load all keyboard layout JSON files from data/layouts/."""
        layouts_dir = Path(__file__).parent.parent / "data" / "layouts"
        if not layouts_dir.exists():
            _logger.warning("Layouts directory not found: %s", layouts_dir)
            return
        for path in layouts_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                layout_id = data.get("id", path.stem)
                self._layouts[layout_id] = data
                _logger.info("Loaded layout: %s", layout_id)
            except (json.JSONDecodeError, OSError) as e:
                _logger.warning("Failed to load layout %s: %s", path.name, e)

    @Slot(result=list)
    def getAvailableLayouts(self) -> list:
        """Return list of {id, name} dicts for available layouts."""
        return [
            {"id": lid, "name": data.get("name", lid)}
            for lid, data in self._layouts.items()
        ]

    @Slot(result=str)
    def getCurrentLayout(self) -> str:
        """Return current layout id."""
        return self._current_layout

    @Slot(str)
    def setLayout(self, layout_id: str) -> None:
        """Switch to a different keyboard layout."""
        if layout_id in self._layouts and layout_id != self._current_layout:
            self._current_layout = layout_id
            self.layoutChanged.emit(layout_id)
            self.layoutDataChanged.emit(self._layouts[layout_id].get("rows", []))
            self._add_debug_log(f"Layout changed to: {layout_id}")

    @Slot(result=list)
    def getLayoutRows(self) -> list:
        """Return the current layout's row data for QML rendering."""
        layout = self._layouts.get(self._current_layout, {})
        rows: list = layout.get("rows", [])
        return rows

    # --- Analytics ---

    @Slot(result="QVariant")
    def getAnalytics(self) -> Dict[str, Any]:
        """Return session + all-time analytics for the QML dashboard."""
        stats: Dict[str, Any] = self._analytics.get_session_stats()
        return stats

    @Slot()
    def saveAnalytics(self) -> None:
        """Save analytics to disk."""
        self._analytics.save()

    @Slot(result="QVariant")
    def getVisualizationData(self) -> Dict[str, Any]:
        """Return language-model data for the visualisation panel."""
        ngram = self._predictor._ngram

        # Top words by frequency — only words the user has actually typed
        user_words: dict[str, int] = {}
        for w, c in ngram.user_vocab.items():
            if w not in ngram.blacklist:
                user_words[w] = c
        sorted_words = sorted(user_words.items(), key=lambda x: x[1], reverse=True)[:100]

        # Bigram edges — only between user-typed words
        top_word_set = {w for w, _ in sorted_words[:40]}
        edges: list[dict] = []
        for prev, nexts in ngram.bigrams.items():
            if prev not in top_word_set:
                continue
            for nxt, cnt in nexts.items():
                if nxt in top_word_set and nxt in ngram.user_vocab and cnt >= 2:
                    edges.append({"from": prev, "to": nxt, "count": cnt})
        edges.sort(key=lambda e: e["count"], reverse=True)
        edges = edges[:150]

        # Stats
        stats = ngram.get_stats()
        stats["blacklistCount"] = len(ngram.blacklist)
        stats["dispreferenceCount"] = len(ngram.dispreference)
        stats["blacklist"] = list(ngram.blacklist)[:30]
        stats["dispreference"] = [
            {"word": w, "count": c}
            for w, c in sorted(ngram.dispreference.items(), key=lambda x: x[1], reverse=True)[:20]
        ]

        # Analytics
        analytics = self._analytics.get_session_stats()

        return {
            "words": [{"word": w, "count": c} for w, c in sorted_words],
            "edges": edges,
            "stats": stats,
            "analytics": analytics,
        }

    # --- Prediction Properties ---

    def _get_predictions(self) -> List[str]:
        return self._predictions

    def _get_llm_enabled(self) -> bool:
        return self._predictor.enable_llm

    def _get_llm_available(self) -> bool:
        return self._predictor.llm_available

    def _get_prediction_count(self) -> int:
        return getattr(self, '_prediction_count', 5)

    predictions = Property(list, _get_predictions, notify=predictionsChanged)
    llmEnabled = Property(bool, _get_llm_enabled, notify=llmEnabledChanged)
    llmAvailable = Property(bool, _get_llm_available, notify=llmAvailableChanged)
    predictionCount = Property(int, _get_prediction_count, notify=predictionCountChanged)
