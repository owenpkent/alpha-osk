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

import logging
from typing import List, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

from .platform import create_key_synthesizer
from .platform.base import KeySynthesizerBase
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

    # Debug signals
    debugModeChanged = Signal(bool)
    debugLogChanged = Signal(list)

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

        # Context tracking for predictions
        self._context_buffer = ""
        self._current_word = ""
        self._sentence_buffer = ""  # Accumulates words for sentence-level learning
        self._predictions: List[str] = []

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
    _NO_SPACE_BEFORE = {"?", "!", ".", ",", ";", ":", "'", '"', ")", "]", "}"}

    @Slot(str)
    def pressKey(self, key: str) -> None:
        """Called from QML when a character key is pressed."""
        if self._shift_active or self._caps_lock_active:
            char = key.upper()
        else:
            char = key.lower()

        # Handle punctuation spacing - remove preceding space
        if char in self._NO_SPACE_BEFORE and self._context_buffer.endswith(" "):
            # Delete the trailing space before typing punctuation
            self._send_key("BackSpace")
            self._context_buffer = self._context_buffer[:-1]
            _logger.info("Removed space before '%s'", char)

        # Use _send_key for modifier combos (Ctrl+C, Win+Shift+S, etc.)
        # Send the lowercase key — Shift is included as a modifier by _send_key
        if self._ctrl_active or self._alt_active or self._win_active:
            self._send_key(key.lower())
        else:
            self._send_text(char)

        # Update context and get predictions
        self._current_word += char

        # Sentence-ending punctuation triggers sentence learning
        if char in (".", "!", "?"):
            sentence = self._sentence_buffer + self._current_word
            if sentence.strip():
                self._predictor.learn(sentence.strip())
            self._sentence_buffer = ""
            self._current_word = ""
            self._context_buffer += char + " "
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
            self._ctrl_active = False
            self.ctrlActiveChanged.emit(self._ctrl_active)
        if self._alt_active:
            self._alt_active = False
            self.altActiveChanged.emit(self._alt_active)
        if self._win_active:
            self._win_active = False
            self.winActiveChanged.emit(self._win_active)

    @Slot(str)
    def pressSpecialKey(self, key_name: str) -> None:
        """Called from QML for special keys (Backspace, Return, etc.)."""
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

        # Handle context for predictions
        if key_name == "space":
            # Word completed - learn it and add to sentence
            if self._current_word:
                self._sentence_buffer += self._current_word + " "
                self._context_buffer += self._current_word + " "
                # Learn bigrams/trigrams from the running sentence
                self._predictor.learn(self._sentence_buffer.strip())
                # Keep context buffer bounded
                if len(self._context_buffer) > 200:
                    self._context_buffer = self._context_buffer[-200:]
            self._current_word = ""
            self._update_predictions()
        elif key_name == "backspace":
            # Remove last character from current word
            if self._current_word:
                self._current_word = self._current_word[:-1]
                self._update_predictions()
        elif key_name == "return":
            # Sentence boundary - learn full sentence, then reset sentence buffer
            if self._current_word:
                self._sentence_buffer += self._current_word
            if self._sentence_buffer.strip():
                self._predictor.learn(self._sentence_buffer.strip())
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
            self._ctrl_active = False
            self.ctrlActiveChanged.emit(self._ctrl_active)
        if self._alt_active:
            self._alt_active = False
            self.altActiveChanged.emit(self._alt_active)
        if self._win_active:
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
        """Toggle ctrl modifier (sticky)."""
        self._ctrl_active = not self._ctrl_active
        self.ctrlActiveChanged.emit(self._ctrl_active)

    @Slot()
    def toggleAlt(self) -> None:
        """Toggle alt modifier (sticky)."""
        self._alt_active = not self._alt_active
        self.altActiveChanged.emit(self._alt_active)

    @Slot()
    def toggleWin(self) -> None:
        """Toggle Windows/Super modifier (sticky)."""
        self._win_active = not self._win_active
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

        # Complete the word: type remaining characters + space
        if self._current_word:
            # Type only the remaining part of the word
            if word.startswith(self._current_word.lower()):
                remaining = word[len(self._current_word):]
            else:
                remaining = word
            self._send_text(remaining + " ")
        else:
            self._send_text(word + " ")

        # Learn from selection
        context = self._context_buffer + self._current_word
        self._predictor.learn_from_selection(context, word)

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
        """Clear current predictions (called from QML on dismiss/deactivation)."""
        self._predictions = []
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
        """Clear user-learned vocabulary."""
        self._predictor.clear_user_data()
        _logger.info("User data cleared")

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
        """Add a message to the debug log."""
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
