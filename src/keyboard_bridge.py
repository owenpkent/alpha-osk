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

from .analytics import TypingAnalytics
from .platform import create_key_synthesizer
from .platform.base import KeySynthesizerBase
from .platform.password_detect import is_password_field
from .prediction import HybridPredictor

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

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._shift_active = False
        self._caps_lock_active = False
        self._ctrl_active = False
        self._alt_active = False
        self._win_active = False
        self._current_layer = "lower"  # "lower", "upper", "numbers", "symbols"

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

        # Privacy mode — suppresses prediction and learning
        self._privacy_mode = False
        self._privacy_mode_manual = False   # User toggled manually
        self._password_detect_enabled = True

        # Poll for password fields every 200ms (fast detection reduces keystroke leakage)
        self._password_timer = QTimer(self)
        self._password_timer.setInterval(200)
        self._password_timer.timeout.connect(self._check_password_field)
        self._password_timer.start()

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

    # --- QML Slots ---

    # Punctuation that should not have a space before them
    _NO_SPACE_BEFORE = {"?", "!", ".", ",", ";", ":", ")", "]", "}"}

    @Slot(str)
    def pressKey(self, key: str) -> None:
        """Called from QML when a character key is pressed."""
        self._play_click()
        if not self._privacy_mode:
            self._analytics.record_keystroke(key)
        if self._shift_active or self._caps_lock_active:
            char = key.upper()
        else:
            char = key.lower()

        # Handle punctuation spacing - remove preceding space only if the
        # space is actually the last thing on screen (no partial word after it)
        if (char in self._NO_SPACE_BEFORE
                and self._context_buffer.endswith(" ")
                and not self._current_word):
            # Delete the trailing space before typing punctuation
            self._send_key("BackSpace")
            self._context_buffer = self._context_buffer[:-1]
            _logger.info("Removed space before '%s'", char)

        # Use _send_key for modifier combos (Ctrl+C, Win+Shift+S, etc.)
        # Send the lowercase key — Shift is included as a modifier by _send_key
        if self._ctrl_active or self._alt_active or self._win_active:
            self._send_key(key.lower())
            # Don't update _current_word or predictions — this was a shortcut,
            # not text input. Skip the rest of character handling.
            # Auto-release shift after one keypress (not caps lock)
            if self._shift_active and not self._caps_lock_active:
                self._shift_active = False
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
        self._play_click()
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
            # Remove last character from current word
            self._analytics.record_backspace()
            if self._current_word:
                self._current_word = self._current_word[:-1]
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
        """Toggle shift state."""
        self._shift_active = not self._shift_active
        self._update_layer()
        self.shiftActiveChanged.emit(self._shift_active)

    @Slot()
    def toggleCapsLock(self) -> None:
        """Toggle caps lock state."""
        self._caps_lock_active = not self._caps_lock_active
        if self._caps_lock_active:
            self._shift_active = True
        else:
            self._shift_active = False
        self._update_layer()
        self.capsLockActiveChanged.emit(self._caps_lock_active)
        self.shiftActiveChanged.emit(self._shift_active)

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
        _logger.info("Prediction selected: %s", word)

        # Track prediction usage — keystrokes saved = characters user didn't type + space
        rank = self._predictions.index(word) + 1 if word in self._predictions else 1
        saved = len(word) - len(self._current_word) + 1  # +1 for auto-space
        self._analytics.record_prediction_selected(word, rank, keystrokes_saved=max(0, saved))

        # Complete the word: erase typed prefix, then type full word + space.
        # Using backspace-then-retype is more robust than sending only the
        # remaining suffix, because _current_word can drift out of sync with
        # what's actually on screen (e.g. after modifier keys or focus changes).
        # The replace is done atomically (single SendInput call on Windows) so
        # the target app can't interleave other events between the backspaces
        # and the replacement text.
        self._synth.replace_text(len(self._current_word), word + " ")

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
        self._predictions = next_preds
        self.predictionsChanged.emit(next_preds)
        self._add_debug_log(f"Next-word after '{word}': {next_preds}")

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

    # --- Internal ---

    def _update_layer(self) -> None:
        """Update the current layer based on shift/caps state."""
        if self._current_layer in ("numbers", "symbols"):
            return  # Don't change layer if user is on numbers/symbols
        new_layer = "upper" if self._shift_active else "lower"
        if new_layer != self._current_layer:
            self._current_layer = new_layer
            self.currentLayerChanged.emit(self._current_layer)

    def _update_predictions(self) -> None:
        """Request updated predictions from the engine."""
        context = self._context_buffer + self._current_word
        self._predictor.predict_with_refinement(context, n=self._prediction_count)

    def _on_predictions_ready(self, predictions: List[str]) -> None:
        """Handle instant n-gram predictions."""
        self._predictions = predictions
        if predictions:
            self._analytics.record_prediction_offered()
        self.predictionsChanged.emit(predictions)

    def _on_predictions_refined(self, predictions: List[str]) -> None:
        """Handle LLM-refined predictions."""
        self._predictions = predictions
        self.predictionsRefined.emit(predictions)

    @Slot()
    def savePredictionModel(self) -> None:
        """Save the prediction model to disk."""
        self._predictor.save()

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

    @property
    def autoSaveOnExit(self) -> bool:
        """Whether to auto-save prediction model on exit."""
        return self._auto_save_on_exit

    # --- Privacy Mode ---

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

    # --- Accessibility Profile Management ---

    @Slot(result=list)
    def getAccessibilityProfiles(self) -> List[str]:
        """Get list of available accessibility profile names."""
        return self._predictor.get_accessibility_profiles()

    @Slot(result=str)
    def getCurrentProfile(self) -> str:
        """Get current accessibility profile name."""
        return self._predictor.get_current_profile()

    @Slot(str, result=bool)
    def setAccessibilityProfile(self, profile_name: str) -> bool:
        """
        Set accessibility profile for fuzzy recognition.

        Profiles: precise, normal, mild_tremor, moderate_tremor,
                  severe_tremor, limited_mobility
        """
        result = self._predictor.set_accessibility_profile(profile_name)
        if result:
            self._add_debug_log(f"Accessibility profile: {profile_name}")
        return result

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

    @Slot(str, str)
    def editPrediction(self, original: str, edited: str) -> None:
        """User edited a prediction (e.g. to fix capitalization). Insert it and learn."""
        edited = edited.strip()
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
        self._predictions = next_preds
        self.predictionsChanged.emit(next_preds)

        self._add_debug_log(f"Edited prediction: {original} → {edited}")
        _logger.info("Prediction edited: %s → %s", original, edited)

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

    def _get_current_profile(self) -> str:
        return self._predictor.get_current_profile()

    predictions = Property(list, _get_predictions, notify=predictionsChanged)
    llmEnabled = Property(bool, _get_llm_enabled, notify=llmEnabledChanged)
    llmAvailable = Property(bool, _get_llm_available, notify=llmAvailableChanged)
    predictionCount = Property(int, _get_prediction_count, notify=predictionCountChanged)
