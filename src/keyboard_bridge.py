"""
Keyboard Bridge - Python backend exposed to QML.

Handles key synthesis (sending keystrokes to the focused application)
using the platform abstraction layer:

- **Linux**: xdotool (X11) or ydotool (Wayland) via subprocess.
- **Windows**: Win32 SendInput API via ctypes, with optional UIAccess
  for elevated-window support (requires EV code-signed binary).

The bridge is platform-agnostic — all OS-specific logic lives in
``src/platform/``.  See ``docs/architecture/PLATFORM_ARCHITECTURE.md``.
"""

from __future__ import annotations

import json
import logging
import time
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
from .platform import CURRENT_PLATFORM, create_key_synthesizer
from .platform.base import KeySynthesizerBase
from .platform.password_detect import is_password_field
from .prediction import HybridPredictor, SwipeRecognizer
from .snippets import SnippetStore
from .telemetry import TelemetryClient
from .updater import UpdateInfo, check_for_update, download_and_install

# How long to keep the "installing v… keyboard back in a moment" toast
# on screen before letting the install proceed (and the installer's
# taskkill arrive). Long enough to read, short enough not to feel like
# the click did nothing.
_PRE_INSTALL_TOAST_DWELL_S = 1.8

# Window classes / process exes used to auto-detect a foreground app
# whose keystroke handling breaks the suffix-only insertion path.
# Two categories live in the same set because the compat lever is the
# same — switch prediction insertion to BackSpace+retype:
#   1. Remote-desktop clients (TeamViewer, RDP, VNC, AnyDesk, ...) —
#      the user is typing THROUGH them and the remote-forwarding
#      pipeline drops/duplicates/reorders keystrokes.  Class match.
#   2. IDEs with always-on keystroke interception (VS Code + Monaco
#      forks, JetBrains family) — the user is typing INTO them and
#      IntelliSense/snippet expansion/multi-caret reorders or eats
#      keystrokes.  Process-name match (Electron and JetBrains both
#      use shared window classes — Chrome_WidgetWin_1, SunAwtFrame —
#      that overlap with too many unrelated apps).
# Conservative whitelist throughout — a false positive costs the user
# the chat-composer-friendly suffix-only path; a false negative just
# means the manual toggle is still available.  Chrome Remote Desktop's
# host-viewer window class isn't here because it'd need to be
# differentiated from regular Chrome browser windows.
_COMPAT_WINDOW_CLASSES = frozenset({
    # Microsoft Remote Desktop Connection
    "TscShellContainerClass",
    "RDPViewer",
    "UIMainClass",
    # TeamViewer
    "TV_TitleBar",
    "TV_Client",
    "TV_FullScreen",
    "#32770TVMainForm",
    # AnyDesk
    "AnyDeskMainWindow",
    "AnyDeskMainView",
    # VNC variants
    "TightVNCClassName",
    "VNCMDI_Window",
    "VNCviewer",
    "RealVNCClass",
    "UltraVNCClass",
    "TVNVncCtrl",
    # RustDesk
    "RustDesk",
    # Parsec
    "ParsecHostWindow",
    # Splashtop
    "SplashtopRemoteDesktopClass",
})

_COMPAT_PROCESS_NAMES = frozenset({
    "teamviewer.exe",
    "tv_w32.exe",
    "tv_x64.exe",
    "mstsc.exe",
    "msrdc.exe",
    "anydesk.exe",
    "vncviewer.exe",
    "tvnviewer.exe",
    "uvnc.exe",
    "winvnc.exe",
    "rustdesk.exe",
    "splashtop.exe",
    "stp.exe",
    "logmein.exe",
    "parsecd.exe",
    "moonlight.exe",
    # IDEs that intercept keystrokes for autocomplete / snippets /
    # multi-caret in ways that break the suffix-only insertion path's
    # "the typed prefix is on screen, just append the rest"
    # assumption — same compat lever as remote-desktop tools.
    # Match on exe basename: window classes (Chrome_WidgetWin_1 for
    # Electron, SunAwtFrame for JetBrains) are shared with too many
    # unrelated apps to be safe.
    #
    # VS Code + Monaco-engine forks (Cursor, Windsurf, Codium, etc.):
    "code.exe",
    "code - insiders.exe",
    "cursor.exe",
    "windsurf.exe",
    "codium.exe",
    "code-oss.exe",
    "positron.exe",
    "trae.exe",
    # JetBrains IntelliJ Platform IDEs.  64-bit launchers only —
    # JetBrains dropped 32-bit `*.exe` launchers in 2019.  Android
    # Studio also ships `studio.exe` as a wrapper, included for
    # safety.
    "idea64.exe",
    "pycharm64.exe",
    "webstorm64.exe",
    "phpstorm64.exe",
    "clion64.exe",
    "goland64.exe",
    "rider64.exe",
    "rubymine64.exe",
    "datagrip64.exe",
    "dataspell64.exe",
    "studio64.exe",
    "studio.exe",
})


def _window_needs_compat_mode(hwnd: int) -> bool:
    """Whether ``hwnd`` is a foreground window that needs compat mode.

    Covers two categories: remote-desktop clients (TeamViewer, RDP,
    VNC, ...) and IDEs whose editors intercept keystrokes (VS Code +
    Monaco forks, JetBrains family).  Both break the suffix-only
    insertion path; the compat lever — switch to BackSpace+retype —
    is identical for both.

    Returns False on non-Windows or when detection fails.  Conservative:
    a False return is safe (compat mode just stays off), so any error
    in the platform calls bails silently rather than throwing.

    Detection is two-pass:
    1. Window class name (``GetClassNameW``) against
       ``_COMPAT_WINDOW_CLASSES`` — used for remote-desktop clients
       which expose distinctive class names.
    2. On miss, the owning process's exe basename
       (``QueryFullProcessImageNameW``) against
       ``_COMPAT_PROCESS_NAMES`` — used for IDEs (Electron's
       ``Chrome_WidgetWin_1`` and JetBrains' ``SunAwtFrame`` are too
       broad to match by class) and as a safety net for remote tools.
    Both checks are exact-match against curated whitelists so unrelated
    apps cannot spuriously trigger compat mode.
    """
    import sys
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32          # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32      # type: ignore[attr-defined]
        cls_buf = ctypes.create_unicode_buffer(256)
        if user32.GetClassNameW(hwnd, cls_buf, 256) > 0:
            if cls_buf.value in _COMPAT_WINDOW_CLASSES:
                return True
        # Process-exe fallback.
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return False
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value,
        )
        if not handle:
            return False
        try:
            exe_buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if kernel32.QueryFullProcessImageNameW(
                handle, 0, exe_buf, ctypes.byref(size),
            ):
                exe_name = Path(exe_buf.value).name.lower()
                if exe_name in _COMPAT_PROCESS_NAMES:
                    return True
        finally:
            kernel32.CloseHandle(handle)
    except (OSError, AttributeError):
        # ctypes/Win32 errors: fail-safe.
        pass
    return False


# Cursor-movement keys. When a sticky modifier is held, pressing one of
# these should KEEP Shift/Ctrl held (extend selection / word-jump across
# multiple presses) instead of auto-releasing after the first press. See
# the auto-release block in pressSpecialKey for the full rationale.
_NAV_KEYS = frozenset({
    "left", "right", "up", "down", "home", "end", "pageup", "pagedown",
})

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

    # Live-context signal for the language-model visualization. Fires on
    # every keystroke that changes the typing context with
    # ``(prev_word, current_partial)`` — the visualization uses it to
    # pulse the active node and the active edge in the flow graph as
    # the user types in the foreground app. Cheap by design: emits raw
    # tokens, no formatting, and consumers throttle their own repaints.
    activeContextChanged = Signal(str, str)

    # Auto-update signals — version, asset_name, notes (release-notes
    # markdown, already sanitised by the updater).  ``updateUnavailable``
    # fires after a manual "Check now" that found nothing — it lets the
    # UI distinguish "no newer version" from "still checking".
    updateAvailable = Signal(str, str, str)
    updateUnavailable = Signal()
    updateInstallStarted = Signal()
    updateInstallFailed = Signal(str)
    # Fires immediately before the installer process is launched (after
    # download + signature verify succeed). The QML side flashes a toast
    # warning the user that the keyboard is about to disappear for
    # ~30 s while the install runs and the relauncher brings it back.
    # Without this, the silence between taskkill and relaunch reads as
    # "the update broke the keyboard" — see docs/build/AUTO_UPDATE.md § Update
    # progress indicator.
    updateInstallHandoffPending = Signal(str)
    # Streaming download progress for the update installer. Bytes
    # received + total bytes (or -1 when the server omits Content-Length).
    # Emitted from the install worker thread; QML auto-connects via the
    # default queued-connection so the bar repaints on the UI thread.
    # Cadence is throttled at the emit site so a 64 KB chunk size doesn't
    # spam the signal bus on a fast download.
    updateDownloadProgress = Signal(int, int)

    # Edit-mode signals — when the prediction-edit popup is open, OSK
    # keystrokes must target its TextField, not the OS-focused app
    # behind us (we can't steal OS focus without breaking the rest of
    # the keyboard). QML calls setEditMode(True) when the popup opens,
    # we short-circuit pressKey/pressSpecialKey to emit these signals
    # instead, and QML mutates the TextField directly.
    editKeyTyped = Signal(str)          # char to insert at cursor
    editSpecialPressed = Signal(str)    # special key name (backspace, left, etc.)

    # Emitted after the snippet list changes (add / edit / delete /
    # move) so the Snippets popup re-queries getSnippets() and rebuilds
    # its rows.
    snippetsChanged = Signal(list)

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

        # Snippets — user-defined quick-insert text (name, email, phone,
        # address, canned phrases).  Tapped from the title-bar Snippets
        # popup and inserted verbatim via _send_text.  The store seeds
        # four empty labelled slots on first launch and saves on every
        # mutation, so there is no on-quit save path to wire up.
        self._snippets = SnippetStore()
        self._snippets.load()

        # Telemetry — opt-in, off by default.  Pulls lifetime counters
        # from the analytics dashboard's getter so there is one source
        # of truth.  Settings → Data & Privacy → Privacy controls it; the QTimer below
        # checks once an hour whether the weekly window has elapsed.
        # See docs/architecture/TELEMETRY.md (design) and docs/PRIVACY.md (user-facing).
        self._telemetry = TelemetryClient(
            analytics_provider=self._analytics.get_session_stats,
            app_version=APP_VERSION,
            os_name=CURRENT_PLATFORM,
        )
        self._telemetry_timer = QTimer(self)
        # Hourly tick is plenty: maybe_submit() short-circuits unless
        # the 7-day window has elapsed, and we want the timer to be
        # cheap (a no-op call costs ~5 microseconds).
        self._telemetry_timer.setInterval(60 * 60 * 1000)
        self._telemetry_timer.timeout.connect(self._telemetry.maybe_submit)
        self._telemetry_timer.start()

        # Context tracking for predictions
        self._context_buffer = ""
        self._current_word = ""
        # True iff Caps Lock was active for at least one character in the
        # currently-being-typed word.  Distinguishes "user shouted via
        # caps lock" from "user deliberately right-clicked / shifted each
        # letter to type all-caps" — the latter is a strong signal that
        # the word is canonically uppercase ("HVAC", "ROFL") and should
        # be learned, the former is incidental and would pollute the
        # capitalisation table.  Reset on every word boundary.
        self._word_typed_under_caps_lock = False
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
        # Space-time autocorrect — replace the typed word with a
        # known correction when space lands.  Off by default: the
        # user can pick a corrected pill from the suggestion bar
        # if they want it, but a silent on-space replacement
        # clobbered deliberate input ("vs" → "is", and a hyphenated
        # word followed by another word reportedly wiped both).
        # The fuzzy recogniser still contributes to the suggestion
        # pills (that's the "autocorrect in the suggestion box"
        # the user wants); only the space-triggered overwrite path
        # is disabled.  ``setAutocorrectEnabled`` flips this back
        # on if a future caller wants it.
        self._autocorrect_enabled = False

        # Compatibility mode — covers two categories of foreground
        # apps where the local OSK's suffix-only prediction insertion
        # (and Shift+Left-based autocorrect replace) is unsafe:
        #   1. Remote-desktop clients (TeamViewer, RDP, VNC, AnyDesk)
        #      where the remote-forwarding pipeline drops, duplicates,
        #      or reorders keystrokes before the remote app sees them.
        #   2. IDEs with always-on keystroke interception (VS Code +
        #      Monaco forks, JetBrains family) where IntelliSense /
        #      snippet expansion / multi-caret eats or reorders
        #      keystrokes inside the editor.
        # In both cases the typed prefix the OSK *thinks* is on screen
        # doesn't match what's *actually* on screen, so suffix-only
        # produces "helhello"-style duplicates.  Compat mode rewires
        # both paths into a sequence of independent, single-event
        # keystrokes — BackSpace × len(typed) + type the full word —
        # which is robust to per-event drops/duplicates (worst case is
        # a one-char gap, not a scrambled word).
        #
        # Three flags compose into the effective state:
        # - ``_compat_manual`` — user's explicit Settings toggle
        #   ("Compatibility Mode").  Force-on, never auto-cleared.
        # - ``_compat_auto_enabled`` — whether to auto-detect based on
        #   the foreground window class / process exe.
        # - ``_compat_auto_active`` — whether the current foreground
        #   window is a known remote-desktop client or
        #   keystroke-intercepting IDE.  Updated by
        #   ``_check_foreground_window`` on every poll.
        # Effective: ``_in_compat_mode()`` returns
        # ``manual or (auto_enabled and auto_active)``.
        self._compat_manual = False
        self._compat_auto_enabled = True
        self._compat_auto_active = False

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
            # Track whether Caps Lock was on for any char in this word
            # — gates whether all-caps typing is allowed to be learned
            # (see `_word_typed_under_caps_lock` in __init__).
            if self._caps_lock_active:
                self._word_typed_under_caps_lock = True

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
                self._word_typed_under_caps_lock = False
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
                self._word_typed_under_caps_lock = False
                if self._auto_space_after_punctuation:
                    self._send_text(" ")
                    self._auto_space_pending = True
                if len(self._context_buffer) > 200:
                    self._context_buffer = self._context_buffer[-200:]

            # Word-internal boundaries that DON'T get an auto-space:
            # hyphen / slash / opening bracket / markdown-and-sigil
            # punctuation.  Without this, typing "word1-word2" left
            # _current_word = "word1-word2", so clicking a suggestion
            # for "word2" failed the prefix-match in pressPrediction
            # and fell through to replace_text, which backspaced
            # "word1-" off the screen too.  Same bug for "*hello",
            # "@user", "#tag", "$var", `key=value` etc. — the leading
            # punctuation got selected and overwritten by the pill.
            # Treat each as a word boundary for prediction purposes:
            # keep the character on screen (already sent above), reset
            # _current_word, and append the segment-plus-separator to
            # the buffers WITHOUT a trailing space (the user types
            # these with no following space, unlike commas).  Excluded
            # deliberately: apostrophe (contractions like "don't" are
            # single tokens) and underscore (snake_case identifiers).
            elif char in (
                "-", "/", "\\", "(", "[", "{", "<",
                "*", "@", "#", "$", "%", "&", "+", "=",
                "~", "^", "|", '"', "`",
            ):
                word_before = self._current_word[:-1]
                if word_before:
                    self._sentence_buffer += word_before + char
                    self._context_buffer += word_before + char
                else:
                    self._context_buffer += char
                self._current_word = ""
                self._word_typed_under_caps_lock = False
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
                if self._in_compat_mode():
                    # Compat mode: Shift+Left selection is unsafe under
                    # remote-forwarding pipelines and IDE interception.
                    # Use BackSpace × N + type instead — same end result,
                    # robust to per-event drops/duplicates.
                    for _ in range(len(self._current_word)):
                        self._synth.send_key("BackSpace")
                    self._send_text(cased + " ")
                else:
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
                # Learn capitalization from user typing.  All-caps is
                # only allowed if Caps Lock was off the whole word —
                # otherwise we'd pollute the table with shouty forms of
                # every word typed under caps lock.  Off-the-whole-word
                # means the user deliberately right-clicked / shifted
                # each letter to type all-caps, which is a strong
                # signal ("HVAC", "ROFL").
                allow_uppercase = not self._word_typed_under_caps_lock
                if self._predictor.learn_capitalization(
                    self._current_word, allow_uppercase=allow_uppercase
                ):
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
            self._word_typed_under_caps_lock = False
            self._update_predictions()
        elif key_name == "backspace":
            self._analytics.record_backspace()
            if self._current_word:
                self._current_word = self._current_word[:-1]
                if not self._current_word:
                    self._word_typed_under_caps_lock = False
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
            self._word_typed_under_caps_lock = False
            self._update_predictions()

        # Auto-release shift/ctrl/alt/win after special key too. Without
        # this, Shift+Tab (or any sticky-Shift + special key combo) left
        # _shift_active=True and the OS-held Shift from hold_modifier in
        # place, so every following click was also under Shift until the
        # user tapped Shift again. The character-key path in _press_char
        # already auto-releases all four; special keys must match so the
        # chord behaviour mirrors the Windows on-screen keyboard.
        #
        # Exception: cursor-movement keys. When a modifier is held, the
        # user is almost always building a compound action across several
        # presses — Shift+arrow to extend a selection, Ctrl+arrow to jump
        # by word, Ctrl+Shift+arrow to select by word. Auto-releasing
        # Shift/Ctrl after the first arrow press breaks that: the second
        # press lands without the modifier, and an auto-repeating held
        # arrow drops the modifier after its very first tick (the reported
        # "holding shift + arrow stops holding shift"). So for navigation
        # keys we keep Shift and Ctrl held; the user taps the modifier
        # again to release it when done, same as Shift+click/Shift+drag
        # selection extension. Alt/Win combos (Alt+Left = back,
        # Win+arrow = snap) are one-shot, so those still auto-release.
        keep_selection_modifiers = key_name in _NAV_KEYS
        if (
            self._shift_active
            and not self._caps_lock_active
            and not keep_selection_modifiers
        ):
            self._shift_active = False
            self._synth.release_modifier("shift")
            self._update_layer()
            self.shiftActiveChanged.emit(self._shift_active)
        if self._ctrl_active and not keep_selection_modifiers:
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
        #
        # Compat mode (remote-desktop clients + keystroke-intercepting
        # IDEs — see _COMPAT_PROCESS_NAMES) bypasses both the suffix-
        # only and Shift+Left-replace paths.  Suffix-only depends on
        # the OSK's _current_word matching what's actually rendered on
        # screen — and remote forwarding / IDE interception can drop,
        # duplicate, or reorder events, so that assumption breaks.
        # Shift+Left has the same race shape.  Compat mode rewires
        # everything to BackSpace × N + type the full word, a sequence
        # of independent single-event keystrokes that is robust to
        # per-event drops / duplicates.
        if self._in_compat_mode() and self._current_word:
            for _ in range(len(self._current_word)):
                self._synth.send_key("BackSpace")
            self._send_text(word + " ")
        elif word.startswith(self._current_word) and self._current_word:
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

        # Capture casing intent.  If the user typed *any* uppercase
        # letter in the prefix (right-click → shifted variant, or manual
        # shift), that's a deliberate signal "this word is capitalized."
        # Triggering on `prefix != prefix.lower()` covers both first-
        # letter caps ("Hello") and mid-word caps ("eBay", "macBook",
        # "iPhone"), which was the gap in the original first-letter-only
        # check — a right-click on the 'B' in "macBook" left the casing
        # intent unlearned because `_current_word[0]` was lowercase 'm'.
        # learn_from_selection only updates frequency / bigrams, so
        # without this call the casing was being thrown away — the user
        # would have to re-right-click / re-shift every time they typed
        # the same word.  learn_capitalization has its own guards
        # (rejects single-char inputs), so the call is safe on any
        # non-lowercase prefix.  All-caps is allowed only if the user
        # didn't have Caps Lock on for any char of the prefix (i.e.
        # they deliberately right-clicked / shifted each letter) —
        # see `_word_typed_under_caps_lock`.
        if self._current_word and self._current_word != self._current_word.lower():
            allow_uppercase = not self._word_typed_under_caps_lock
            self._predictor.learn_capitalization(word, allow_uppercase=allow_uppercase)

        # Update context - add the completed word
        self._context_buffer += word + " "
        if len(self._context_buffer) > 100:
            self._context_buffer = self._context_buffer[-100:]
        self._current_word = ""
        self._word_typed_under_caps_lock = False

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
        self._word_typed_under_caps_lock = False
        self._sentence_buffer = ""
        self._context_buffer = ""
        self.predictionsChanged.emit([])

    # ------------------------------------------------------------------
    #  Snippets — user-defined quick-insert text (see src/snippets.py)
    # ------------------------------------------------------------------

    @Slot(result="QVariantList")
    def getSnippets(self) -> List[Dict[str, str]]:
        """Return the snippet list as ``[{label, value}, ...]`` for QML."""
        return self._snippets.get_all()

    @Slot(int)
    def insertSnippet(self, index: int) -> None:
        """Type the snippet at *index* verbatim into the focused app.

        Snippets are full literal inserts (no prefix matching, no
        autocorrect), so they go straight through ``_send_text`` and
        work the same in every app — compat mode's BackSpace+retype
        dance exists to replace a *typed prefix* and isn't relevant to a
        fresh insert.  Privacy mode does NOT block insertion: privacy is
        about not *learning* from typing, and the user may well need to
        drop their address into a sensitive form.

        After inserting, the typing context is cleared so the verbatim
        text (which may carry punctuation or newlines) can't corrupt the
        next prediction's prefix matching.
        """
        if self._edit_mode_active:
            return
        value = self._snippets.get_value(index)
        if not value:
            return
        self._send_text(value)
        self._current_word = ""
        self._word_typed_under_caps_lock = False
        self._auto_space_pending = False
        self._predictions = []
        self.predictionsChanged.emit([])

    @Slot(int, str, str)
    def setSnippet(self, index: int, label: str, value: str) -> None:
        """Replace the label + value of the snippet at *index*."""
        if self._snippets.set(index, label, value):
            self.snippetsChanged.emit(self._snippets.get_all())

    @Slot()
    def addSnippet(self) -> None:
        """Append a new blank snippet (for the user to fill in) and notify QML."""
        if self._snippets.add("New", ""):
            self.snippetsChanged.emit(self._snippets.get_all())

    @Slot(int)
    def deleteSnippet(self, index: int) -> None:
        """Remove the snippet at *index* and notify QML."""
        if self._snippets.delete(index):
            self.snippetsChanged.emit(self._snippets.get_all())

    @Slot(int, int)
    def moveSnippet(self, index: int, direction: int) -> None:
        """Move the snippet at *index* up (-1) or down (+1) one position."""
        if self._snippets.move(index, direction):
            self.snippetsChanged.emit(self._snippets.get_all())

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
        # Tell the language-model visualization what the active edge is
        # so it can pulse the node + edge live as the user types.
        # Privacy mode suppresses the emit — the viz must not leak
        # password chars or password-field context.
        if not self._privacy_mode:
            ctx_tokens = self._context_buffer.split()
            prev_word = ctx_tokens[-1].lower() if ctx_tokens else ""
            self.activeContextChanged.emit(prev_word, self._current_word.lower())

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

        Also retracts one sighting of the rehydrated word from the
        n-gram predictor (backspace-as-negative-signal). The word's
        ``learn()`` call fired when the user pressed space; if they're
        now editing it, the most likely reason is a typo. The retract
        only touches user-side tables (candidate_counts → user_vocab),
        so a word the user has typed many times can't be unlearned by a
        single backspace; if they re-complete the word with the same
        spelling, ``learn()`` will count it again on the next space.
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
        if self._current_word and not self._privacy_mode:
            self._predictor.unlearn_word(self._current_word)

    def _display_cased(self, predictions: List[str]) -> List[str]:
        """Transform predictions to match the user's active case mode.

        Two cases:

        1. Caps Lock on — every character the user types is being sent
           uppercase, and `_current_word` accumulates uppercase too.
           Pills must match: showing "hello" while the user typed
           "HELL" misleads about which pill matches the prefix, and
           clicking sends the lowercase form next to an uppercase
           prefix.
        2. Any uppercase in the prefix.  The user typed e.g. "Hel",
           "HEL" (right-clicked each letter), "HEl", or "iP" (mid-word
           cap via right-click). Mirror each typed uppercase position
           onto the corresponding pill position so the displayed pill
           reflects exactly what the user typed. The mirror runs
           regardless of whether the pill strict-prefix-matches the
           typed letters: prefix-match completions ("Hel" -> "Hello")
           and fuzzy corrections that *don't* strict-match ("Hwl" ->
           "hello", "Heilo" -> "hello") both need the capital reflected.
           Without the unconditional mirror the fuzzy-corrected pills
           kept showing lowercase. The strict-prefix path also matters
           for the suffix-only insert: "hello".startswith("HEL") is
           False without mirroring, so the click would fall through to
           a full replace and clobber the user's capitals.

        Sentence-start and proper-noun capitalisation are handled
        upstream by :func:`NgramPredictor.get_capitalized`; this layer
        only mirrors the *typed* prefix back into the displayed form.
        """
        if not predictions:
            return predictions
        if self._caps_lock_active:
            return [w.upper() for w in predictions]
        cw = self._current_word
        if cw and any(c.isupper() for c in cw):
            result: List[str] = []
            for w in predictions:
                if not w:
                    result.append(w)
                    continue
                new_chars = []
                for i, ch in enumerate(w):
                    if i < len(cw) and cw[i].isupper():
                        new_chars.append(ch.upper())
                    else:
                        new_chars.append(ch)
                result.append("".join(new_chars))
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
    #  Export / import (data backup — see src/data_export.py)
    # ------------------------------------------------------------------

    @Slot(result=str)
    def getDefaultExportDir(self) -> str:
        """Return the default directory for the export / import file picker.

        Defaults to ``<config_dir>/exports/`` — the same folder the
        rescue archives already live in, and a sibling of the model
        files being exported. Using the config dir avoids an
        elevation pitfall on Windows: ``run.py`` UAC-elevates the
        process, so ``QStandardPaths.DocumentsLocation`` would resolve
        to the *elevated* user's profile (often an admin account)
        rather than the interactive user's. The config dir always
        tracks the running user's actual data location, so the export
        lands next to the data it's exporting.
        """
        from .platform import get_config_dir
        exports = get_config_dir() / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        return str(exports)

    @Slot(result=str)
    def getSuggestedExportName(self) -> str:
        """Default filename including a timestamp."""
        from . import data_export
        return data_export.suggested_export_name()

    @Slot(result=str)
    def pickExportPath(self) -> str:
        """Open a native Save-File dialog pre-populated with a sensible
        default directory + filename. Returns the chosen path or an
        empty string if the user cancelled.

        Goes through Python's :class:`QFileDialog` rather than the QML
        ``Platform.FileDialog`` because the labs dialog has no portable
        initial-filename property across Qt versions (``currentFile``
        is honoured on some platforms, ignored on others). Routing
        through Python lets us pass a full initial path including the
        suggested timestamped filename so the user just clicks Save.
        """
        try:
            from PySide6.QtWidgets import QFileDialog

            from . import data_export
            default_dir = self.getDefaultExportDir()
            suggested = data_export.suggested_export_name()
            initial = str(Path(default_dir) / suggested)
            path, _ = QFileDialog.getSaveFileName(
                None,
                "Save Alpha-OSK data export",
                initial,
                "Alpha-OSK export (*.zip)",
            )
            return path or ""
        except Exception as exc:  # pragma: no cover — defensive
            _logger.exception("pickExportPath failed: %s", exc)
            return ""

    @Slot(result=str)
    def pickImportPath(self) -> str:
        """Open a native Open-File dialog rooted at the default export
        directory. Returns the chosen path or empty string on cancel."""
        try:
            from PySide6.QtWidgets import QFileDialog
            default_dir = self.getDefaultExportDir()
            path, _ = QFileDialog.getOpenFileName(
                None,
                "Open Alpha-OSK data export",
                default_dir,
                "Alpha-OSK export (*.zip);;All files (*)",
            )
            return path or ""
        except Exception as exc:  # pragma: no cover — defensive
            _logger.exception("pickImportPath failed: %s", exc)
            return ""

    @Slot(str, result=str)
    def exportUserData(self, dest_path: str) -> str:
        """Write the current model + analytics + packs to *dest_path*.

        Returns an empty string on success, or a human-readable error
        message on failure. The QML side shows the message verbatim
        in the result toast.

        Saves the in-memory model to disk first so the export
        reflects the running session and not a stale on-disk copy.
        """
        from . import data_export
        try:
            self._predictor.save()
            try:
                self._analytics.save()
            except Exception as exc:  # pragma: no cover — analytics is best-effort
                _logger.warning("Analytics save before export failed: %s", exc)
            from .platform import get_config_dir
            summary = data_export.export_user_data(get_config_dir(), Path(dest_path))
            self._add_debug_log(
                f"Exported {len(summary.files)} files ({len(summary.pack_ids)} packs) "
                f"to {summary.path}"
            )
            return ""
        except data_export.DataExportError as exc:
            self._add_debug_log(f"Export failed: {exc}")
            return str(exc)
        except Exception as exc:  # pragma: no cover — last-resort
            _logger.exception("Unexpected error during export")
            return f"Unexpected error: {exc}"

    @Slot(str, result="QVariant")
    def inspectUserExport(self, src_path: str) -> dict:
        """Preview an export file without applying it.

        Returns a dict with ``ok`` (bool), and on success ``files`` (list),
        ``pack_ids`` (list), ``app_version`` (str), ``exported_at`` (str),
        ``bytes`` (int), ``schema_version`` (int). On failure returns
        ``{ok: False, error: <message>}``.

        QML uses this to show a "you're about to replace your data
        with X" confirmation summary before the user commits.
        """
        from . import data_export
        try:
            summary = data_export.inspect_export(Path(src_path))
            return {
                "ok": True,
                "files": summary.files,
                "pack_ids": summary.pack_ids,
                "app_version": summary.app_version,
                "exported_at": summary.exported_at,
                "bytes": summary.bytes,
                "schema_version": summary.schema_version,
            }
        except data_export.DataExportError as exc:
            return {"ok": False, "error": str(exc)}

    @Slot(str, result=str)
    def importUserData(self, src_path: str) -> str:
        """Replace the current user data with the contents of *src_path*.

        A rescue export of the current state is written to
        ``<config_dir>/exports/`` first (see :func:`import_user_data`).
        The predictor is reloaded from disk after files are replaced
        so the live session reflects the imported state — no restart
        required.

        Returns empty string on success, error message on failure.
        """
        from . import data_export
        try:
            from .platform import get_config_dir
            data_export.import_user_data(Path(src_path), get_config_dir())
            self._predictor.reload_from_disk()
            try:
                self._analytics.reload_from_disk()
            except AttributeError:
                # Older analytics module — fall back to a process-level
                # reload by reading the file directly. Live numbers
                # will lag until the next save/load cycle on next
                # launch. Don't fail the whole import.
                _logger.warning(
                    "TypingAnalytics has no reload_from_disk(); lifetime stats"
                    " will display stale values until next launch."
                )
            except Exception as exc:  # pragma: no cover
                _logger.warning("Analytics reload after import failed: %s", exc)
            try:
                self._snippets.reload_from_disk()
                self.snippetsChanged.emit(self._snippets.get_all())
            except Exception as exc:  # pragma: no cover — defensive
                _logger.warning("Snippet reload after import failed: %s", exc)
            self._current_word = ""
            self._context_buffer = ""
            self._sentence_buffer = ""
            self._predictions = []
            self.predictionsChanged.emit([])
            self._add_debug_log(f"Imported user data from {src_path}")
            return ""
        except data_export.DataExportError as exc:
            self._add_debug_log(f"Import failed: {exc}")
            return str(exc)
        except Exception as exc:  # pragma: no cover — last-resort
            _logger.exception("Unexpected error during import")
            return f"Unexpected error: {exc}"

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

            def _on_installer_launching(version: str) -> None:
                # Fired from the worker thread immediately before the
                # installer is spawned. Emit the toast signal and then
                # block briefly so the toast has time to paint and be
                # legible before the installer's taskkill arrives. The
                # sleep is in the worker thread, so the UI stays
                # responsive — the user sees a toast appear, then the
                # keyboard disappears a moment later, instead of the
                # keyboard vanishing without warning.
                self.updateInstallHandoffPending.emit(version)
                time.sleep(_PRE_INSTALL_TOAST_DWELL_S)

            # Throttle the download-progress emits. The downloader's
            # 64 KB chunk size means an 85 MB installer would fire ~1300
            # signals; coalescing to ~once-per-256 KB keeps the bar
            # smooth without flooding the queued-signal connection.
            # Also always emit the final chunk so the bar lands at 100 %.
            last_emit = [0]
            EMIT_EVERY = 256 * 1024

            def _on_progress(written: int, total: Optional[int]) -> None:
                if (
                    written - last_emit[0] >= EMIT_EVERY
                    or (total is not None and written >= total)
                ):
                    last_emit[0] = written
                    self.updateDownloadProgress.emit(
                        written, total if total is not None else -1,
                    )

            try:
                ok, err = download_and_install(
                    info,
                    progress=_on_progress,
                    on_installer_launching=_on_installer_launching,
                )
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

    @Slot(result="QVariant")
    def consumeUpdateHandoff(self) -> Dict[str, Any]:
        """Return the post-update toast payload, if one is pending.

        After the auto-update relauncher launches a freshly-installed
        Alpha-OSK, it writes an ``update_handoff.json`` breadcrumb to
        ``$APPDATA/alpha-osk/`` so this brand-new instance knows to
        confirm the update visually. QML calls this slot in
        ``Component.onCompleted`` and flashes a toast if the return
        value is non-empty.

        The file is deleted on read (single-use breadcrumb). Stale or
        unreadable files are treated as no handoff. Anything older than
        five minutes is also ignored — the user already either knows
        the update happened (it ran moments ago) or has been using the
        new build for a while and doesn't need the toast.

        Returns ``{"version": str, "previousVersion": str}`` on a fresh
        handoff, otherwise ``{}``.
        """
        try:
            from src.platform import get_config_dir
        except ImportError:
            from .platform import get_config_dir  # type: ignore
        path = get_config_dir() / "update_handoff.json"
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._safe_unlink(path)
            return {}

        completed_at = float(data.get("completed_at", 0) or 0)
        # Five-minute freshness window. Anything older means the OSK
        # has been launched at least once since the update — no need
        # to surface the toast again.
        if completed_at > 0 and (time.time() - completed_at) > 300:
            self._safe_unlink(path)
            return {}

        self._safe_unlink(path)
        return {
            "version": str(data.get("version", "")),
            "previousVersion": str(data.get("previous_version", "")),
        }

    @staticmethod
    def _safe_unlink(path: "Path") -> None:
        """Best-effort delete of the handoff breadcrumb."""
        try:
            path.unlink()
        except OSError:
            # File may already be gone or held open by AV; the breadcrumb
            # is non-critical, so swallow rather than surface to the user.
            pass

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
            getattr(self, "_telemetry_timer", None),
        ):
            if timer is not None:
                try:
                    timer.stop()
                except RuntimeError:
                    pass  # already deleted by Qt; harmless

        # Last-chance telemetry submit on the on-quit path.  Internally
        # gated on consent + endpoint + anon_id + a 60 s anti-spam
        # window, so calling it unconditionally here is safe.
        try:
            self._telemetry.submit_on_quit()
        except Exception as e:
            _logger.info("telemetry on-quit submit failed: %s", e)

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
            # Shutdown path: COM teardown failures must not crash the
            # exit handler. The OS will reap the apartment regardless.
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

    @Slot(str)
    def setMergeStrategy(self, strategy: str) -> None:
        """Pick the prediction merge strategy.

        One of ``"rank"`` (default), ``"rrf"``, ``"linear"``,
        ``"loglinear"``.  Unknown values are ignored — see
        :meth:`HybridPredictor.set_merge_strategy`.  The setting is
        persisted in QML ``Settings`` as ``savedMergeStrategy`` and
        reapplied on every launch via the QML
        ``Component.onCompleted`` block.
        """
        self._predictor.set_merge_strategy(strategy)

    @Slot(bool)
    def setCompatMode(self, enabled: bool) -> None:
        """Toggle the *manual* compatibility-mode flag.

        When enabled, prediction-click insertion and autocorrect-on-
        space stop using suffix-only / Shift+Left-replace tricks and
        instead emit BackSpace × N + the full word.  Robust to the
        keystroke drops / duplications / reordering that happen in
        remote-desktop sessions (TeamViewer, RDP, VNC, AnyDesk) and to
        the keystroke interception inside IDEs (VS Code + Monaco
        forks, JetBrains family).

        Combined with the auto-detect flag (see
        ``setCompatAutoDetect``) — the effective state is
        ``manual OR (auto_enabled AND auto_active)``.
        """
        self._compat_manual = enabled
        _logger.info("Compat mode (manual): %s", enabled)

    @Slot(bool)
    def setCompatAutoDetect(self, enabled: bool) -> None:
        """Toggle auto-detection of apps that need compat mode.

        When enabled, ``_check_foreground_window`` inspects each new
        foreground window and flips ``_compat_auto_active`` if the
        window's class or owning process matches a known remote-
        desktop client (TeamViewer, RDP / mstsc, AnyDesk, VNC, ...) or
        IDE with always-on keystroke interception (VS Code + Monaco
        forks, JetBrains family).  Compat mode then activates
        automatically without requiring the user to flip the manual
        toggle each time.

        On disable, ``_compat_auto_active`` is cleared so a previously
        detected session does not leave compat mode stuck on.
        """
        self._compat_auto_enabled = enabled
        if not enabled:
            self._compat_auto_active = False
        else:
            # Re-check immediately so a relevant window currently
            # focused gets compat mode on the next keystroke instead
            # of waiting for the next 250 ms timer tick.
            self._update_compat_auto(self._last_foreground_hwnd)
        _logger.info(
            "Compat mode (auto-detect): %s (currently active=%s)",
            enabled, self._compat_auto_active,
        )

    def _in_compat_mode(self) -> bool:
        """Return whether compat mode should currently apply.

        Effective state: ``manual OR (auto_enabled AND auto_active)``.
        """
        return self._compat_manual or (
            self._compat_auto_enabled and self._compat_auto_active
        )

    def _update_compat_auto(self, hwnd: int) -> None:
        """Inspect ``hwnd`` and update ``_compat_auto_active``.

        No-op if auto-detect is disabled or the platform's detector is
        unavailable.  Called from ``_check_foreground_window`` on every
        foreground change and from ``setCompatAutoDetect`` on enable.
        """
        if not self._compat_auto_enabled or not hwnd:
            return
        try:
            new_active = _window_needs_compat_mode(hwnd)
        except Exception:
            new_active = False
        if new_active != self._compat_auto_active:
            self._compat_auto_active = new_active
            _logger.debug(
                "Compat auto-active: %s (hwnd=%s)", new_active, hwnd,
            )

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
            self._word_typed_under_caps_lock = False
            self._sentence_buffer = ""
            self._context_buffer = ""
            self.predictionsChanged.emit([])
            _logger.debug("Foreground window changed — predictions cleared")
        # Update auto-detect for compat mode on every poll (cheap on
        # Windows — class lookup is a syscall, process check only fires
        # on class miss).  Auto-active toggling is debounced internally
        # so this isn't noisy.
        self._update_compat_auto(hwnd)
        # macOS: feed the foreground pid into the synthesizer's target
        # tracking.  Redundant with the NSWorkspace activation observer
        # in MacOSKeySynthesizer but acts as defence-in-depth: the
        # observer can miss transitions (e.g. if the user activates an
        # app via a path that doesn't fire NSWorkspaceDidActivate, or
        # during the observer-install window at startup).  hwnd on
        # macOS IS the pid — see _get_foreground_window_id's macOS
        # branch.  set_target_pid filters self and no-ops when pid is
        # unchanged, so calling on every poll is cheap.
        if CURRENT_PLATFORM == "macos" and hwnd > 0:
            set_target = getattr(self._synth, "set_target_pid", None)
            if callable(set_target):
                set_target(hwnd)
        self._last_foreground_hwnd = hwnd

    def _get_foreground_window_id(self) -> int:
        """Return the focused-window ID, or 0 if unavailable.

        Windows: ``GetForegroundWindow()`` via ctypes.
        X11:    ``xdotool getactivewindow`` subprocess (~5 ms).
        macOS:  ``NSWorkspace.frontmostApplication().processIdentifier()``
                — pid stands in for window id; the bridge only uses
                this value to detect *changes*, so a stable per-app
                identifier is enough.  Multi-window apps share a pid,
                which means switching between two TextEdit documents
                won't clear context — acceptable, matches the Linux
                behaviour today, and avoids the much heavier
                ``CGWindowListCopyWindowInfo`` traversal on every
                poll.
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
            if sys.platform == "darwin":
                try:
                    from AppKit import NSWorkspace  # type: ignore[import-not-found]
                except ImportError:
                    return 0
                app = NSWorkspace.sharedWorkspace().frontmostApplication()
                if app is None:
                    return 0
                return int(app.processIdentifier())
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
        self._word_typed_under_caps_lock = False
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

    # --- Telemetry (opt-in usage stats) ---

    @Slot(result=bool)
    def getTelemetryEnabled(self) -> bool:
        """QML reads this on Settings panel mount to render the toggle."""
        return self._telemetry.enabled

    @Slot(bool)
    def setTelemetryEnabled(self, enabled: bool) -> None:
        """Toggle the opt-in telemetry pipeline.  Off → On generates a
        new anon_id and starts the weekly clock; On → Off clears the
        anon_id (so future opt-in cycles cannot be linked).  See
        docs/PRIVACY.md.
        """
        if enabled:
            self._telemetry.enable()
        else:
            self._telemetry.disable()
        _logger.info("Telemetry consent: %s", enabled)

    @Slot(result=bool)
    def forgetTelemetryData(self) -> bool:
        """Ask the server to delete this user's contributed row.
        Triggered by the 'Delete my contributed data' button in
        Settings → Data & Privacy → Privacy.  Returns True if the request was sent.
        """
        return self._telemetry.forget()

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
        """Enable a vocabulary pack by ID (the directory name under user_packs_dir)."""
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
    def markGoodSuggestion(self, word: str) -> None:
        """Boost a word in future predictions. Clears any prior dispreference
        then bumps the unigram count by the same +5 used for prediction-click
        reinforcement and records the boost so the dashboard can show it
        and the user can undo it later.
        """
        self._predictor.mark_good_suggestion(word)
        self._add_debug_log(f"Marked good: {word}")

    @Slot(str)
    def unprefer(self, word: str) -> None:
        """Roll back an explicit user boost (dashboard restore action)."""
        self._predictor.unprefer(word)
        self._add_debug_log(f"Unpreferred: {word}")

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
        self._word_typed_under_caps_lock = False

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
                # Non-Qt test runner: key_centers is already a native
                # Python dict, no QJSValue conversion needed.
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
            # Non-Qt test runner: points is already a native Python
            # list, no QJSValue conversion needed.
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
        self._word_typed_under_caps_lock = False

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
        stats["preferredCount"] = len(ngram.preferred)
        stats["blacklist"] = list(ngram.blacklist)[:30]
        stats["dispreference"] = [
            {"word": w, "count": c}
            for w, c in sorted(ngram.dispreference.items(), key=lambda x: x[1], reverse=True)[:20]
        ]
        stats["preferred"] = [
            {"word": w, "count": c}
            for w, c in sorted(ngram.preferred.items(), key=lambda x: x[1], reverse=True)[:20]
        ]

        # Analytics
        analytics = self._analytics.get_session_stats()

        return {
            "words": [{"word": w, "count": c} for w, c in sorted_words],
            "edges": edges,
            "stats": stats,
            "analytics": analytics,
        }

    @Slot(str, result="QVariant")
    def getWordContext(self, word: str) -> Dict[str, Any]:
        """Drill-down view of a single word's neighbourhood.

        Returns the word's frequency along with its top successors
        (bigram ``word → next``), top predecessors (``prev → word``),
        and top trigram windows (``X word Y``). Driven by the click-
        through panel in the language-model visualization — the cloud /
        flow views are static aggregates, so the only way to inspect
        *why* a word ranks where it does is to surface its actual
        n-gram neighbours.

        All counts come from the merged tables (``unigrams`` /
        ``bigrams`` / ``trigrams``), so base-dictionary edges show
        alongside user-typed reinforcement; the click-through is for
        understanding the model's view of the word, not just the user's
        contribution.
        """
        ngram = self._predictor._ngram
        key = (word or "").lower().strip()
        if not key:
            return {
                "word": "",
                "count": 0,
                "successors": [],
                "predecessors": [],
                "trigrams": [],
            }

        successors = sorted(
            ngram.bigrams.get(key, {}).items(),
            key=lambda kv: kv[1], reverse=True,
        )[:8]

        predecessors: list[tuple[str, int]] = []
        for prev, nexts in ngram.bigrams.items():
            cnt = nexts.get(key, 0)
            if cnt > 0:
                predecessors.append((prev, cnt))
        predecessors.sort(key=lambda kv: kv[1], reverse=True)
        predecessors = predecessors[:8]

        trigram_windows: list[tuple[str, str, int]] = []
        for tri_key, nexts in ngram.trigrams.items():
            parts = tri_key.split(" ")
            if len(parts) != 2:
                continue
            prev2, prev1 = parts
            # Word in middle position: prev2 KEY next.
            if prev1 == key:
                for nxt, cnt in nexts.items():
                    trigram_windows.append((f"{prev2} {key} {nxt}", "middle", cnt))
            # Word in trailing position: prev2 prev1 KEY.
            for nxt, cnt in nexts.items():
                if nxt == key:
                    trigram_windows.append(
                        (f"{prev2} {prev1} {key}", "trailing", cnt),
                    )
        trigram_windows.sort(key=lambda t: t[2], reverse=True)
        trigram_windows = trigram_windows[:6]

        return {
            "word": key,
            "count": int(ngram.unigrams.get(key, 0)),
            "userCount": int(ngram.user_vocab.get(key, 0)),
            "successors": [
                {"word": w, "count": int(c)} for w, c in successors
            ],
            "predecessors": [
                {"word": w, "count": int(c)} for w, c in predecessors
            ],
            "trigrams": [
                {"phrase": phrase, "position": pos, "count": int(c)}
                for phrase, pos, c in trigram_windows
            ],
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
