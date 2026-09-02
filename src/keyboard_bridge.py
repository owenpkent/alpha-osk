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
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot

# Audio feedback — optional, gracefully degrades if QtMultimedia unavailable
try:
    from PySide6.QtMultimedia import QSoundEffect

    _HAS_AUDIO = True
except ImportError:
    _HAS_AUDIO = False

from .__version__ import __version__ as APP_VERSION
from .analytics import TypingAnalytics
from .glyphs import MAX_RECENT
from .glyphs import categories as glyph_categories
from .platform import CURRENT_PLATFORM, create_key_synthesizer
from .platform.base import KeySynthesizerBase
from .platform.password_detect import (
    caret_position_token,
    detection_available,
    focused_element_token,
    is_password_field,
)
from .platform.pointer import external_click_detected
from .prediction import HybridPredictor
from .prediction.fuzzy_recognizer import positions_from_layout
from .prediction.token_predictor import TokenPredictor
from .snippets import MAX_SNIPPETS, SNIPPET_COLORS, SnippetStore
from .telemetry import TelemetryClient
from .text_patterns import (
    detect_snippet_candidate,
    is_email,
    is_phone,
    label_for_kind,
    suppresses_auto_space,
    suppression_is_provisional,
)
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
_COMPAT_WINDOW_CLASSES = frozenset(
    {
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
    }
)

_COMPAT_PROCESS_NAMES = frozenset(
    {
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
    }
)


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

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
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
            PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid.value,
        )
        if not handle:
            return False
        try:
            exe_buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if kernel32.QueryFullProcessImageNameW(
                handle,
                0,
                exe_buf,
                ctypes.byref(size),
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


# Games whose foreground window should switch key synthesis to the held
# down/up path (see _window_is_game / _GAME_KEY_HOLD_SECONDS). Games read the
# keyboard by polling state once per render frame (DirectInput / Raw Input /
# GetAsyncKeyState), so a zero-gap key-down+key-up injected in one SendInput
# batch can land entirely between two polls and be missed: the user sees the
# keystroke do nothing. Holding the key down for ~one frame fixes it. Matched
# by exe basename (lowercased), exactly like _COMPAT_PROCESS_NAMES; extend this
# set as reports of other unresponsive games come in.
_GAME_PROCESS_NAMES = frozenset(
    {
        # Age of Empires family
        "aoe2de_s.exe",  # Age of Empires II: Definitive Edition
        "aoe3de_s.exe",  # Age of Empires III: Definitive Edition
        "aoede_s.exe",  # Age of Empires: Definitive Edition
        "reliccardinal.exe",  # Age of Empires IV
        "age2_x1.exe",  # Age of Empires II: The Conquerors (classic)
        "age2_x2.exe",  # AoE II HD: Forgotten Empires
        "aoe2hd.exe",  # Age of Empires II: HD Edition
        "empires2.exe",  # Age of Empires II (original)
    }
)


def _owning_exe_name(hwnd: int) -> Optional[str]:
    """Lowercased basename of ``hwnd``'s owning-process exe, or None.

    None on non-Windows or any failure (fail-safe). Same Win32 path
    ``_window_needs_compat_mode`` uses for its exe lookup.
    """
    import sys

    if sys.platform != "win32" or not hwnd:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid.value,
        )
        if not handle:
            return None
        try:
            exe_buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if kernel32.QueryFullProcessImageNameW(
                handle,
                0,
                exe_buf,
                ctypes.byref(size),
            ):
                return Path(exe_buf.value).name.lower()
        finally:
            kernel32.CloseHandle(handle)
    except (OSError, AttributeError):
        pass
    return None


def _window_is_borderless_fullscreen(hwnd: int) -> bool:
    """Whether ``hwnd`` is a borderless window covering its whole monitor.

    This is the catch-all for unlisted games: a borderless / exclusive-
    fullscreen game (no title bar, rect spanning the entire monitor,
    including the taskbar strip) looks like this, while a normal maximized
    window keeps its ``WS_CAPTION`` title bar and leaves the taskbar
    visible, so it does not match. Requiring BOTH "covers the full
    monitor" AND "no caption" keeps the false-positive surface down to
    fullscreen media players / slideshows, where a 50 ms key hold is
    harmless. (Fullscreen productivity apps like an F11 browser or a
    fullscreen IDE are excluded separately in ``_window_is_game`` by exe
    name.)

    Returns False on non-Windows or any failure (fail-safe).
    """
    import sys

    if sys.platform != "win32" or not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        # Local WinDLL instance so setting argtypes/restype (needed to keep the
        # 64-bit HMONITOR handle from being truncated to int) doesn't mutate the
        # shared ``ctypes.windll.user32`` prototypes other call sites rely on.
        user32 = ctypes.WinDLL("user32")
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = ctypes.c_void_p
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = wintypes.LONG

        hwnd_h = wintypes.HWND(hwnd)
        rect = RECT()
        if not user32.GetWindowRect(hwnd_h, ctypes.byref(rect)):
            return False
        MONITOR_DEFAULTTONEAREST = 2
        hmon = user32.MonitorFromWindow(hwnd_h, MONITOR_DEFAULTTONEAREST)
        if not hmon:
            return False
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return False
        m = mi.rcMonitor
        covers = (
            rect.left <= m.left
            and rect.top <= m.top
            and rect.right >= m.right
            and rect.bottom >= m.bottom
        )
        if not covers:
            return False
        GWL_STYLE = -16
        WS_CAPTION = 0x00C00000
        style = user32.GetWindowLongW(hwnd_h, GWL_STYLE)
        return bool((style & WS_CAPTION) == 0)
    except (OSError, AttributeError):
        return False


def _window_is_game(hwnd: int) -> bool:
    """Whether ``hwnd`` should use the held key-synthesis path.

    Two signals, in order:
    1. Owning-process exe in ``_GAME_PROCESS_NAMES`` (catches games even
       in windowed mode).
    2. Borderless-fullscreen heuristic (``_window_is_borderless_fullscreen``),
       the zero-config catch-all for unlisted games. Skipped for exes in
       ``_COMPAT_PROCESS_NAMES`` (IDEs / remote-desktop clients): those are
       productivity apps that are sometimes run fullscreen, and adding a
       50 ms key hold there would lag normal typing.

    Returns False on non-Windows or any failure (fail-safe).
    """
    import sys

    if sys.platform != "win32" or not hwnd:
        return False
    exe = _owning_exe_name(hwnd)
    if exe is not None:
        if exe in _GAME_PROCESS_NAMES:
            return True
        if exe in _COMPAT_PROCESS_NAMES:
            return False
    return _window_is_borderless_fullscreen(hwnd)


# How long (seconds) to hold a single key down when the foreground app is a
# game. ~50 ms spans 1.5 frames at 30 fps and 3 frames at 60 fps, comfortably
# crossing at least one keyboard-state poll. Only applied on the game path so
# normal typing keeps its zero-latency atomic injection.
_GAME_KEY_HOLD_SECONDS = 0.05


# How often to ask whether the user clicked outside the keyboard. Fast on
# purpose: the answer is paired with the pointer's *current* position, so
# every millisecond between the press and the reading is a millisecond the
# pointer has to travel back onto a key and be mistaken for one of ours.
# Two syscalls per tick when nothing was clicked, so the cost is noise next
# to the 200 ms password poll.
_CLICK_POLL_MS = 50

# How long an outside click is given to prove it moved the caret.
#
# The click poll sees the press up to _CLICK_POLL_MS after it happened,
# and the app it landed in may not have processed it yet, so a caret read
# taken at that instant can still show the old position.  Deciding there
# and then would answer "did the caret move" with "not yet", which is the
# wrong answer to a different question.  So the decision is settled over
# a short window instead: any change inside it means the caret moved.
#
# 200 ms is four poll ticks, generous for a Win32 app to move a caret in
# response to a click it has already been handed.  The cost of the window
# is that a genuinely stale bar stays up for that long, which is not
# reachable in practice: taking it means moving the pointer off whatever
# was just clicked, back onto a pill, and clicking again, and no hand
# does that in 200 ms.
_CLICK_SETTLE_MS = 200


# Cursor-movement keys. When a sticky modifier is held, pressing one of
# these should KEEP Shift/Ctrl held (extend selection / word-jump across
# multiple presses) instead of auto-releasing after the first press. See
# the auto-release block in pressSpecialKey for the full rationale.
_NAV_KEYS = frozenset(
    {
        "left",
        "right",
        "up",
        "down",
        "home",
        "end",
        "pageup",
        "pagedown",
    }
)

_logger = logging.getLogger("KeyboardBridge")

# Cap on ``_raw_token``.  It only ever answers "does the run before the
# cursor have a recognisable shape", and every shape that matters (an
# email, a URL, a decimal) is decided well inside this, so an unbounded
# buffer would only be storing typed characters for no benefit.
_MAX_RAW_TOKEN_LEN = 128

# Special keys after which ``_raw_token`` no longer describes the run
# before the cursor: every one of them either inserts whitespace, moves
# the caret somewhere we aren't tracking, or deletes text ahead of it.
# Backspace is deliberately absent — it is the one key whose effect on
# the run we can follow exactly, by popping a character.
_TOKEN_BREAKING_KEYS = _NAV_KEYS | frozenset({"tab", "escape", "delete"})

# Ctrl chords an edit field handles itself, letter -> the name emitted on
# ``editSpecialPressed``.  Deliberately the clipboard set and undo, and
# nothing that would need to reach the OS: a chord in edit mode is for the
# field in front of the user, never for the application behind it.
_EDIT_CHORDS = {
    "a": "selectall",
    "c": "copy",
    "v": "paste",
    "x": "cut",
    "z": "undo",
    "y": "redo",
}

# How far back through ``_context_buffer`` snippet detection looks for a
# shape that spans whitespace (an address, a spaced-out phone number).
# Bounded so something typed a paragraph ago doesn't surface as an offer
# at an unrelated word boundary; long enough for any of the shapes.
_SNIPPET_SCAN_TAIL = 120

# How many already-offered values to remember before the oldest fall
# off.  These are emails, phone numbers and addresses the user typed,
# so the ledger is bounded rather than session-long; overflowing costs
# at most a re-offer of something dismissed long ago.
_MAX_REMEMBERED_OFFERS = 64


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
    # "Locked" = a modifier the user right-clicked to hold down.  Unlike
    # the sticky/one-shot active state, a locked modifier survives the
    # per-keystroke auto-release, so several combos (Ctrl+C, Ctrl+V) or a
    # long Shift-selection can run without re-tapping.  Surfaced
    # separately so QML can draw a distinct "held" indicator.
    shiftLockedChanged = Signal(bool)
    ctrlLockedChanged = Signal(bool)
    altLockedChanged = Signal(bool)
    winLockedChanged = Signal(bool)
    currentLayerChanged = Signal(str)

    # Prediction signals
    predictionsChanged = Signal(list)  # Instant predictions
    predictionsRefined = Signal(list)  # LLM-refined predictions
    predictionLoading = Signal(bool)  # LLM loading state
    llmEnabledChanged = Signal(bool)  # LLM enabled state
    llmAvailableChanged = Signal(bool)  # LLM available state
    predictionCountChanged = Signal(int)  # Prediction count changed
    predictionStatsChanged = Signal()  # Stats updated

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
    editKeyTyped = Signal(str)  # char to insert at cursor
    editSpecialPressed = Signal(str)  # special key name (backspace, left, etc.)

    # Emitted after the snippet list changes (add / edit / delete /
    # move) so the Snippets popup re-queries getSnippets() and rebuilds
    # its rows.
    snippetsChanged = Signal(list)

    # Emitted when a just-typed email / phone / address looks worth
    # saving: (kind, label, value).  QML shows a one-tap offer and calls
    # acceptSnippetOffer() / dismissSnippetOffer().  Suppressed in
    # privacy mode along with everything else that touches typed content.
    snippetOffered = Signal(str, str, str)

    # Emitted when a live offer stops being about what the user is
    # doing (app switch, context reset, privacy mode).  QML closes the
    # toast; without it a Save button stays on screen doing nothing.
    snippetOfferWithdrawn = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._shift_active = False
        self._caps_lock_active = False
        self._ctrl_active = False
        self._alt_active = False
        self._win_active = False
        # Right-click "lock" flags: a locked modifier is held at the OS
        # level and exempt from the per-keystroke auto-release below.
        # A locked modifier is always also active (held); clearing the
        # lock releases it.
        self._shift_locked = False
        self._ctrl_locked = False
        self._alt_locked = False
        self._win_locked = False
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
        # True iff a context reset emptied _current_word while the user
        # was part-way through a word, so what accumulates from here is
        # the *tail* of a word whose head is still on screen.  Travels
        # with _word_typed_under_caps_lock above: same per-word lifetime,
        # cleared at the same boundaries.  Its one consumer is the word-
        # completion learning gate, because learning "mentation" from an
        # interrupted "documentation" writes a fragment into user_vocab,
        # the analytics word table, the dashboard's Top Words and the
        # Data Backup archive, and then prefix-matches to the top of the
        # pill bar for ever after.  See _take_lost_prefix.
        self._word_prefix_lost = False
        self._sentence_buffer = ""  # Accumulates words for sentence-level learning
        self._predictions: List[str] = []
        self._auto_space_after_punctuation = True
        self._auto_capitalize_after_punctuation = False
        # Auto-capitalize is NOT a held Shift.  Setting _shift_active
        # for it looked equivalent and was not: _send_key builds its
        # chord modifiers straight from that flag, so after a sentence
        # ending, Enter became Shift+Enter (a newline in Slack rather
        # than send), Ctrl+C became Ctrl+Shift+C, and arrows started
        # extending a selection.  It was also the one site that set
        # _shift_active with no paired hold_modifier(), so the bridge
        # believed in a hold the OS did not have.  This flag capitalises
        # the next character and touches nothing else.
        self._pending_auto_cap = False
        # The punctuation whose auto-space was skipped *provisionally*
        # (see text_patterns.suppression_is_provisional), empty when
        # nothing is owed.  A bare number is the one token shape where
        # "3" + "." and "42" + "." are indistinguishable at the moment
        # the dot lands, so the space is withheld and delivered on the
        # next character if that character proves it was prose.
        self._deferred_auto_space = ""
        self._auto_save_on_exit = True
        # Intelligent spacing — skip the punctuation auto-space when it
        # would break a structured token (an email, a URL, a decimal).
        # See src/text_patterns.py for the shapes and the known gap.
        self._intelligent_spacing = True
        # The unbroken run of characters immediately before the cursor,
        # punctuation included.  Deliberately NOT _current_word, which is
        # the prediction engine's notion of a word and resets at "@" and
        # every other non-word character — so by the time the "." in
        # "owen@gmail.com" arrives, _current_word is "gmail" and the "@"
        # that proves this is an email is already gone.  Reset on any
        # whitespace, cursor motion, or verbatim insert; trimmed by
        # backspace.  Only maintained outside privacy mode, same as
        # _current_word: it holds typed characters.
        self._raw_token = ""
        # The last run handed to the token store, so one user action
        # cannot count as two sightings.  See _learn_raw_token.
        self._learned_raw_token = ""
        # Structured-token pills: displayed text -> the text a tap should
        # type.  These two differ, which is the whole reason for the map:
        # a domain pill reads "gmail.com" while the run before the cursor
        # is "owen@", so neither `word` nor `_current_word` tells
        # pressPrediction what to insert.  Rebuilt on every token-pill
        # emit and emptied whenever the bar goes back to words, so a
        # stale entry can never be tapped.
        self._token_pill_words: Set[str] = set()
        # The run the pills currently on the bar continue: the tail after
        # the "@" for domain pills, the whole run otherwise.  Kept beside
        # the map because the insert path cannot recover it from a pair,
        # and because the case check needs the characters as typed.
        self._token_pill_typed: str = ""
        # Snippet auto-detection — offer to save an email / phone /
        # address the user just typed into Snippets (see
        # _maybe_offer_snippet).
        self._snippet_detection_enabled = True
        # Values already offered this session, accepted or dismissed, so
        # a declined offer stays declined and an accepted one doesn't
        # immediately re-offer itself.  Session-scoped by design: this is
        # a nag guard, not a preference worth persisting.  A dict used as
        # an ordered set so _remember_offered can bound it -- it holds
        # typed values, which must not accumulate for a whole session.
        self._offered_snippet_values: Dict[str, None] = {}
        # The offer currently on screen as ``(kind, value)``, or None.
        self._pending_snippet_offer: Optional[Tuple[str, str]] = None
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

        # Game auto-compat: whether the current foreground window belongs to a
        # known polling game (``_GAME_PROCESS_NAMES``).  When True, single keys
        # are synthesised with a brief key-down hold so a frame-polling game
        # observes the press.  Updated by ``_check_foreground_window`` on every
        # poll, the same place ``_compat_auto_active`` is.
        self._game_auto_active = False

        # Privacy mode — suppresses prediction and learning
        self._privacy_mode = False
        self._privacy_mode_manual = False  # User toggled manually
        self._password_detect_enabled = True
        # Whether the platform's auto-detection backend actually works this
        # session, vs. silently falling back to the null detector (no AT-SPI
        # on Linux, no Accessibility TCC grant on macOS, etc). Computed once
        # here -- the module picks its detector on first use and keeps it for
        # the process lifetime -- and surfaced read-only to QML so the UI can
        # tell the user the manual Learning toggle is their only protection.
        # This call is also what lazily creates the cached detector and logs
        # the one-time startup warning when it falls back to null.
        self._password_detection_available = detection_available()
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
        # Identity of the last-focused UI element (UIA RuntimeId on Windows,
        # None elsewhere). Lets the foreground poll notice focus moving
        # between two controls inside the *same* window — e.g. two text
        # boxes on one web page — which the window-handle check can't see.
        self._last_focus_token: Optional[str] = None
        # Where the caret sat at the last poll, and whether the user
        # typed anything since.  Together they separate a caret move
        # the user made (click into another paragraph) from one we
        # made (typing), which the element token cannot distinguish.
        self._last_caret_token: Optional[str] = None
        self._keystroke_since_poll = False
        self._foreground_timer = QTimer(self)
        self._foreground_timer.setInterval(250)
        self._foreground_timer.timeout.connect(self._check_foreground_window)
        self._foreground_timer.start()

        # A click landing outside our own window is the user moving the
        # caret, and it is the one signal that survives the case the other
        # three miss: two fields inside a single window, where the UIA
        # element id is shared and no caret is published.  Polled on its own
        # fast timer rather than folded into the 250 ms foreground poll
        # because the check reads the pointer's *current* position, so the
        # closer the reading sits to the press, the less chance the pointer
        # has already travelled back onto the keyboard.
        # The caret token as of the *previous* click poll, which is the
        # only baseline that reliably predates a press: the press is
        # detected as a transition against the previous poll, so the
        # previous poll's reading is necessarily from before it.
        # `_last_caret_token` cannot serve here -- it is maintained by
        # the 4 Hz foreground poll, which lands between a click and its
        # detection often enough to matter (a 50 ms window in every
        # 250 ms), and when it does it has already been overwritten with
        # the post-click value.
        self._caret_before_click: Optional[str] = None
        # Whether we synthesized anything since the previous *click*
        # poll.  A separate flag from `_keystroke_since_poll` rather
        # than a shared one: the two polls run at 4 Hz and 20 Hz and
        # each consumes the flag it reads, so one flag would mean
        # whichever fired first ate the evidence the other needed.
        self._keystroke_since_click_poll = False
        # Set while an outside click is waiting to see whether the caret
        # moves: (deadline, the caret token to compare against).
        self._click_settle: Optional[Tuple[float, str]] = None
        self._click_timer = QTimer(self)
        self._click_timer.setInterval(_CLICK_POLL_MS)
        self._click_timer.timeout.connect(self._check_external_click)
        self._click_timer.start()

        # Auto-update — last fetched UpdateInfo, used by installUpdate()
        # so the QML side doesn't have to round-trip the URL/asset name
        # back through Python (and so we never trust QML-supplied URLs).
        self._update_info: Optional[UpdateInfo] = None
        self._update_check_in_flight = False

    # --- Key synthesis (delegated to platform layer) ---

    def _note_own_keystroke(self) -> None:
        """Record that *we* just moved the caret, for both caret polls.

        Two polls compare a caret token across their own ticks, and both
        have to tell our own insert from the user clicking somewhere
        else: ``_check_caret_moved`` at 4 Hz and ``_check_external_click``
        at 20 Hz.  Each consumes the flag it reads, so they cannot share
        one -- whichever ran first would eat the evidence the other
        needed.  Setting them happens here, once, so a future insert
        path gets both by construction rather than by whoever adds it
        remembering: parallel blocks that must be kept in sync are the
        failure this file warns about elsewhere, and the click poll was
        already an instance of it.
        """
        self._keystroke_since_poll = True
        self._keystroke_since_click_poll = True

    def _send_key(self, key_name: str, hold_seconds: float = 0.0) -> None:
        """
        Send a single key event via the platform synthesizer.

        Automatically attaches any active sticky modifiers (Ctrl, Alt, Win)
        to the keystroke.

        ``hold_seconds`` > 0 holds the key down briefly (game-compat path,
        see ``_key_hold_seconds`` / ``WindowsKeySynthesizer.send_key``).
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

        mods = modifiers if modifiers else None
        self._note_own_keystroke()
        # Only thread hold_seconds through when actually holding (game mode) so
        # the common zero-hold call keeps its original two-arg signature.
        if hold_seconds > 0:
            self._synth.send_key(key_name, modifiers=mods, hold_seconds=hold_seconds)
        else:
            self._synth.send_key(key_name, modifiers=mods)

    def _send_text(self, text: str) -> None:
        """Send a string of text via the platform synthesizer."""
        self._note_own_keystroke()
        self._synth.send_text(text)

    def _replace_text(self, backspaces: int, text: str) -> None:
        """Select the last *backspaces* characters and overwrite with *text*."""
        self._note_own_keystroke()
        self._synth.replace_text(backspaces, text)

    def _suppress_auto_space(self, token_before: str, punct: str) -> bool:
        """Should the punctuation auto-space be skipped this time?

        Thin wrapper over :func:`text_patterns.suppresses_auto_space` that
        honours the user's Intelligent Spacing setting.  The decision is
        made entirely from the token, deliberately: an earlier version
        also passed a "has anything with a space been typed yet" hint
        derived from ``_context_buffer``, which turned out to be a proxy
        for the wrong thing -- that buffer is emptied on every app switch
        and focused-element change, so the hint was true at the start of
        every newly focused field and ate the space after its first
        sentence.  See ``suppresses_auto_space`` for the full account.
        """
        if not self._intelligent_spacing:
            return False
        return suppresses_auto_space(token_before, punct)

    def _defer_auto_space(self, token_before: str, punct: str) -> None:
        """Remember that *punct*'s auto-space was skipped on thin evidence.

        Only a bare run of digits qualifies (see
        ``text_patterns.suppression_is_provisional``): "3" and "42" are
        the same token, so the dot is either a decimal point or a full
        stop and nothing on screen yet says which.
        """
        self._deferred_auto_space = punct if suppression_is_provisional(token_before, punct) else ""

    def _consume_auto_cap(self) -> None:
        """Spend a pending auto-capital on a verbatim insert.

        A tapped pill and a snippet are each "the next thing typed", so
        they spend the capital exactly as a character does.  The capital
        itself is already *in* the inserted text -- ``_display_cased``
        applies it to pills, and a snippet is verbatim by definition --
        so all that is missing is
        clearing the flag.  Without that the capital stays owed and
        lands later on some unrelated character: type "hello. ", tap a
        pill, and the next letter you type comes out uppercase mid-word.
        """
        if not self._pending_auto_cap:
            return
        self._pending_auto_cap = False
        self._update_layer()

    def _take_lost_prefix(self) -> bool:
        """True iff the word just completed had its opening cut away.

        A context reset that lands mid-word empties ``_current_word``
        while its characters are still on screen, so everything typed
        from there to the next boundary is the tail of a word the bridge
        never saw the start of.  Type "docu", take an outside click on a
        scrollbar (the caret-neutral false positive
        :meth:`_check_external_click` deliberately accepts), finish
        "mentation ", and the space branch would otherwise learn
        "mentation" as a word: it reaches ``user_vocab``, the analytics
        word table, the dashboard's Top Words and the Data Backup
        archive, and prefix-matches to rank 1 for every later "ment".
        That is persistent, exported corruption, where the desynced
        mirror the reset also causes is transient.

        Consumed at each of the three sites that learn from
        ``_current_word`` (space, sentence punctuation, Return), so the
        suppression covers exactly one word.  Missing a learn is the
        cheap direction; the codebase takes that trade everywhere else
        it decides what may enter the model.
        """
        lost = self._word_prefix_lost
        self._word_prefix_lost = False
        return lost

    def _take_deferred_space(self, prose: bool) -> Tuple[str, bool]:
        """Settle a deferred auto-space: ``(text to type, capital owed)``.

        *prose* is the caller's verdict on what follows: a letter, a
        tapped pill or a snippet both mean the punctuation
        ended a sentence after all, so the withheld space is owed.  A
        digit (``prose=False``) confirms the decimal and there was never
        a space to send.  Either way the deferral is spent, so this must
        be called on every path that types after one was armed.

        It **returns** the space rather than sending it, so each caller
        can emit it the same way it emits the text that follows.  That is
        not tidiness: the verbatim inserts type inside
        ``_without_held_modifiers``, and a space sent outside it would
        arrive as a chord under a right-click-locked Ctrl (Ctrl+Space is
        an IME toggle on Windows) -- a hole in the very guard that
        exists to stop a held modifier rewriting an insert.
        """
        punct = self._deferred_auto_space
        self._deferred_auto_space = ""
        if not punct or not prose:
            return "", False
        if not self._privacy_mode:
            # Mirror it in the buffer for the same reason the ordinary
            # auto-space is mirrored: a pill click inserts against what
            # the buffer says is on screen.
            self._context_buffer += " "
        return " ", self._auto_capitalize_after_punctuation and punct in (".", "!", "?")

    @contextmanager
    def _without_held_modifiers(self) -> Iterator[None]:
        """Run a verbatim insert with every held modifier temporarily down.

        Verbatim inserts (prediction pills, snippets, the autocorrect
        retype) push text the user never typed character by
        character, and a modifier standing held at the OS level silently
        rewrites all of it.  With Shift down the scancode path emits
        "HELLO" for "hello" -- ``_make_char_scancode_events`` only knows
        not to *add* a redundant Shift wrap, it cannot cancel a standing
        hold -- and with Ctrl down every character arrives as a chord
        rather than as text.  Sticky and right-click-locked modifiers both
        survive a pill tap, so this is reachable in ordinary use rather
        than only mid-chord.

        **It wraps the whole insert, not just the text.**  Two of
        ``pressPrediction``'s branches never reach ``send_text`` at all:
        compat mode sends ``BackSpace`` N times, and the casing-mismatch
        branch calls ``replace_text``, whose Shift+Left selection is
        itself a chord.  Under a locked Alt those backspaces arrive as
        Alt+BackSpace (undo, N times); under a locked Ctrl the selection
        becomes Ctrl+Shift+Left and swallows three preceding *words*,
        which the insert then overwrites.  Guarding only the text half
        left the two most destructive branches unguarded.

        Restores in a ``finally`` because leaving a locked modifier up at
        the OS while the keycap still shows it held is the worse of the
        two failures.

        This reads the ``_*_active`` flags rather than querying the OS,
        which is sound **only because those flags always imply a real
        hold**.  That invariant was briefly false: auto-capitalize used to
        set ``_shift_active`` with no ``hold_modifier``, and the restore
        below would then have pinned a Shift that was never down.  Any new
        code that sets an ``_*_active`` flag must pair it with a hold.

        The single-character path in ``_press_char`` deliberately does not
        use this: there a held Shift is doing its job (it is what makes
        the keystroke uppercase), and a release plus re-hold on every
        keystroke would be churn on the hottest path in the app.  Callers
        that also want the sticky auto-release should do it *before*
        entering, which leaves nothing for this to restore.
        """
        held = [name for name in self._MODIFIERS if getattr(self, f"_{name}_active", False)]
        for name in held:
            self._synth.release_modifier(name)
        try:
            yield
        finally:
            for name in held:
                self._synth.hold_modifier(name)

    def _send_literal_text(self, text: str) -> None:
        """Type ``text`` verbatim, immune to any modifier held at the OS level."""
        with self._without_held_modifiers():
            self._send_text(text)

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

    # Marks that may stand between a sentence boundary and the first
    # letter of the sentence, and so do not spend a pending auto-capital:
    # it is owed to that letter, not to the quote or bracket in front of
    # it.  `hi. "hello"` and `hi. (see below)` both capitalise the letter.
    # Closers are in the set for the same reason from the other side:
    # `he said "hi." Then` and `(see hi.) Then` each put one between the
    # full stop and the word that owns the capital.  Everything else
    # spends it, a digit included, since `hi. 5 apples` starts its
    # sentence with the digit and there is no capital to place.
    _CARRIES_AUTO_CAP = {'"', "'", "(", ")", "[", "]", "{", "}"}

    def _closes_a_quotation(self) -> bool:
        """Is the `"` about to be typed a closing quote rather than an opening one?

        `"` is the one mark that is both, so it cannot join
        `_NO_SPACE_BEFORE` outright the way `)` and `]` can: `he said
        "hello"` wants our auto-space kept before the first quote and
        removed before the second.  Parity over the on-screen mirror
        answers it: an odd number of quotes already on screen means we
        are inside a quotation, so this one closes it.

        A `_context_buffer` that was truncated at 200 chars or reset by
        an app switch reads as even, which keeps the space.  That is the
        safe way to be wrong: a stray space the user can delete, rather
        than one we deleted out from under them.
        """
        return self._context_buffer.count('"') % 2 == 1

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
            # A chord is not a character.  Ctrl/Alt/Win were documented as
            # "ignored inside the field so stray chords cannot leak to the
            # app behind us", and the leak half was true, but the other half
            # was not: the modifier was dropped and the *letter* inserted, so
            # Ctrl+A in the snippets editor typed "a" and Ctrl+B typed "b".
            #
            # The clipboard four are the reason this matters rather than a
            # tidiness point.  Every character of a long address costs a
            # click here, so being able to copy one from somewhere else and
            # paste it in is the difference between a snippet being worth
            # making and not.  They are handled locally and never reach the
            # OS, which is what the original rule was protecting.
            if self._ctrl_active or self._alt_active or self._win_active:
                action = _EDIT_CHORDS.get(key.lower()) if self._ctrl_active else None
                if action:
                    self.editSpecialPressed.emit(action)
                # Anything else is swallowed rather than typed as a letter.
                self._release_edit_chord_modifiers()
                return
            # _pending_auto_cap is deliberately not consulted here.  It is
            # owed to the *app behind us* -- the period that armed it was
            # typed there, and nothing typed inside an edit field can arm
            # one, because this branch returns before the punctuation
            # handling below.  Applying it here spent nothing and cleared
            # nothing, so after typing "hello." every single character
            # entered in the prediction-edit popup or the snippets editor
            # came out uppercase.  Leaving it armed keeps the capital for
            # the character it was actually meant for.
            if literal:
                char = key
            elif self._shift_active or self._caps_lock_active:
                char = key.upper()
            else:
                char = key.lower()
            self.editKeyTyped.emit(char)
            # Auto-release shift after one keypress (caps lock persists;
            # a right-click-locked Shift also stays held).
            if self._shift_active and not self._caps_lock_active and not self._shift_locked:
                self._shift_active = False
                self._synth.release_modifier("shift")
                self._update_layer()
                self.shiftActiveChanged.emit(False)
            return

        # Close the 200 ms race window: if focus has just landed on a
        # password field, flip privacy mode *before* we touch any
        # prediction state with this keystroke.
        self._check_password_field_sync()
        self._note_own_keystroke()
        self._play_click()
        if not self._privacy_mode:
            self._analytics.record_keystroke(key)
        if literal:
            char = key
        elif self._shift_active or self._caps_lock_active or self._pending_auto_cap:
            char = key.upper()
        else:
            char = key.lower()

        # Settle a provisionally suppressed auto-space now that the next
        # character is known: a letter after "42." means it was the end of
        # a sentence, so the space (and any capital it owed) is delivered
        # one keystroke late.  A digit means it was "3.14" and there was
        # never a space to send.  Anything else is left alone rather than
        # guessed at.  Purely additive: nothing typed is ever taken back.
        if self._deferred_auto_space:
            deferred_space, owes_capital = self._take_deferred_space(char.isalpha())
            if deferred_space:
                self._send_text(deferred_space)
            if owes_capital and not literal:
                char = char.upper()

        # Handle punctuation spacing — remove preceding space only if WE
        # auto-inserted it (after a prediction click or punctuation auto-
        # space).  Never undo a space the user typed manually: a visible
        # backspace flicker after their own keystroke is surprising and
        # in some apps (rich-text editors, web fields) has side effects
        # like clobbering selection state or undo history.
        #
        # A closing `"` counts too, but only once parity says it closes
        # something (see _closes_a_quotation).
        closes_quote = char == '"' and self._closes_a_quotation()
        if (
            (char in self._NO_SPACE_BEFORE or closes_quote)
            and self._auto_space_pending
            and self._context_buffer.endswith(" ")
            and not self._current_word
        ):
            self._send_key("BackSpace")
            self._context_buffer = self._context_buffer[:-1]
            _logger.info("Removed auto-space before punctuation")

        # Any keystroke clears the flag — it tracks one specific window:
        # the moment between us inserting an auto-space and the user's
        # immediate next keystroke.  Set again below if this keystroke
        # itself adds an auto-space (after . , ; : ! ?).
        self._auto_space_pending = False

        # A pending auto-capital is spent on the character this keystroke
        # types, so record that it was owed *before* the punctuation
        # branches below get a chance to arm a fresh one — and record
        # separately whether they did, because the two are not mutually
        # exclusive.  "Wait!" arms a capital, and the "?" of "Wait!?"
        # both spends that one and arms another; clearing on the spend
        # alone threw the new one away and left the next sentence
        # lowercase.  Same for "..." and for any run of sentence-ending
        # punctuation.
        consumed_auto_cap = self._pending_auto_cap
        rearmed_auto_cap = False

        # Use _send_key for modifier combos (Ctrl+C, Win+Shift+S, etc.)
        # Send the lowercase key — Shift is included as a modifier by _send_key
        if self._ctrl_active or self._alt_active or self._win_active:
            self._send_key(key.lower(), hold_seconds=self._key_hold_seconds())
            # Don't update _current_word or predictions — this was a shortcut,
            # not text input. Skip the rest of character handling.
            # Auto-release each modifier after one keypress unless it's
            # right-click-locked (held down until the user releases it) —
            # locked lets Ctrl+C, Ctrl+V, ... fire without re-tapping.
            if self._shift_active and not self._caps_lock_active and not self._shift_locked:
                self._shift_active = False
                self._synth.release_modifier("shift")
                self._update_layer()
                self.shiftActiveChanged.emit(self._shift_active)
            if self._ctrl_active and not self._ctrl_locked:
                self._synth.release_modifier("ctrl")
                self._ctrl_active = False
                self.ctrlActiveChanged.emit(self._ctrl_active)
            if self._alt_active and not self._alt_locked:
                self._synth.release_modifier("alt")
                self._alt_active = False
                self.altActiveChanged.emit(self._alt_active)
            if self._win_active and not self._win_locked:
                self._synth.release_modifier("win")
                self._win_active = False
                self.winActiveChanged.emit(self._win_active)
            return
        elif self._in_game_mode():
            # Polling game: send the single char as a held key so it survives
            # a per-frame keyboard-state poll. send_key resolves the char's VK
            # and applies shift for case the same way send_text's scancode
            # path does, so the held tap matches what would otherwise be typed.
            self._send_key(char, hold_seconds=self._key_hold_seconds())
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

            # Snapshot the run *before* this character, then extend it.
            # Intelligent spacing asks "would a space here break what is
            # already on screen", so it needs the token as it stood when
            # the punctuation landed, not including the punctuation.
            token_before = self._raw_token
            self._raw_token = (self._raw_token + char)[-_MAX_RAW_TOKEN_LEN:]
            # The run changed, so whatever was learned last no longer
            # describes it: typing the same number again must count again.
            self._learned_raw_token = ""

            # Sentence-ending punctuation triggers sentence learning
            if char in (".", "!", "?"):
                # A word whose opening a reset cut away ends the sentence
                # without joining it: it is a tail, not a word.  See
                # _take_lost_prefix.
                sentence = self._sentence_buffer
                if not self._take_lost_prefix():
                    sentence += self._current_word
                if sentence.strip():
                    new_words = self._predictor.learn(sentence.strip())
                    if new_words:
                        for nw in new_words:
                            self._add_debug_log(f'NEW WORD learned: "{nw}"')
                            _logger.info("New word learned (len=%d)", len(nw))
                self._sentence_buffer = ""
                self._current_word = ""
                self._word_typed_under_caps_lock = False
                suppressed = self._auto_space_after_punctuation and self._suppress_auto_space(
                    token_before, char
                )
                if self._auto_space_after_punctuation and not suppressed:
                    self._send_text(" ")
                    self._auto_space_pending = True
                    # Not suppressed means intelligent spacing judged the
                    # punctuation to be prose rather than part of the
                    # token, so the run before it is finished: "my zip is
                    # 02134." learns "02134".  The suppressed branch
                    # deliberately does not learn: there the token is
                    # still being typed.
                    self._learn_raw_token(token_before)
                    self._raw_token = ""
                elif suppressed:
                    self._defer_auto_space(token_before, char)
                # Mirror on screen: no space sent means no space in the
                # buffer either, or a pill click would insert against a
                # prefix that isn't there.  The auto-space-off path is
                # deliberately left alone (it has always recorded the
                # boundary even without sending a space).
                self._context_buffer += char if suppressed else char + " "
                # Auto-capitalize next letter — but not mid-token, or
                # "example.com" comes out "example.Com".
                if self._auto_capitalize_after_punctuation and not suppressed:
                    self._pending_auto_cap = True
                    rearmed_auto_cap = True
                    self._update_layer()
                if len(self._context_buffer) > 200:
                    self._context_buffer = self._context_buffer[-200:]

            # Mid-sentence punctuation — auto-space but no learning/capitalize
            elif char in (",", ";", ":"):
                suppressed = self._auto_space_after_punctuation and self._suppress_auto_space(
                    token_before, char
                )
                trailing = "" if suppressed else " "
                # Preserve the word before the comma in the sentence buffer
                # (_current_word includes the comma at this point, strip it)
                word_before = self._current_word[:-1]
                # A fragment still mirrors into _context_buffer, which
                # has to match the screen, but never into
                # _sentence_buffer, which is what the next boundary
                # hands to the learner.  See _take_lost_prefix.
                lost_prefix = self._take_lost_prefix()
                if word_before:
                    if not lost_prefix:
                        self._sentence_buffer += word_before + char + trailing
                    self._context_buffer += word_before + char + trailing
                else:
                    self._context_buffer += char + trailing
                self._current_word = ""
                self._word_typed_under_caps_lock = False
                if self._auto_space_after_punctuation and not suppressed:
                    self._send_text(" ")
                    self._auto_space_pending = True
                    self._learn_raw_token(token_before)
                    self._raw_token = ""
                elif suppressed:
                    self._defer_auto_space(token_before, char)
                if len(self._context_buffer) > 200:
                    self._context_buffer = self._context_buffer[-200:]

            # Word-internal boundaries that DON'T get an auto-space:
            # hyphen / slash / brackets / markdown-and-sigil punctuation /
            # maths / currency / legal marks.  Without this, typing
            # "word1-word2" left _current_word = "word1-word2", so clicking
            # a suggestion for "word2" failed the prefix-match in
            # pressPrediction and fell through to replace_text, which
            # backspaced "word1-" off the screen too.  Same bug for
            # "*hello", "@user", "#tag", "$var", `key=value` etc: the
            # leading punctuation got selected and overwritten by the pill.
            # Treat each as a word boundary for prediction purposes:
            # keep the character on screen (already sent above), reset
            # _current_word, and append the segment-plus-separator to
            # the buffers WITHOUT a trailing space (the user types
            # these with no following space, unlike commas).
            #
            # Stated as "everything that isn't a word character" rather than
            # as a list of separators, because the list form silently failed
            # open: the second symbol page added 18 glyphs (° × ÷ ± ≈ ≠ ≤ ≥
            # € £ ¥ ¢ § ¶ © ® ™ •) and every one of them fell through to be
            # appended to _current_word, so "cost€" became the prediction
            # prefix and the learned token.  Any glyph a future layer adds is
            # now covered by construction.  Kept as word characters: letters
            # and digits (including accented and non-Latin ones, which are
            # real word content), the apostrophe (contractions like "don't"
            # are single tokens) and the underscore (snake_case identifiers).
            elif not (char.isalnum() or char in ("'", "_")):
                word_before = self._current_word[:-1]
                # Mirror but do not learn a fragment, same rule as the
                # comma branch above.  See _take_lost_prefix.
                lost_prefix = self._take_lost_prefix()
                if word_before:
                    if not lost_prefix:
                        self._sentence_buffer += word_before + char
                    self._context_buffer += word_before + char
                else:
                    self._context_buffer += char
                self._current_word = ""
                self._word_typed_under_caps_lock = False
                if len(self._context_buffer) > 200:
                    self._context_buffer = self._context_buffer[-200:]

            # Which bar to show.  The token check comes first because the
            # two are mutually exclusive rather than ranked: part-way
            # through "owen@gm" or "555-123-", no English word is a
            # plausible suggestion, and the word engine is looking at a
            # `_current_word` of "gm" that it reset at the "@" anyway.
            #
            # The old gate here was `if char.isalpha()`, which is why a
            # digit used to blank the bar outright.
            if char.isalpha() or self._in_token_context():
                self._refresh_prediction_bar()
            else:
                self._clear_token_pills()
                self._predictions = []
                self.predictionsChanged.emit([])

        # Spend the pending auto-capital on the character just typed.
        # Guarded on `consumed_auto_cap` so a capital armed by *this*
        # keystroke (the punctuation branches above) survives for the
        # next one, which is the whole point of it, and on
        # `_CARRIES_AUTO_CAP` so a quote or bracket standing in front of
        # the sentence passes it along instead: `"` cannot carry a
        # capital, so spending one on it just threw it away and left
        # `hi. "hello"` lowercase.
        if consumed_auto_cap and not rearmed_auto_cap and char not in self._CARRIES_AUTO_CAP:
            self._pending_auto_cap = False
            self._update_layer()

        # Auto-release shift after one keypress (not caps lock, not a
        # right-click-locked hold).  Auto-capitalize needs no exception
        # here any more: it sets _pending_auto_cap, not _shift_active.
        if self._shift_active and not self._caps_lock_active and not self._shift_locked:
            self._shift_active = False
            self._synth.release_modifier("shift")
            self._update_layer()
            self.shiftActiveChanged.emit(self._shift_active)

        # Auto-release ctrl/alt/win after one keypress unless locked
        if self._ctrl_active and not self._ctrl_locked:
            self._synth.release_modifier("ctrl")
            self._ctrl_active = False
            self.ctrlActiveChanged.emit(self._ctrl_active)
        if self._alt_active and not self._alt_locked:
            self._synth.release_modifier("alt")
            self._alt_active = False
            self.altActiveChanged.emit(self._alt_active)
        if self._win_active and not self._win_locked:
            self._synth.release_modifier("win")
            self._win_active = False
            self.winActiveChanged.emit(self._win_active)

    def _release_edit_chord_modifiers(self) -> None:
        """Drop Ctrl/Alt/Win after a chord consumed inside an edit field.

        The fifth copy of the auto-release block, and it exists rather
        than being written inline for the reason this file gives for the
        other four: they are parallel blocks that have to stay in step,
        and a keystroke path that forgets one leaves a modifier the
        bridge believes is held.  A right-click lock still wins, exactly
        as it does everywhere else.
        """
        if self._ctrl_active and not self._ctrl_locked:
            self._synth.release_modifier("ctrl")
            self._ctrl_active = False
            self.ctrlActiveChanged.emit(self._ctrl_active)
        if self._alt_active and not self._alt_locked:
            self._synth.release_modifier("alt")
            self._alt_active = False
            self.altActiveChanged.emit(self._alt_active)
        if self._win_active and not self._win_locked:
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
        self._note_own_keystroke()
        self._play_click()
        # Any user-driven special key invalidates the auto-space window —
        # they pressed space themselves, or they're backspacing, or
        # navigating cursor; any subsequent punctuation should not undo
        # whatever space is on screen.
        self._auto_space_pending = False
        # A deferred auto-space is dropped rather than delivered here: the
        # user typing their own space, backspacing, or moving the caret
        # all settle the question themselves, and inserting ours on top
        # would be the one outcome none of them asked for.
        self._deferred_auto_space = ""
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
            "f1": "F1",
            "f2": "F2",
            "f3": "F3",
            "f4": "F4",
            "f5": "F5",
            "f6": "F6",
            "f7": "F7",
            "f8": "F8",
            "f9": "F9",
            "f10": "F10",
            "f11": "F11",
            "f12": "F12",
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
                self._current_word,
                self._context_buffer,
            )
            if correction and correction.lower() != self._current_word.lower():
                cased = self._match_case(self._current_word, correction)
                with self._without_held_modifiers():
                    if self._in_compat_mode():
                        # Compat mode: Shift+Left selection is unsafe under
                        # remote-forwarding pipelines and IDE interception.
                        # Use BackSpace × N + type instead — same end result,
                        # robust to per-event drops/duplicates.
                        for _ in range(len(self._current_word)):
                            self._synth.send_key("BackSpace")
                        self._send_text(cased + " ")
                    else:
                        self._replace_text(
                            len(self._current_word),
                            cased + " ",
                        )
                self._add_debug_log(f"Autocorrected: {self._current_word!r} → {cased!r}")
                _logger.info(
                    "Autocorrected (typed_len=%d, corrected_len=%d)",
                    len(self._current_word),
                    len(cased),
                )
                self._current_word = cased
                autocorrected = True

        if not autocorrected:
            # Game mode holds the key down briefly so a polling game catches
            # it (arrows, F-keys, space, Return are common in-game commands).
            self._send_key(xdotool_key, hold_seconds=self._key_hold_seconds())

        # Privacy mode — send the key but don't track context or learn
        if self._privacy_mode:
            pass
        elif key_name == "space":
            # Word completed - learn it and add to sentence
            lost_prefix = self._take_lost_prefix()
            if self._current_word and lost_prefix:
                # A context reset landed mid-word, so this is the tail of
                # a word whose head is still on screen.  It mirrors into
                # _context_buffer, because that has to match what was
                # typed, and reaches nothing that persists.  See
                # _take_lost_prefix for what learning it would cost.
                self._context_buffer += self._current_word + " "
                if len(self._context_buffer) > 200:
                    self._context_buffer = self._context_buffer[-200:]
            elif self._current_word:
                self._add_debug_log(f'Word completed: "{self._current_word}"')
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
                    self._add_debug_log(f'Learned capitalization: "{self._current_word}"')
                    _logger.info("Learned capitalization (len=%d)", len(self._current_word))
                self._sentence_buffer += self._current_word + " "
                self._context_buffer += self._current_word + " "
                # Learn bigrams/trigrams from the running sentence
                new_words = self._predictor.learn(self._sentence_buffer.strip())
                if new_words:
                    for nw in new_words:
                        self._add_debug_log(f'NEW WORD learned: "{nw}"')
                        _logger.info("New word learned (len=%d)", len(nw))
                # Keep context buffer bounded
                if len(self._context_buffer) > 200:
                    self._context_buffer = self._context_buffer[-200:]
            self._current_word = ""
            self._word_typed_under_caps_lock = False
            # The space ended the run, so the token is complete: this is
            # the moment to look at it for a saveable email / phone /
            # address, and the moment it stops being the current one.
            self._maybe_offer_snippet()
            # Space retires the run, so this is where a phone number, zip
            # or email the user typed by hand enters the token store.
            self._learn_raw_token(self._raw_token)
            self._raw_token = ""
            self._refresh_prediction_bar()
        elif key_name == "backspace":
            self._analytics.record_backspace()
            self._raw_token = self._raw_token[:-1]
            self._learned_raw_token = ""
            if self._current_word:
                self._current_word = self._current_word[:-1]
                if not self._current_word:
                    self._word_typed_under_caps_lock = False
                # Backspacing inside "owen@gmai" or "555-123-" must keep
                # offering the token bar.  `_current_word` is no help in
                # telling the two apart: it holds only the letters since
                # the last "@" or dot, so mid-address it looks exactly
                # like an ordinary partial word.
                self._refresh_prediction_bar()
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
                # Same choice as the branch above, and it belongs here
                # just as much: after any "-", ".", "/", "(" or "@" the
                # current word is empty, so "555-" then Backspace lands
                # in *this* branch, not that one.  Calling
                # `_update_predictions` unconditionally here is what
                # dropped the token bar on exactly the shape the comment
                # above claims to fix.
                self._refresh_prediction_bar()
        elif key_name == "return":
            # Sentence boundary - learn full sentence, then reset sentence buffer
            lost_prefix = self._take_lost_prefix()
            if self._current_word and not lost_prefix:
                self._add_debug_log(f'Word completed: "{self._current_word}"')
                self._analytics.record_word_completed(self._current_word)
                self._sentence_buffer += self._current_word
            if self._sentence_buffer.strip():
                new_words = self._predictor.learn(self._sentence_buffer.strip())
                if new_words:
                    for nw in new_words:
                        self._add_debug_log(f'NEW WORD learned: "{nw}"')
                        _logger.info("New word learned (len=%d)", len(nw))
            self._sentence_buffer = ""
            # Preserve context across lines (don't wipe)
            if self._current_word:
                self._context_buffer += self._current_word + " "
            if len(self._context_buffer) > 200:
                self._context_buffer = self._context_buffer[-200:]
            self._current_word = ""
            self._word_typed_under_caps_lock = False
            self._maybe_offer_snippet()
            self._learn_raw_token(self._raw_token)
            self._raw_token = ""
            self._refresh_prediction_bar()
        elif key_name == "tab":
            # Tab moves to the next field, so everything the buffers
            # describe is about a field the caret has left.  This is the
            # same failure the outside-click signal exists for, arriving
            # through the keyboard instead: left alone, a pill tapped
            # after tabbing inserts the tail of a word whose head is in
            # the previous field.
            #
            # The word in progress is deliberately **not** learned first.
            # Tab is also the accept-completion key in every IDE and the
            # shell, where `_current_word` is a *prefix* the app is about
            # to finish, so learning it would feed the model "hel" every
            # time the user completed "hello".
            #
            # A live snippet offer survives, for the reason the outside
            # click path documents: tabbing to the next field of a form
            # is the most likely thing to happen right after typing an
            # email address, and it must not close the Save button.
            self._reset_typing_context(keep_snippet_offer=True)
        elif key_name in _NAV_KEYS:
            # **A cursor-motion key invalidates the whole typing context,
            # not just the token run.**  Home, End, the arrows and the page
            # keys all put the caret somewhere we did not watch it go, so
            # `_current_word` and `_context_buffer` stop describing the
            # text in front of it.  This used to clear only `_raw_token`,
            # which is the same reasoning applied to half the state.
            #
            # It is worth more than better suggestions.  Those two buffers
            # are what the insert path measures against: a pill types only
            # the tail it believes is unseen, and falls back to selecting
            # `len(_current_word)` characters backwards and overwriting
            # them.  Both are arithmetic on a prefix that is no longer at
            # the caret, so a stale context does not merely suggest the
            # wrong word, it can eat text the user typed somewhere else.
            #
            # The pills go with it, so there is nothing stale left to tap.
            # That is what separates this from the mid-word reset the
            # caret *poll* deliberately avoids: that one clears the buffers
            # while leaving a live bar and a partial word on screen, which
            # is how a tap ends up inserting a whole word beside its own
            # prefix.  Here the bar is emptied in the same breath.
            #
            # Delete and Escape are deliberately NOT here.  Delete removes
            # the character *after* the caret, so the run before it, which
            # is the only thing these buffers describe, is untouched; and
            # Escape does not move the caret at all.  They keep the
            # narrower token-only clearing below.
            #
            # A live snippet offer survives, exactly as it does for Tab:
            # moving the caret within a form you are filling in must not
            # close the Save button on the address you just typed.
            #
            # Guarded so a held arrow does not fire a reset per repeat.
            # After the first one every field is already empty, so the
            # condition is self-limiting rather than a rate limit.
            if (
                self._current_word
                or self._context_buffer
                or self._sentence_buffer
                or self._predictions
                or self._raw_token
            ):
                self._reset_typing_context(keep_snippet_offer=True)
                self._update_layer()
        elif key_name in _TOKEN_BREAKING_KEYS:
            # Escape and Delete destroy text we can no longer account for,
            # so the run before the cursor is no longer whatever we last
            # watched being typed.  Clearing it costs one missed auto-space
            # decision and is the only honest answer; a stale token would
            # suppress a space somewhere unrelated.
            self._raw_token = ""
            # A capital owed to the word after a full stop does not
            # survive the caret moving somewhere else.  Space is
            # deliberately not in this set: the word after the auto-space
            # is exactly the one the capital was meant for.
            self._pending_auto_cap = False
            self._update_layer()
            # Clearing the run has to take its pills with it.  A token
            # pill continues `_raw_token`, so once that is gone the pill
            # on screen is a promise about a prefix the caret has left:
            # type "owen@", press Right, tap "gmail.com" and a whole
            # domain lands wherever the caret moved to.  The "is it on
            # the bar right now" safety check in `pressPrediction` cannot
            # catch this one, because this branch invalidates the prefix
            # without touching the bar.
            #
            # Guarded on there being pills to invalidate rather than
            # refreshing unconditionally: these keys auto-repeat, and a
            # held arrow must not fire a prediction query per repeat.
            if self._token_pill_words:
                self._refresh_prediction_bar()

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
        # A right-click-locked modifier stays held regardless of key type
        # (same as the nav-key exception, but the user opted in explicitly).
        keep_selection_modifiers = key_name in _NAV_KEYS
        if (
            self._shift_active
            and not self._caps_lock_active
            and not keep_selection_modifiers
            and not self._shift_locked
        ):
            self._shift_active = False
            self._synth.release_modifier("shift")
            self._update_layer()
            self.shiftActiveChanged.emit(self._shift_active)
        if self._ctrl_active and not keep_selection_modifiers and not self._ctrl_locked:
            self._synth.release_modifier("ctrl")
            self._ctrl_active = False
            self.ctrlActiveChanged.emit(self._ctrl_active)
        if self._alt_active and not self._alt_locked:
            self._synth.release_modifier("alt")
            self._alt_active = False
            self.altActiveChanged.emit(self._alt_active)
        if self._win_active and not self._win_locked:
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
            self._clear_lock("shift")  # a tap also clears a right-click lock
        self._update_layer()
        self.shiftActiveChanged.emit(self._shift_active)
        self._recase_visible_predictions()

    @Slot()
    def releaseShift(self) -> None:
        """Drop Shift if it is held; do nothing if it isn't.

        Idempotent, unlike ``toggleShift``, and that is the whole point.
        The compact layer switch has to drop a held Shift (the symbol pages
        carry no Shift key, so one carried in from the letters page could
        never be cleared, and the OS-held modifier would make "1" emit "!"
        while the keycap still read "1"). Expressing that as
        ``if (root.shiftOn) keyboard.toggleShift()`` made the correctness of
        a *release* depend on QML's mirror of the bridge state agreeing with
        the bridge: ``root.shiftOn`` starts as a binding but is imperatively
        reassigned by the shiftActiveChanged handler, which breaks the
        binding permanently, so from then on it is only as accurate as
        signal delivery. One missed emit and the toggle would turn Shift
        *on* on a page with no way to clear it. Asking for the end state
        instead of a flip cannot fail that way.

        Clears a right-click lock too: a locked Shift mismatches the keycaps
        just as loudly as a sticky one.
        """
        if not self._shift_active:
            return
        self._shift_active = False
        self._synth.release_modifier("shift")
        self._clear_lock("shift")
        self._update_layer()
        self.shiftActiveChanged.emit(False)
        self._recase_visible_predictions()

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
        self._recase_visible_predictions()

    def _recase_visible_predictions(self) -> None:
        """Re-emit the visible pills for a changed Shift / Caps Lock state.

        Both modifiers feed ``_display_cased``, so flipping either has to
        redraw whatever is already on screen or the bar contradicts the
        keycaps until the next keystroke.

        It re-queries the engine rather than re-casing the stored list in
        place, and that is the whole reason this is a method call and not
        a ``.upper()``: ``self._predictions`` holds the *displayed* form,
        so once "iPhone" has been shown as "IPHONE" the original casing is
        gone and no amount of ``.lower()`` gets it back.  The engine is the
        only source of truth for what a word looks like un-cased.

        No-op when the bar is empty, which also keeps a Shift tap from
        costing a prediction round trip during ordinary typing.

        Also a no-op on a structured-token bar, for two reasons.  Token
        pills deliberately bypass ``_display_cased`` (they insert
        verbatim, so there is no typed casing to mirror), which makes the
        recase meaningless there; and re-emitting would both replace the
        token bar with word predictions -- losing the completion the user
        was reaching for when they tapped Shift for a shifted symbol or a
        capital in an email local part -- and count a second
        ``record_prediction_offered`` for a bar that never went away.
        """
        if not self._predictions:
            return
        if self._in_token_context():
            return
        self._update_predictions()

    @Slot()
    def toggleCtrl(self) -> None:
        """Toggle ctrl modifier (sticky). Holds/releases at the OS level."""
        self._ctrl_active = not self._ctrl_active
        if self._ctrl_active:
            self._synth.hold_modifier("ctrl")
        else:
            self._synth.release_modifier("ctrl")
            self._clear_lock("ctrl")
        self.ctrlActiveChanged.emit(self._ctrl_active)

    @Slot()
    def toggleAlt(self) -> None:
        """Toggle alt modifier (sticky). Holds/releases at the OS level."""
        self._alt_active = not self._alt_active
        if self._alt_active:
            self._synth.hold_modifier("alt")
        else:
            self._synth.release_modifier("alt")
            self._clear_lock("alt")
        self.altActiveChanged.emit(self._alt_active)

    @Slot()
    def toggleWin(self) -> None:
        """Toggle Windows/Super modifier (sticky). Holds/releases at the OS level."""
        self._win_active = not self._win_active
        if self._win_active:
            self._synth.hold_modifier("win")
        else:
            self._synth.release_modifier("win")
            self._clear_lock("win")
        self.winActiveChanged.emit(self._win_active)

    @Slot()
    def resetModifiers(self) -> None:
        """Drop every held modifier and clear its UI highlight — a clean slate.

        Called from QML when the keyboard opens (``Component.onCompleted``)
        so we never start a session with a modifier left active from a
        prior run, a crash mid-chord, or an external grab. Belt-and-braces
        alongside the OS-level reset in ``__init__``: this one also clears
        the bridge's own flags and emits the change signals so the on-key
        highlights match reality.

        Resets Shift / Ctrl / Alt / Win (the four that hold at the OS
        level and can leave a click "stuck" under a modifier). Caps Lock
        is intentionally left as-is — it holds nothing at the OS level, so
        it can't get stuck, and it's a deliberate persistent toggle a user
        may have turned on on purpose.
        """
        # Release anything still held at the X server / compositor / kernel.
        self._synth.reset_modifier_state()
        if self._shift_active:
            self._shift_active = False
            self.shiftActiveChanged.emit(False)
        if self._ctrl_active:
            self._ctrl_active = False
            self.ctrlActiveChanged.emit(False)
        if self._alt_active:
            self._alt_active = False
            self.altActiveChanged.emit(False)
        if self._win_active:
            self._win_active = False
            self.winActiveChanged.emit(False)
        # Shift feeds the upper/lower layer; resync after clearing it.
        self._update_layer()

    # Lockable modifiers. The lock helpers derive attribute and signal
    # names from these (``_{name}_active`` / ``_{name}_locked`` /
    # ``{name}LockedChanged``), so the four modifiers stay DRY.
    _MODIFIERS = ("shift", "ctrl", "alt", "win")

    def _release_sticky_modifiers(self) -> None:
        """Drop every sticky (non-locked) modifier, OS hold and state alike.

        The per-keystroke auto-release, factored out.  A right-click lock
        is skipped, matching every other release site: the whole point of
        the lock is surviving a keystroke.

        ``_press_char`` and ``pressSpecialKey`` keep their own inline
        copies rather than calling this, and deliberately so — each has
        per-site conditions this can't express (``pressSpecialKey`` holds
        Shift/Ctrl across ``_NAV_KEYS`` so Shift+arrow selection persists,
        and the char path sequences the layer update against the chord
        branch).  This exists for the *verbatim insert* paths, which have
        no such exceptions: they consume the modifiers outright.
        """
        for name in self._MODIFIERS:
            if not getattr(self, f"_{name}_active") or getattr(self, f"_{name}_locked"):
                continue
            if name == "shift" and self._caps_lock_active:
                # Caps Lock deliberately pins a sticky Shift, the same
                # exception both inline copies carry.  Dropping it here
                # silently ended a Shift+drag the user had set up.
                continue
            setattr(self, f"_{name}_active", False)
            self._synth.release_modifier(name)
            getattr(self, f"{name}ActiveChanged").emit(False)
            if name == "shift":
                # Shift drives the upper/lower layer; resync the keycaps.
                self._update_layer()

    def _clear_lock(self, name: str) -> None:
        """Drop a right-click lock without touching the active/held state.

        Called from the sticky ``toggleX`` paths when they turn a
        modifier off: a plain tap on a locked modifier should also clear
        the lock so the user isn't stuck holding it.
        """
        attr = f"_{name}_locked"
        if getattr(self, attr):
            setattr(self, attr, False)
            getattr(self, f"{name}LockedChanged").emit(False)

    @Slot(str)
    def lockModifier(self, name: str) -> None:
        """Right-click a modifier → toggle a persistent 'lock' (held down).

        A locked modifier is held at the OS level and is exempt from the
        per-keystroke auto-release, so the user can fire several combos
        (Ctrl+C, Ctrl+V, ...) or hold Shift across many keys without
        re-tapping. Right-click again (or tap the key) to release. Caps
        Lock is already a persistent toggle, so it is not lockable here.
        """
        name = name.lower()
        if name not in self._MODIFIERS:
            return

        active_attr = f"_{name}_active"
        locked_attr = f"_{name}_locked"
        was_active = getattr(self, active_attr)
        new_locked = not getattr(self, locked_attr)
        # Locking implies held; unlocking releases entirely.
        new_active = new_locked

        setattr(self, locked_attr, new_locked)
        setattr(self, active_attr, new_active)

        # Only touch the OS hold when the held state actually flips, so a
        # right-click that locks an already-sticky-active modifier doesn't
        # re-send a redundant key-down (and unlocking a modifier that was
        # only sticky-active still releases it).
        if new_active and not was_active:
            self._synth.hold_modifier(name)
        elif not new_active and was_active:
            self._synth.release_modifier(name)

        if name == "shift":
            self._update_layer()

        getattr(self, f"{name}LockedChanged").emit(new_locked)
        if new_active != was_active:
            getattr(self, f"{name}ActiveChanged").emit(new_active)
            if name == "shift":
                # Right-click-locking Shift capitalises the pills exactly
                # like tapping it does; only the held state matters to
                # _display_cased, not how it came to be held.
                self._recase_visible_predictions()

    @Slot(str)
    def switchLayer(self, layer: str) -> None:
        """Switch keyboard layer (lower, upper, numbers, symbols)."""
        self._current_layer = layer
        self.currentLayerChanged.emit(self._current_layer)

    @Slot(str)
    def pressPrediction(self, word: str) -> None:
        """Called when user taps a prediction suggestion."""
        # Close the same 200 ms race _press_char guards against: if focus
        # just landed on a password field, flip privacy mode before this
        # selection persists anything to analytics or the model.
        self._check_password_field_sync()
        # Structured-token pills take a different insert path entirely:
        # what they display is not what they type, and the prefix they
        # continue is `_raw_token`, which the word path below never looks
        # at.  Dispatched on the map rather than on a mode flag so a pill
        # can only ever be inserted the way it was emitted.
        #
        # `_predictions` is in the condition as well, and that is the part
        # doing the safety work: there are a dozen sites in this file that
        # emit a pill row, so "clear the map at every one of them" would
        # be another set of parallel blocks to keep in sync, and missing
        # one would leave a stale entry tappable.  Requiring the pill to
        # be *on the bar right now* makes that impossible without
        # touching any of them.
        if word in self._token_pill_words and word in self._predictions:
            self._insert_token_pill(word)
            return
        _logger.info(
            "Prediction selected (word_len=%d, prefix_len=%d)",
            len(word),
            len(self._current_word),
        )

        # Track prediction usage: keystrokes saved = characters user didn't type + space.
        # Suppressed in privacy mode (analytics is persisted usage data); the
        # word is still inserted below regardless: the user tapped the pill,
        # so the text must reach the target app either way.
        if not self._privacy_mode:
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
        #
        # Drop the sticky modifiers *before* the insert, not after.  A pill
        # tap is a keystroke, so it consumes a one-shot Shift the same way
        # typing a character does — and with Shift the ordering is load-
        # bearing rather than cosmetic: `word` already carries the capital
        # _display_cased put there, so a Shift still held at the OS level
        # would uppercase the whole insert on top of it and "Hello" would
        # arrive as "HELLO".  Releasing first also leaves nothing for
        # _send_literal_text to drop and restore, so the ordinary path
        # costs no extra modifier round trip.
        self._release_sticky_modifiers()
        # A provisionally suppressed auto-space is settled by this tap: a
        # pill is a word, so the "42." that withheld the space ended a
        # sentence after all.  It can also owe a capital the pill was
        # never given, because nothing armed _pending_auto_cap while the
        # question was still open and _display_cased had nothing to act
        # on.  The space itself is typed inside the guard below, with the
        # rest of the insert.
        deferred_space, owes_capital = self._take_deferred_space(True)
        if owes_capital and word[:1].islower():
            word = word[0].upper() + word[1:]
        # The tap also spends any *armed* auto-capital.  That one is
        # already visible on the pill (_display_cased case 3), so only
        # the flag is left to clear; leaving it set would hand the same
        # capital to a later, unrelated character.
        self._consume_auto_cap()
        # The guard wraps every branch, including the two that never reach
        # send_text: a locked Alt turns the compat BackSpaces into
        # Alt+BackSpace (undo), and a locked Ctrl turns replace_text's
        # Shift+Left selection into Ctrl+Shift+Left, which eats whole
        # preceding words that the insert then overwrites.
        with self._without_held_modifiers():
            if deferred_space:
                self._send_text(deferred_space)
            if self._in_compat_mode() and self._current_word:
                for _ in range(len(self._current_word)):
                    self._synth.send_key("BackSpace")
                self._send_text(word + " ")
            elif word.startswith(self._current_word) and self._current_word:
                # Prediction extends what was typed (same case) — type the rest
                suffix = word[len(self._current_word) :] + " "
                self._send_text(suffix)
            elif not self._current_word:
                # Next-word prediction (nothing typed) — type the full word
                self._send_text(word + " ")
            else:
                # Casing differs (e.g. "iph"→"iPhone") or prefix mismatch —
                # select the typed letters and overwrite with the correct word.
                self._replace_text(len(self._current_word), word + " ")
        # All three paths append an auto-space; flag it so the next
        # keystroke (if it's punctuation) can elide it cleanly.
        self._auto_space_pending = True

        # Learn from selection — use context_buffer only, not the typed
        # fragment (_current_word) which is being *replaced* by the prediction.
        # Suppressed in privacy mode: this persists into the model.
        if not self._privacy_mode:
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
        # see `_word_typed_under_caps_lock`.  Suppressed in privacy mode,
        # same as the other persistence calls above.
        if (
            not self._privacy_mode
            and self._current_word
            and self._current_word != self._current_word.lower()
        ):
            allow_uppercase = not self._word_typed_under_caps_lock
            self._predictor.learn_capitalization(word, allow_uppercase=allow_uppercase)

        # Update context - add the completed word.  Suppressed in privacy
        # mode: the buffer feeds learn_from_selection() and predict() on
        # the next call, so a word accepted in a password field must not
        # linger in it either.  _current_word / caps-lock tracking still
        # clear unconditionally: those describe the in-progress typed
        # fragment that was just replaced, not learned content.
        if not self._privacy_mode:
            self._context_buffer += word + " "
            if len(self._context_buffer) > 100:
                self._context_buffer = self._context_buffer[-100:]
        self._current_word = ""
        self._raw_token = ""
        self._word_typed_under_caps_lock = False
        # The insert replaced the word outright, so a lost opening
        # is no longer owed to anything.  Travels with the flag above.
        self._word_prefix_lost = False

        # IMPORTANT: Clear predictions first, then get next-word predictions
        self._predictions = []
        self.predictionsChanged.emit([])

        # Get next-word predictions immediately
        # Context should end with space to signal "predict next word, not complete current"
        context_for_prediction = self._context_buffer
        _logger.info(
            "Context for next-word prediction (len=%d, ends_with_space=%s)",
            len(context_for_prediction),
            context_for_prediction.endswith(" "),
        )

        next_preds = self._predictor.predict(context_for_prediction, n=self._prediction_count)
        _logger.info("Next-word predictions (count=%d)", len(next_preds))

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
        """Full reset of typing state, for explicit user action only.

        The clear-context ring.  It **delegates** rather than clearing
        the same fields itself, because the hand-written copy is exactly
        the parallel-blocks drift this file warns about for sticky
        modifiers, and it had already drifted in both directions: this
        one cleared ``_learned_raw_token`` and :meth:`_reset_typing_context`
        did not, while that one cleared ``_pending_auto_cap`` and this
        did not -- so typing "hello." and then tapping the ring left a
        capital owed, and the next character in a context the user had
        just told the keyboard to forget came out uppercase.
        """
        self._reset_typing_context()

    # ------------------------------------------------------------------
    #  Off-screen "Tuck away" — see src/platform/x11_window.py and the
    #  "Tuck away" notes in docs/architecture/GOTCHAS.md.
    # ------------------------------------------------------------------

    @Slot(result=bool)
    def tuckSupported(self) -> bool:
        """Whether the off-screen 'Tuck away' affordance works this session.

        Only X11 has the on-screen clamp that tuck exists to escape (and the
        DOCK-type clamp-escape that does it). Off X11 the title-bar button is
        hidden — Windows/macOS aren't clamped and Wayland can't escape it.
        """
        try:
            from .platform.x11_window import is_x11

            return is_x11()
        except Exception:  # pragma: no cover - defensive
            return False

    @Slot("QVariant", bool)
    def setWindowDock(self, window: Any, dock: bool) -> None:
        """Promote a QML window to DOCK type (``dock=True``) or revert to NORMAL.

        DOCK is the one window type Mutter exempts from the on-screen clamp
        while keeping always-on-top + no-focus, so the keyboard can be parked
        off a screen edge. The cost (no taskbar entry, inert ``showMinimized``)
        is why QML only flips to DOCK *while parked* and reverts to NORMAL on
        return. No-op off X11. The window object is passed straight from QML;
        its native id is resolved here.
        """
        try:
            from .platform.x11_window import set_window_dock

            win_id = int(window.winId()) if window is not None else 0
            set_window_dock(win_id, bool(dock))
        except Exception:  # pragma: no cover - defensive
            _logger.debug("setWindowDock failed", exc_info=True)

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
        # A snippet tap is a keystroke, so it consumes the sticky
        # modifiers like any other — and a held Shift would otherwise
        # deliver the whole address in capitals.
        self._release_sticky_modifiers()
        # Settle a deferred auto-space (the snippet is prose following the
        # punctuation that withheld it) and spend any armed auto-capital.
        # The capital is deliberately *not* applied to the value: a
        # snippet is verbatim, and its own casing is the whole point.
        # The space rides along inside the literal insert so it is under
        # the same held-modifier guard the value is.
        deferred_space, _ = self._take_deferred_space(True)
        self._consume_auto_cap()
        self._send_literal_text(deferred_space + value)
        self._current_word = ""
        self._raw_token = ""
        self._word_typed_under_caps_lock = False
        # The insert replaced the word outright, so a lost opening
        # is no longer owed to anything.  Travels with the flag above.
        self._word_prefix_lost = False
        self._auto_space_pending = False
        self._predictions = []
        self.predictionsChanged.emit([])

    # ------------------------------------------------------------------
    #  Symbols & Emoji: the long tail behind the keyboard's symbol layer
    # ------------------------------------------------------------------

    @Slot(result="QVariantList")
    def getGlyphCategories(self) -> List[Dict[str, object]]:
        """Return the picker's catalogue as ``[{id, label, glyphs}, ...]``.

        Static data from :mod:`src.glyphs`, rebuilt into plain dicts and
        lists on every call because a NamedTuple does not survive the
        conversion into QML.  Cheap enough to leave uncached: QML asks
        once, when the window is first opened.
        """
        return glyph_categories()

    @Slot(result=int)
    def getRecentGlyphLimit(self) -> int:
        """How many recently-tapped glyphs the picker keeps.

        The list itself lives in the Qt settings layer, so QML owns the
        storage; the bound stays here so there is one number rather than
        one per side, the same split ``getSnippetLimit`` uses.
        """
        return MAX_RECENT

    @Slot(str, result=bool)
    def insertGlyph(self, glyph: str) -> bool:
        """Type *glyph* verbatim into the focused app.

        The same shape as :meth:`insertSnippet`, deliberately: a tapped
        glyph is a keystroke that happens to come from a window instead of
        a keycap, so it takes the sticky modifiers with it, settles a
        deferred auto-space as prose, spends an armed auto-capital, and
        goes out through ``_send_literal_text`` so a held modifier cannot
        rewrite it.  Not gated on privacy mode, for the same reason a
        snippet is not: the user tapped it, so it has to reach the app.

        Unlike a snippet this is one character, but the context handling
        is identical rather than merely similar, and that is the point:
        the two paths are siblings and the failure mode this codebase
        keeps hitting is a pair of nearly-identical blocks drifting apart.
        ``_context_buffer`` is left alone on both, which costs nothing
        here because ``NgramPredictor._tokenize`` keeps only ``[a-zA-Z']``
        and would discard the glyph anyway.

        Returns False when nothing was typed, so QML can say so rather
        than leave a tap that did nothing looking like one that missed.
        """
        if self._edit_mode_active or not glyph:
            return False
        self._release_sticky_modifiers()
        deferred_space, _ = self._take_deferred_space(True)
        self._consume_auto_cap()
        self._send_literal_text(deferred_space + glyph)
        self._current_word = ""
        self._raw_token = ""
        self._word_typed_under_caps_lock = False
        self._word_prefix_lost = False
        self._auto_space_pending = False
        self._predictions = []
        self.predictionsChanged.emit([])
        return True

    # --- Snippet auto-detection ----------------------------------------

    def _maybe_offer_snippet(self) -> None:
        """Offer to save a just-typed email / phone / address as a snippet.

        Called at word boundaries.  Everything about this is opt-outable
        and conservative, because the failure mode is offering to store
        the user's personal data when they didn't ask:

        - **Never in privacy mode.**  Both call sites already sit inside
          the non-privacy branch, and the guard here is belt-and-braces
          for any future caller: a password field must not produce an
          offer, let alone a saved copy.
        - **Never twice for the same value.**  A dismissed offer stays
          dismissed for the session, and an accepted one doesn't bounce
          straight back.
        - **Never for something already saved.**  If the value is in any
          snippet already, there is nothing to offer.
        - **Never on top of a live offer.**  One at a time, or the toast
          would flicker between candidates as the sentence grows.

        Two scan windows, and both are needed.  ``_raw_token`` is the run
        that just ended, and it is the only place a single-token shape
        survives intact: ``_current_word`` resets at "@" and at every dot,
        so by the time "owen@example.com" is finished the prediction
        engine's idea of the word is "com".  The recent tail of
        ``_context_buffer`` then covers the shapes that span whitespace --
        a street address is several words long, and a phone number is
        often written "(555) 123 4567".  The tail is bounded so an
        address typed a paragraph ago doesn't resurface at an unrelated
        word boundary.

        Nothing here is logged: it is all typed content (see the
        diagnostic-log rules in CLAUDE.md).
        """
        if not self._snippet_detection_enabled or self._privacy_mode:
            return
        if self._pending_snippet_offer is not None:
            return

        span = (self._context_buffer[-_SNIPPET_SCAN_TAIL:] + " " + self._current_word).strip()
        found = detect_snippet_candidate(self._raw_token) or detect_snippet_candidate(span)
        if found is None:
            return
        kind, value = found
        if value in self._offered_snippet_values:
            return
        if any(s.get("value", "") == value for s in self._snippets.get_all()):
            return

        self._pending_snippet_offer = (kind, value)
        # The ledger is written when the user *answers* the offer, not
        # here.  Recording it at raise time meant any withdrawal -- a
        # caret poll, an app switch, privacy mode -- burned the value for
        # the session, so the Save button never came back for an address
        # the user never got to see, let alone decline.
        _logger.info("Snippet offer raised (kind=%s, value_len=%d)", kind, len(value))
        self.snippetOffered.emit(kind, label_for_kind(kind), value)

    def _remember_offered(self, value: str) -> None:
        """Record *value* as answered, keeping the set bounded.

        Called from :meth:`acceptSnippetOffer` and
        :meth:`dismissSnippetOffer` only.  The ledger suppresses offers
        the user *saw and decided about*; an offer withdrawn by a poll
        was never answered, so it must stay offerable.

        This is the "don't nag" ledger, and it holds values the user
        typed, so it is not allowed to grow for the life of the session:
        an unbounded set of emails, phone numbers and addresses is both a
        slow leak and a pile of personal data sitting in memory for no
        reason.  A dict is used as an ordered set (insertion order is
        guaranteed) so the oldest entries fall off first.  Overflowing
        only costs a re-offer of something dismissed a very long time ago.
        """
        self._offered_snippet_values[value] = None
        while len(self._offered_snippet_values) > _MAX_REMEMBERED_OFFERS:
            self._offered_snippet_values.pop(next(iter(self._offered_snippet_values)))

    def _withdraw_snippet_offer(self) -> None:
        """Drop a live offer that is no longer about what the user is doing.

        Used when the typing context is torn down underneath it (an app
        switch, a context reset, entering privacy mode).  Emitting the
        withdrawal matters: the toast lives in QML on its own 8 s timer,
        so clearing only the Python side would leave a Save button on
        screen that silently does nothing.

        Deliberately does **not** write ``_offered_snippet_values``: the
        user never answered this one, so retyping the same address later
        must be able to raise it again.
        """
        if self._pending_snippet_offer is None:
            return
        self._pending_snippet_offer = None
        self.snippetOfferWithdrawn.emit()

    @Slot(result=bool)
    def acceptSnippetOffer(self) -> bool:
        """Save the offered value into Snippets.  True iff it was written.

        The value lands in the matching labelled slot when that slot is
        still empty (the seeded Name / Email / Phone / Address slots exist
        precisely to be filled).  When the slot already holds a *different*
        value, a new numbered slot is appended instead -- "Email 2" -- so
        a second address never overwrites the first.  Work and personal
        emails are both worth keeping, and silently replacing one the user
        had already curated would be the worse failure.

        **The return value is load-bearing.**  ``SnippetStore.add`` refuses
        past ``MAX_SNIPPETS`` and reports it by returning False, so at the
        cap this used to do nothing at all while QML flashed "Saved" -- the
        user walks away believing their email is stored.  QML now flashes
        the confirmation only on True.
        """
        offer = self._pending_snippet_offer
        self._pending_snippet_offer = None
        if offer is None:
            return False
        kind, value = offer
        self._remember_offered(value)
        label = label_for_kind(kind)
        existing = self._snippets.get_all()

        for index, snippet in enumerate(existing):
            if snippet.get("label", "").strip().lower() != label.lower():
                continue
            if snippet.get("value", "").strip():
                break  # slot taken — fall through to the numbered append
            if not self._snippets.set(index, snippet.get("label") or label, value):
                return False
            self.snippetsChanged.emit(self._snippets.get_all())
            _logger.info("Snippet offer accepted into existing slot (kind=%s)", kind)
            return True

        # Number from the count of slots already carrying this label, so
        # the second email is "Email 2" whether or not the user renamed
        # or reordered anything.
        taken = sum(
            1 for s in existing if s.get("label", "").strip().lower().startswith(label.lower())
        )
        if not self._snippets.add(f"{label} {taken + 1}" if taken else label, value):
            _logger.info("Snippet offer could not be saved: snippet list is full")
            return False
        self.snippetsChanged.emit(self._snippets.get_all())
        _logger.info("Snippet offer accepted into new slot (kind=%s)", kind)
        return True

    @Slot()
    def dismissSnippetOffer(self) -> None:
        """Drop the offer without saving.

        The value goes into ``_offered_snippet_values`` here, which is
        what makes it stop asking -- otherwise the next word boundary
        would re-detect the same address still sitting in the sentence
        buffer and put the toast straight back.  The toast's own 8 s
        timeout comes through this slot too, because ignoring an offer
        is an answer.  A *withdrawal* is not, and deliberately does not
        write the ledger: see :meth:`_withdraw_snippet_offer`.
        """
        offer = self._pending_snippet_offer
        self._pending_snippet_offer = None
        if offer is None:
            return
        self._remember_offered(offer[1])
        _logger.info("Snippet offer dismissed")

    @Slot(bool)
    def setSnippetDetection(self, enabled: bool) -> None:
        """Enable/disable offering to save detected emails/phones/addresses.

        Turning it off withdraws a live offer rather than merely dropping
        it: the toast is a QML window on its own timer, so clearing only
        the Python side left a Save button on screen that reported
        "Snippets are full" when tapped (``acceptSnippetOffer`` finds no
        offer and returns False, which is the same signal the cap uses).
        """
        self._snippet_detection_enabled = bool(enabled)
        if not self._snippet_detection_enabled:
            self._withdraw_snippet_offer()

    @Slot(bool)
    def setIntelligentSpacing(self, enabled: bool) -> None:
        """Enable/disable skipping the auto-space inside structured tokens."""
        self._intelligent_spacing = bool(enabled)

    @Slot(int, str, str, result=bool)
    def setSnippet(self, index: int, label: str, value: str) -> bool:
        """Replace the label + value of the snippet at *index*.

        The colour tag is deliberately not a parameter: the editor does
        not show one, and passing a default here would clear whatever the
        actions sheet had set (see ``SnippetStore.set``).

        Returns whether anything was written, so the editor can stop
        flashing "Saved" over a write the store refused: ``set`` rejects
        an out-of-range index, and the editor is reachable from the
        actions sheet, whose index a Data Backup import can invalidate
        while it is open.  Same reason ``acceptSnippetOffer`` and
        ``copySnippet`` report a bool.
        """
        if self._snippets.set(index, label, value):
            self.snippetsChanged.emit(self._snippets.get_all())
            return True
        return False

    @Slot(int, result=bool)
    def copySnippet(self, index: int) -> bool:
        """Put the snippet at *index* on the system clipboard.

        This is what a tile tap does, in preference to typing the value in.
        Typing it is one click, but it only lands correctly if the caret is
        already in the right field and the app does not intercept synthetic
        keystrokes (the whole reason Compatibility Mode exists); a long
        address also arrives one character at a time, and when it misses it
        misses silently, into whichever window happened to be focused. The
        clipboard has no focus race and no per-character path, at the cost
        of a paste.

        There is deliberately **no** "type it" control anywhere in the UI,
        not even in the actions sheet. ``insertSnippet`` still exists and
        still works, but nothing in QML calls it; it is kept as the
        reference for how a verbatim insert has to behave.

        Returns False without touching the clipboard for an out-of-range
        index and for an empty snippet: copying "nothing" would silently
        wipe whatever the user had already put there, which is the one way
        this can destroy something.

        Not gated on privacy mode, for the same reason inserting is not: the
        user asked for their own text. Nothing here is learned or logged.
        """
        value = self._snippets.get_value(index)
        if not value:
            return False
        # Imported here rather than at module scope.  QtGui dlopens the
        # host's libEGL / libGL when it is first imported, and this module
        # is imported by most of the Python suite, so an unconditional
        # import turns every one of those files into a pytest *collection
        # error* on a host without them instead of a skip.  That is the
        # same failure tests/test_qml_snippets.py wraps its own QtGui
        # import in a try/except to avoid.  One slot needs it; nothing
        # else in this module does.
        try:
            from PySide6.QtGui import QGuiApplication
        except ImportError:  # no GUI stack on this host
            return False
        # isinstance, not `is not None`: instance() is inherited from
        # QCoreApplication and hands back a plain QCoreApplication in a
        # non-GUI process, so a null check does not answer the question it
        # looks like it answers.  Falling through to clipboard() there
        # warns "Must construct a QGuiApplication" and returns null.
        if not isinstance(QGuiApplication.instance(), QGuiApplication):
            return False
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(value)
        return True

    @Slot(int, str)
    def setSnippetColor(self, index: int, color: str) -> None:
        """Tag the snippet at *index* with a colour (empty name clears it)."""
        if self._snippets.set_color(index, color):
            self.snippetsChanged.emit(self._snippets.get_all())

    @Slot(result="QStringList")
    def getSnippetColors(self) -> List[str]:
        """Return the allowed colour-tag names, untagged ("") first.

        QML builds its swatch row from this rather than from a list of
        its own, so a swatch can never offer a name the store would
        reject and silently drop back to untagged.
        """
        return list(SNIPPET_COLORS)

    @Slot(result=int)
    def getSnippetLimit(self) -> int:
        """Return the maximum number of snippets the store will hold.

        QML needs it to disable Add at the cap. ``SnippetStore.add``
        refuses past it by returning False and the list simply does not
        grow, which the old button could not tell from success: it opened
        the editor on "the last snippet" either way, which at the cap is
        an existing snippet the user never asked to edit.
        """
        return MAX_SNIPPETS

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

    def _get_shift_locked(self) -> bool:
        return self._shift_locked

    def _get_ctrl_locked(self) -> bool:
        return self._ctrl_locked

    def _get_alt_locked(self) -> bool:
        return self._alt_locked

    def _get_win_locked(self) -> bool:
        return self._win_locked

    def _get_current_layer(self) -> str:
        return self._current_layer

    def _get_synth_available(self) -> bool:
        return self._synth.is_available()

    def _get_password_detection_available(self) -> bool:
        return self._password_detection_available

    shiftActive = Property(bool, _get_shift_active, notify=shiftActiveChanged)
    capsLockActive = Property(bool, _get_caps_lock_active, notify=capsLockActiveChanged)
    ctrlActive = Property(bool, _get_ctrl_active, notify=ctrlActiveChanged)
    altActive = Property(bool, _get_alt_active, notify=altActiveChanged)
    winActive = Property(bool, _get_win_active, notify=winActiveChanged)
    shiftLocked = Property(bool, _get_shift_locked, notify=shiftLockedChanged)
    ctrlLocked = Property(bool, _get_ctrl_locked, notify=ctrlLockedChanged)
    altLocked = Property(bool, _get_alt_locked, notify=altLockedChanged)
    winLocked = Property(bool, _get_win_locked, notify=winLockedChanged)
    currentLayer = Property(str, _get_current_layer, notify=currentLayerChanged)
    synthAvailable = Property(bool, _get_synth_available, constant=True)
    passwordDetectionAvailable = Property(bool, _get_password_detection_available, constant=True)
    # Exposed so the Settings panel can show the running version next to
    # the auto-update controls — easiest sanity-check that an upgrade
    # actually landed.  Sourced from src/__version__.py at import time.
    appVersion = Property(str, lambda self: APP_VERSION, constant=True)

    # --- Internal ---

    def _update_layer(self) -> None:
        """Update the current layer based on shift/caps state."""
        if self._current_layer in ("numbers", "symbols"):
            return  # Don't change layer if user is on numbers/symbols
        new_layer = (
            "upper"
            if (self._shift_active or self._caps_lock_active or self._pending_auto_cap)
            else "lower"
        )
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

    # ------------------------------------------------------------------
    #  Structured-token predictions (numbers, phone numbers, emails)
    # ------------------------------------------------------------------

    def _in_token_context(self) -> bool:
        """Is the run before the cursor a structured token in progress?

        Two signals, and the asymmetry between them is deliberate:

        * **A digit anywhere.**  The word engine discards digits when it
          tokenises, so anything containing one is already outside what
          it can complete.
        * **An "@" that is not the first character.**  ``@owen`` is a
          mention and belongs to the word model, which can genuinely
          complete a name the user has typed before.  ``owen@`` is an
          address, and the only useful continuation is a domain.

        Two characters minimum: on a single one the prefix matches most
        of the store, so the bar would fill with unrelated numbers the
        moment the user typed any digit at all.
        """
        tok = self._raw_token
        if len(tok) < TokenPredictor.MIN_PREFIX_LEN:
            return False
        if any(ch.isdigit() for ch in tok):
            return True
        return tok.find("@") > 0

    def _token_suggestions(self) -> Tuple[str, List[str]]:
        """``(the typed run these pills continue, [pill text, ...])``.

        A domain pill reads ``gmail.com`` while the user has typed
        ``owen@``, so the pill text is not the whole of what ends up on
        screen.  Showing the whole address would be consistent with how
        word pills render, and worse: at 20-odd characters a pill the
        fitter would drop the row to two suggestions (it drops rather
        than elides, see the prediction-bar notes), and the local part is
        already on screen a centimetre away.  Full learned tokens *do*
        show whole, because there the pill is the answer rather than half
        of it.

        The typed run is returned alongside because the insert path needs
        it and cannot recover it from the pill: for a domain pill it is
        the tail of ``_raw_token`` after the "@", for a full token it is
        the whole run, and the two are indistinguishable from the pill
        alone.  It is also what makes the case check in
        ``_insert_token_pill`` possible, since the store matches
        case-insensitively, so the pill and the typed characters can
        disagree.

        **Every pill is strictly longer than the run it continues**, and
        ``_insert_token_pill`` relies on that: a pill equal to what is
        already typed would insert nothing and select the run to replace
        it with itself.  Enforced on both branches below, and by
        ``TokenPredictor.predict`` for its own half.
        """
        tok = self._raw_token
        at = tok.rfind("@")
        if at > 0:
            typed = tok[at + 1 :]
            return typed, [
                domain
                for domain in self._predictor.predict_email_domains(typed, self._prediction_count)
                if len(domain) > len(typed)
            ]
        return tok, [
            full
            for full in self._predictor.predict_tokens(tok, self._prediction_count)
            if len(full) > len(tok)
        ]

    def _emit_token_predictions(self) -> None:
        """Put structured-token pills on the bar (possibly none)."""
        typed, pills = self._token_suggestions()
        self._token_pill_words = set(pills)
        self._token_pill_typed = typed
        # Deliberately not routed through _display_cased, and the reason
        # is that these pills insert *verbatim* rather than continuing
        # the typed letters.  What arrives is exactly the stored string:
        # `_release_sticky_modifiers` drops a held Shift before the
        # insert, and the synthesizer already inverts its own shift for
        # an OS Caps Lock (see `_resolve_char_scancode`), so a stored
        # "gmail.com" types as "gmail.com" whatever the case state is.
        # Running _display_cased over it would show "GMAIL.COM" under
        # Caps Lock and then insert lowercase, which is precisely the
        # display/insert mismatch that function exists to prevent.
        self._predictions = list(pills)
        if self._predictions:
            self._analytics.record_prediction_offered()
        self.predictionsChanged.emit(self._predictions)

    def _clear_token_pills(self) -> None:
        """Forget which pills were tokens (the bar is showing words now)."""
        self._token_pill_words = set()
        self._token_pill_typed = ""

    def _is_live_token_pill(self, word: str) -> bool:
        """Is *word* a structured-token pill on the bar right now?

        The same two-part test ``_insert_token_pill`` dispatches on, and
        for the same reason: membership in the map alone can go stale,
        while "on the bar right now" cannot.

        Every word-model operation is gated on this.  A structured token
        is not a word, and the tables those operations write to are the
        ones a phone number must never reach: ``unigrams`` and
        ``preferred`` feed the word cloud, Top Words and the dashboard's
        boosted tags, ``capitalization`` is persisted, and all of them
        travel in the Data Backup archive.  The gate lives here as well
        as in QML because there are four call sites and QML is free to
        drift; the reverse operations (``dispreference``, ``blacklist``)
        would not work anyway, since ``TokenPredictor.predict`` never
        consults them.  Forgetting a learned token is ``forgetToken``,
        surfaced in the dashboard's Saved Numbers & Addresses.
        """
        return word in self._token_pill_words and word in self._predictions

    @Slot(str, result=bool)
    def isTokenPill(self, word: str) -> bool:
        """QML: is this pill a structured token rather than a word?

        Used to suppress the pill context menu, whose four actions are
        all word-model operations.  Deliberately not logged: the
        argument is typed content.
        """
        return self._is_live_token_pill(word)

    def _refresh_prediction_bar(self) -> None:
        """Repopulate the suggestion bar with whichever bar the caret is in.

        The word bar and the structured-token bar are mutually exclusive
        rather than ranked (see ``_in_token_context``), so *every* path
        that repopulates suggestions has to make the same choice.  That
        choice used to be written out inline at each site, and the two
        sites that forgot it were exactly the bugs this exists to
        prevent: backspacing over a separator ("555-" then Backspace)
        and tapping Shift mid-token both threw a live token bar away and
        replaced it with word predictions.

        Routing every emit through one method makes the choice
        structural instead of remembered, which is the same fix CLAUDE.md
        prescribes for the sticky-modifier release blocks.  A new insert
        or reset path gets it for free by calling this.
        """
        if self._in_token_context():
            self._emit_token_predictions()
        else:
            self._clear_token_pills()
            self._update_predictions()

    def _token_pill_trailing_space(self, completed: str) -> str:
        """Should a tap append a space after *completed*?

        Word pills always do, and most token pills should too: a house
        number, a zip or an apartment number sits mid-sentence with words
        after it, and on a mouse-driven keyboard a free space is a click
        saved.

        Emails and phone numbers are the exception, because they are
        field *values* rather than sentence content.  A login form that
        does not trim its input rejects "owen@gmail.com " with a
        validation error the user then has to notice, diagnose and
        backspace out of -- a far worse outcome than the one click a
        wanted space costs.  Nothing follows either shape in the same
        field anyway.
        """
        return "" if is_email(completed) or is_phone(completed) else " "

    def _insert_token_pill(self, word: str) -> None:
        """Type the continuation behind a structured-token pill.

        Mirrors ``pressPrediction``'s contract: the insert itself is
        never gated on privacy mode (the user tapped it, so the text must
        reach the app), while everything that *persists* is.

        It mirrors the other half of that contract too, and that half was
        missing once.  The token store matches case-insensitively, so the
        pill and the characters on screen can disagree: with Caps Lock on
        and ``OWEN@GM`` typed, the pill reads ``gmail.com`` while a
        suffix-only insert of ``ail.com`` leaves ``OWEN@GMail.com`` --
        neither what the pill promised nor what the user typed, and then
        learned back in that corrupted form.  So the suffix path is taken
        only when the pill *case-sensitively* continues what was typed;
        otherwise the typed prefix is selected and overwritten with the
        pill, exactly as ``pressPrediction`` falls back to
        ``replace_text``.  Either way what is left on screen is the text
        the pill displayed.
        """
        if word not in self._token_pill_words:
            return
        typed = self._token_pill_typed
        # The pill replaces the run it continues, so what is left on
        # screen is everything before that run plus the pill itself.
        head = self._raw_token[: len(self._raw_token) - len(typed)]
        completed = head + word
        # Case-sensitive on purpose: see the docstring.  A case-insensitive
        # match here is what produced "OWEN@GMail.com".
        suffix_only = word.startswith(typed)
        added = word[len(typed) :] if suffix_only else word
        trailing = self._token_pill_trailing_space(completed)
        _logger.info(
            "Token prediction selected (added_len=%d, token_len=%d, suffix_only=%s)",
            len(added),
            len(completed),
            suffix_only,
        )

        if not self._privacy_mode:
            rank = self._predictions.index(word) + 1 if word in self._predictions else 1
            # Deliberately not record_prediction_selected: that one feeds
            # the value into word_freq, and a phone number has no place
            # in the dashboard's Top Words or in the backup archive.
            #
            # The saving is what the pill spared the user typing, which is
            # the pill minus what they had already typed -- not the length
            # of the retype the replace path happens to send.
            self._analytics.record_token_prediction_selected(
                rank, keystrokes_saved=max(0, len(word) - len(typed)) + len(trailing)
            )

        # A pill tap is a keystroke, so it spends a one-shot Shift and any
        # armed auto-capital, both *before* the insert. A Shift still
        # held at the OS level would rewrite the whole string (see
        # _without_held_modifiers).
        self._release_sticky_modifiers()
        self._consume_auto_cap()
        # prose=False: the tap continues the token, which settles the
        # withheld space as "the punctuation was structural after all".
        # Delivering it would put a space inside the number being built.
        self._take_deferred_space(False)

        # The context manager wraps the whole insert, not just the text:
        # the replace path's Shift+Left selection is itself a chord, so a
        # standing hold would turn the selection into something else
        # entirely (see the verbatim-insert note in CLAUDE.md).
        with self._without_held_modifiers():
            if self._in_compat_mode() and typed:
                # Same rewiring pressPrediction does, for the same reason:
                # inside an IDE or a remote-desktop client both a
                # suffix-only insert and replace_text's Shift+Left
                # selection are unsafe, so the typed run is removed a
                # character at a time and the pill retyped whole.
                for _ in range(len(typed)):
                    self._synth.send_key("BackSpace")
                self._send_text(word + trailing)
            elif suffix_only:
                self._send_text(added + trailing)
            else:
                self._replace_text(len(typed), word + trailing)

        if not self._privacy_mode:
            # The buffer mirrors the screen, and all three branches above
            # leave the same thing there: the typed run replaced by the
            # pill.  The arithmetic has to be done on the *join* of the
            # two halves of that mirror, because the run spans them --
            # part of it sits in `_context_buffer` and part in
            # `_current_word`, which was never committed.  Doing it on
            # the buffer alone left "555-3-4567" recorded for a screen
            # reading "555-123-4567"; one Backspace then rehydrated that
            # corrupted tail, and the next tap replaced nine characters
            # of real text.
            screen = self._context_buffer + self._current_word
            self._context_buffer = screen[: len(screen) - len(typed)] + word + trailing
            if len(self._context_buffer) > 200:
                self._context_buffer = self._context_buffer[-200:]
        self._raw_token = completed[-_MAX_RAW_TOKEN_LEN:]
        self._current_word = ""
        self._word_typed_under_caps_lock = False
        # The insert replaced the word outright, so a lost opening
        # is no longer owed to anything.  Travels with the flag above.
        self._word_prefix_lost = False
        # The tap finished the token either way, so this is a word
        # boundary in the sense both of these care about: learn it, and
        # let an address the user just completed raise a Snippets offer,
        # exactly as typing the last character by hand would have.
        self._learn_raw_token(self._raw_token)
        self._maybe_offer_snippet()
        if trailing:
            self._raw_token = ""
            self._auto_space_pending = True

        self._refresh_prediction_bar()

    def _learn_raw_token(self, token: str) -> None:
        """Offer a completed run to the token store.

        Called wherever ``_raw_token`` is retired by something that means
        "the user finished typing that": space, Return, and the
        punctuation branches that end a token rather than sit inside one.
        Tab is deliberately not among them, for the same reason it does
        not learn ``_current_word`` -- it is the accept-completion key in
        every IDE and shell, so what precedes it is a prefix the app is
        about to finish, not a token the user typed.

        Nothing here is logged.  The argument is typed content, and the
        diagnostic log is attached to bug reports.
        """
        if self._privacy_mode or not token:
            return
        # One user action must not count as two sightings.  Tapping an
        # email or phone pill completes the run and learns it here, and
        # those two shapes deliberately withhold the trailing space, so
        # `_raw_token` is still holding the same string when the user's
        # own space retires it again.  Left unguarded that doubled the
        # count, which is the sort key in TokenPredictor.predict, so
        # pill-accepted tokens (including a built-in domain the user
        # never typed) outranked hand-typed ones at twice the rate.
        # Typing or backspacing clears the marker, so re-typing the same
        # number later still counts.
        if token == self._learned_raw_token:
            return
        self._learned_raw_token = token
        self._predictor.learn_token(token)

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
        self._current_word = self._context_buffer[last_ws + 1 :]
        self._context_buffer = self._context_buffer[: last_ws + 1] if last_ws >= 0 else ""
        if self._current_word and not self._privacy_mode:
            self._predictor.unlearn_word(self._current_word)

    def _display_cased(self, predictions: List[str]) -> List[str]:
        """Transform predictions to match the user's active case mode.

        Three cases, in this order — the earlier ones are more specific
        about what the user actually typed, so they win:

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
        3. Shift held (or an auto-capital pending after a full stop),
           nothing uppercase typed yet.  Capitalise the first
           letter only.  Both mean "the next character is a capital",
           and a tapped pill *is* the next thing typed, so a user who
           wants "Boston" out of a "bost" prefix can tap Shift and see
           the pill change rather than having to type the B themselves.
           It ranks below case 2 because an uppercase already in the
           prefix says something more specific about the word's shape
           (mid-word caps like "iP" → "iPhone") than a pending Shift does.

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
        if self._shift_active or self._pending_auto_cap:
            # 3. Shift held with nothing uppercase typed yet.  Shift is the
            #    "next character is a capital" signal, and the pill *is* the
            #    next thing that gets typed, so it capitalises too.  This is
            #    the same courtesy Caps Lock already got (case 1); without it
            #    the only way to capitalise a suggested word was to type its
            #    first letter shifted and wait for case 2 to mirror it, which
            #    defeats the point of tapping a pill.  Tapping one drops
            #    Shift the way any single keystroke does, so this is a
            #    one-word capital rather than a mode.
            return [w[:1].upper() + w[1:] for w in predictions]
        return predictions

    def _on_predictions_ready(self, predictions: List[str]) -> None:
        """Handle instant n-gram predictions."""
        # Words are landing, so any structured-token pill map is stale.
        # Clearing it here rather than at each call site is what makes
        # "a pill can only be inserted the way it was emitted" hold by
        # construction: every word emit in the app goes through here or
        # its refined twin.
        self._clear_token_pills()
        display = self._display_cased(predictions)
        self._predictions = display
        if display:
            self._analytics.record_prediction_offered()
        self.predictionsChanged.emit(display)

    def _on_predictions_refined(self, predictions: List[str]) -> None:
        """Handle LLM-refined predictions."""
        self._clear_token_pills()
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
            self._raw_token = ""
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
            except Exception as e:  # noqa: BLE001
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

        threading.Thread(target=_worker, name="alpha-osk-update-check", daemon=True).start()

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
                if written - last_emit[0] >= EMIT_EVERY or (total is not None and written >= total):
                    last_emit[0] = written
                    self.updateDownloadProgress.emit(
                        written,
                        total if total is not None else -1,
                    )

            try:
                ok, err = download_and_install(
                    info,
                    progress=_on_progress,
                    on_installer_launching=_on_installer_launching,
                )
            except Exception as e:  # noqa: BLE001
                _logger.error("Install raised: %s", e)
                self.updateInstallFailed.emit(str(e))
                return
            if not ok:
                # err is a short, step-specific message ("Download
                # failed", "Signature check failed", ...) so the banner
                # actually tells the user something useful.
                self.updateInstallFailed.emit(err or "Update failed")

        threading.Thread(
            target=_worker, args=(info,), name="alpha-osk-update-install", daemon=True
        ).start()

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
            getattr(self, "_click_timer", None),
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

        # Release any held modifier — sticky or right-click-locked — so
        # quitting with one "active" doesn't pin it at the OS level.
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
        self._shift_locked = self._ctrl_locked = False
        self._alt_locked = self._win_locked = False

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
            enabled,
            self._compat_auto_active,
        )

    def _in_compat_mode(self) -> bool:
        """Return whether compat mode should currently apply.

        Effective state: ``manual OR (auto_enabled AND auto_active)``.
        """
        return self._compat_manual or (self._compat_auto_enabled and self._compat_auto_active)

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
                "Compat auto-active: %s (hwnd=%s)",
                new_active,
                hwnd,
            )

    def _update_game_auto(self, hwnd: int) -> None:
        """Inspect ``hwnd`` and update ``_game_auto_active``.

        Called from ``_check_foreground_window`` on every foreground change.
        Cheap on a class/exe miss; fail-safe to False on any error.
        """
        if not hwnd:
            return
        try:
            new_active = _window_is_game(hwnd)
        except Exception:
            new_active = False
        if new_active != self._game_auto_active:
            self._game_auto_active = new_active
            _logger.debug(
                "Game key-hold auto-active: %s (hwnd=%s)",
                new_active,
                hwnd,
            )

    def _in_game_mode(self) -> bool:
        """Whether the foreground app is a known polling game."""
        return self._game_auto_active

    def _key_hold_seconds(self) -> float:
        """Per-key down-hold to use right now: nonzero only in game mode."""
        return _GAME_KEY_HOLD_SECONDS if self._game_auto_active else 0.0

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
        window_switched = hwnd != self._last_foreground_hwnd and self._last_foreground_hwnd != 0
        if window_switched:
            # Foreground window changed — user switched apps
            self._reset_typing_context()
            _logger.debug("Foreground window changed — predictions cleared")
        # Element-level focus: catches the caret moving between two controls
        # *inside the same window* (e.g. two text boxes on one web page),
        # which the window-handle check above is blind to. Windows/UIA only;
        # focused_element_token() returns None elsewhere, making this a no-op.
        # None means "couldn't read it" — we leave the baseline alone so a
        # transient UIA hiccup never wipes context.
        token = focused_element_token()
        if token is not None:
            if (
                not window_switched
                and self._last_focus_token is not None
                and token != self._last_focus_token
            ):
                # Same window, so the user is still in the form they were
                # filling in: a live snippet offer survives, exactly as it
                # does for the outside click.  Only `window_switched`
                # above takes the default and withdraws it.
                self._reset_typing_context(keep_snippet_offer=True)
                _logger.debug("Focused element changed — predictions cleared")
            self._last_focus_token = token
        elif window_switched:
            # Window changed but the element is unreadable; drop the stale
            # token so the next readable one re-seeds instead of mismatching.
            self._last_focus_token = None
        self._check_caret_moved(window_switched)
        # Update auto-detect for compat mode on every poll (cheap on
        # Windows — class lookup is a syscall, process check only fires
        # on class miss).  Auto-active toggling is debounced internally
        # so this isn't noisy.
        self._update_compat_auto(hwnd)
        # Same poll: flip the game key-hold path on/off based on whether the
        # foreground app is a known polling game (Age of Empires, ...).
        self._update_game_auto(hwnd)
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

    def _check_caret_moved(self, window_switched: bool) -> None:
        """Clear stale context when the user moves the caret themselves.

        ``focused_element_token`` answers "different control?".  This
        answers "different *place*?", which it cannot: clicking from one
        paragraph to another inside a single text box keeps the same
        element, and a web page often exposes one UIA element for the
        whole document, so two fields on it share a RuntimeId.  Either way
        the prediction context ends up describing text that is no longer
        beside the caret.

        Two guards keep this from firing on its own movement:

        **Typing moves the caret too**, so anything synthesized since the
        previous poll means the move was ours and is expected.  The flag
        is set in ``_send_key`` / ``_send_text`` / ``_replace_text``
        rather than at the keystroke entry points, because a tapped pill
        and a snippet both move the caret without going through
        ``_press_char``: set only there, this poll mistook our
        own insert for the user clicking elsewhere and tore down the
        context — and the next-word pills — the insert had just produced.

        **Only between words.**  A reset mid-word is the dangerous
        direction: it clears ``_current_word`` while the partial word is
        still on screen, and the next pill tap would then insert the whole
        word beside it ("backspacbackspaces"). Scrolling also drags the
        caret rectangle across the screen without the caret moving in the
        text, and that is the false positive most likely to land
        mid-word. Waiting for a word boundary costs the mid-word case,
        where a stale context matters least because the user is about to
        finish the word anyway.
        """
        token = caret_position_token()
        if token is None:
            # No caret published (most browsers, Electron) or not Windows.
            # Fail closed: forget the baseline so a later readable token
            # re-seeds rather than comparing against something ancient.
            self._last_caret_token = None
            self._keystroke_since_poll = False
            return

        typed_since_last_poll = self._keystroke_since_poll
        self._keystroke_since_poll = False
        previous = self._last_caret_token
        self._last_caret_token = token

        if window_switched or previous is None or token == previous:
            return
        if typed_since_last_poll or self._current_word:
            return
        # This branch is unreachable on an app switch (it returns above),
        # so the move was within one window and the offer stays -- see
        # _reset_typing_context.
        self._reset_typing_context(keep_snippet_offer=True)
        _logger.debug("Caret moved without typing — predictions cleared")

    def _check_external_click(self) -> None:
        """Clear stale context when the user clicks away from the keyboard.

        The signal of last resort, and the only one that covers clicking
        from one field to another inside a single window: ``GetForeground
        Window`` sees whole apps, the UIA element id is shared across a
        whole web document, and most of the browser and Electron world
        publishes no caret rectangle for ``_check_caret_moved`` to read.
        A click is observable in all of them.

        It is coarser than the signals it backs up: a click on a toolbar
        button or a scrollbar doesn't move the caret in the text, and
        resetting there costs the next-word prediction the user would
        have got.  That is the deliberate trade, because the failure it
        replaces is worse: context describing a field the caret has left
        produces pills that insert the wrong text into the field it is
        now in.

        **It fires mid-word too, and that is a reversal.**  This used to
        carry ``_check_caret_moved``'s "only between words" guard, which
        withheld the reset at the moment it was most needed: a partial
        word is exactly when the bar is full of completions for the field
        the caret has just left.  Measured, with "hel" typed and the
        caret clicked into another field, tapping the "hello" pill sent
        "lo " into that field.  Deferring to the next word boundary (the
        previous behaviour, held in an ``_external_click_pending`` flag)
        did not rescue it either: that boundary only arrives once the
        user finishes the word, by which point the wrong text is in.

        The guard was protecting the opposite case, a click that does
        *not* move the caret: clearing ``_current_word`` while its
        characters are still on screen desyncs the mirror, so a later
        pill completes against a prefix that is only part of what is
        there ("hel", then a typed "lo", then a tapped "look", gives
        "hellook").  That is real, and rarer than it looks -- mid-word
        the pointer is on the keyboard, and own-window clicks are
        filtered out by process id below, so reaching it means leaving
        the keyboard, clicking something caret-neutral in another app,
        and coming back mid-word.  Clicking into another field mid-word
        is ordinary.  Both directions corrupt text; this one corrupts it
        far less often.  **``_check_caret_moved`` keeps its own guard**,
        because scrolling drags the caret rectangle without moving the
        caret in the text, and that false positive lands mid-word
        constantly.

        Clicks on our own window (a key, a pill, the title bar, the
        snippets window) are filtered out inside
        :func:`external_click_detected` by process id, so the keyboard
        can never clear its own context by being typed on.

        A live snippet offer **survives** this reset, which is the one
        way it differs from the others.  The offer is about a value the
        user typed, not about where the caret is, and clicking the next
        field of the same form is the single most likely thing to happen
        right after typing an email address -- withdrawing here closed
        the Save button before the user could travel to it.  An app
        switch and privacy mode still withdraw it, through their own
        calls to :meth:`_reset_typing_context`.

        **Where the caret is readable, the two kinds of click are told
        apart rather than treated alike.**  A press on a toolbar button,
        a window title or empty chrome moves no caret, and resetting
        there costs the next-word suggestion for nothing.
        ``caret_position_token`` can answer that, but not at the instant
        the press is seen: the poll is up to ``_CLICK_POLL_MS`` behind
        it and the target app may not have handled the click yet, so an
        immediate read reports the old position for a click that is
        about to move the caret, which would suppress exactly the reset
        this signal exists to perform.  The decision is therefore
        settled over ``_CLICK_SETTLE_MS`` against a baseline from the
        *previous* poll (see ``_caret_before_click``), and any change
        inside that window is a move.

        **Unreadable means reset, and that is what keeps the original
        fix intact.**  Browsers and Electron apps publish no caret at
        all, which is the case the click signal was invented for, so
        there the behaviour is byte-identical to resetting on every
        press.  The refinement only ever applies where Windows will say
        plainly whether the caret moved.

        **A scrollbar is not among the clicks this rescues**, and the
        reason is the token rather than the timing: ``rcCaret`` is a
        client-relative rectangle, so scrolling the view drags it while
        the caret stays exactly where it was in the text, and the click
        reads as a move.  That is the same false positive
        ``_check_caret_moved`` carries its only-between-words guard for,
        and taking that guard here was tried and reversed for a stronger
        reason (see above), so a scroll keeps the coarse behaviour and
        resets.  Closing it needs a caret identity that survives a
        scroll, which Windows does not publish.
        """
        caret_now = caret_position_token()
        caret_before, self._caret_before_click = self._caret_before_click, caret_now
        ours = self._keystroke_since_click_poll
        self._keystroke_since_click_poll = False

        if external_click_detected():
            self._begin_click_settle(caret_before, caret_now)
        elif self._click_settle is not None:
            self._continue_click_settle(caret_now, ours)

    def _begin_click_settle(self, caret_before: Optional[str], caret_now: Optional[str]) -> None:
        """Decide, or start waiting to decide, about a fresh outside click."""
        if caret_before is None or caret_now is None:
            # No caret to reason about: fail closed, the pre-existing
            # behaviour and the one the browser case depends on.
            self._clear_click_settle()
            self._reset_after_outside_click("no caret published")
            return
        if caret_now != caret_before:
            # The app was quick: the caret has already moved.  Not
            # qualified on whether we typed in the same tick, unlike the
            # window below, and the asymmetry is deliberate: here the
            # caret has *already* changed by the time the click is seen,
            # so believing our own keystroke did it would mean keeping a
            # context that may belong to the field the click just left,
            # which is the direction that corrupts text.  Failing closed
            # costs one suggestion, on a tick where the user both typed
            # and clicked elsewhere within 50 ms.
            self._clear_click_settle()
            self._reset_after_outside_click("caret moved")
            return
        # Unchanged so far, which is not yet an answer.
        self._click_settle = (time.monotonic() + _CLICK_SETTLE_MS / 1000.0, caret_now)

    def _continue_click_settle(self, caret_now: Optional[str], ours: bool) -> None:
        """Watch a pending click until the caret moves or the window ends.

        ``ours`` says we synthesized something since the previous tick,
        which is the same guard ``_check_caret_moved`` carries and for
        the same reason: typing moves the caret, so reading our own
        insert as the user clicking away tears down the context -- and
        the next-word pills that insert had just produced -- 200 ms
        after producing them.  The settle only opens on a click that had
        *not* moved the caret, so a keystroke landing inside the window
        is evidence about our own insert and none at all about the
        click.  Re-baselining rather than concluding keeps whatever is
        left of the window watching for a move the app makes late.
        """
        assert self._click_settle is not None
        deadline, baseline = self._click_settle
        if ours and caret_now is not None:
            baseline = caret_now
            self._click_settle = (deadline, baseline)
        if caret_now is None or caret_now != baseline:
            self._clear_click_settle()
            self._reset_after_outside_click("caret moved")
            return
        if time.monotonic() >= deadline:
            self._clear_click_settle()
            _logger.debug("Click outside the keyboard left the caret alone: context kept")

    def _clear_click_settle(self) -> None:
        self._click_settle = None

    def _reset_after_outside_click(self, why: str) -> None:
        self._reset_typing_context(keep_snippet_offer=True)
        _logger.debug("Click outside the keyboard (%s): predictions cleared", why)

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
                    capture_output=True,
                    text=True,
                    timeout=0.5,
                    check=False,
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
                _logger.warning("Foreground-window detection failed (%s): %s", key, exc)
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

    def _reset_typing_context(self, *, keep_snippet_offer: bool = False) -> None:
        """Drop all in-progress typing state because the context went stale.

        Shared by the app-switch and focused-element-switch paths in
        ``_check_foreground_window``: once the caret moves to a different
        window or control, the partial word / sentence / context buffers
        describe text that's no longer where the caret is, so predictions
        built from them would be wrong.  Clears the same fields as
        ``_enter_privacy_mode`` (which scrubs for the different reason of
        keeping sensitive input out of the model).

        ``keep_snippet_offer`` belongs to the three *within-window*
        signals -- the outside click, the focused-element change and the
        caret move -- where the caret moved but the user did not leave
        the form they are filling in.  All three had to take it, not just
        the click: they poll on their own timers, so a click that kept
        the offer was followed 250 ms later by a caret or element poll
        that withdrew it, and the Save button vanished anyway.  An app
        switch and privacy mode take the default and still withdraw.
        """
        self._predictions = []
        # A reset that lands mid-word leaves the word's opening on screen
        # with nothing tracking it, so what the user types next is a
        # tail.  See _take_lost_prefix for what learning it would cost.
        self._word_prefix_lost = bool(self._current_word)
        self._current_word = ""
        self._raw_token = ""
        # Goes with _raw_token: it names the run last handed to the token
        # store, and that run is gone.  Left behind, it was the one piece
        # of reset state `resetContext` cleared and this did not.
        self._learned_raw_token = ""
        self._clear_token_pills()
        # A pending outside-click settle was reasoning about a caret in
        # the context that just went away, so it cannot outlive it: on
        # an app switch the next click tick would compare the new app's
        # caret against the old app's baseline and "detect" a move,
        # which passes keep_snippet_offer=True and quietly re-opens a
        # decision the switch had already made.  The baseline goes with
        # it; the cost is that the next click fails closed, and with the
        # context already cleared there is nothing left for it to clear.
        self._clear_click_settle()
        self._caret_before_click = None
        self._pending_auto_cap = False
        # Dropped, not delivered: the punctuation that withheld this
        # space is no longer where the caret is, so typing one now would
        # put it in whatever field the user just moved to.
        self._deferred_auto_space = ""
        self._word_typed_under_caps_lock = False
        self._sentence_buffer = ""
        self._context_buffer = ""
        # A live snippet offer is about the text these buffers held, so it
        # goes with them.  This is also what stops an offer raised just
        # before focus landed on a password field from sitting there
        # savable: privacy mode has to mean "stop doing this", not "stop
        # starting new ones".  The one exception is the outside-click
        # path; see _check_external_click for why a click within the same
        # app is not a reason to close the Save button.
        if not keep_snippet_offer:
            self._withdraw_snippet_offer()
        self.predictionsChanged.emit([])

    def _enter_privacy_mode(self) -> None:
        """Scrub all buffers to prevent sensitive data from leaking to the model."""
        self._reset_typing_context()

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
        if self._is_live_token_pill(word):
            return
        self._predictor.blacklist_word(word)
        # Refresh predictions to remove it immediately
        self._predictions = [w for w in self._predictions if w.lower() != word.lower()]
        self.predictionsChanged.emit(self._predictions)
        self._add_debug_log(f"Blacklisted: {word}")

    @Slot(str)
    def markBadSuggestion(self, word: str) -> None:
        """Downweight a word in future predictions."""
        if self._is_live_token_pill(word):
            return
        self._predictor.mark_bad_suggestion(word)
        self._add_debug_log(f"Marked bad: {word}")

    @Slot(str)
    def markGoodSuggestion(self, word: str) -> None:
        """Boost a word in future predictions. Clears any prior dispreference
        then bumps the unigram count by the same +5 used for prediction-click
        reinforcement and records the boost so the dashboard can show it
        and the user can undo it later.
        """
        if self._is_live_token_pill(word):
            return
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

    @Slot(result="QVariantList")
    def getLearnedTokens(self) -> List[Dict[str, object]]:
        """Every structured token the store has learned, most-typed first.

        The token store is the one learned surface with no other way to
        inspect it.  Learned *words* show up in the word cloud, the flow
        graph and Top Words; a phone number or an email address
        deliberately reaches none of those (see the analytics note in
        ``_insert_token_pill``), so without this slot the only answer to
        "what has it remembered about me" was Clear Learned Data, which
        throws away the vocabulary too.

        That matters more here than for words.  The admission rule is a
        shape test, not a judgement about sensitivity: it accepts any
        short alphanumeric run with a digit in it, which is also the
        shape of a password typed into a field where auto-detection
        failed open.  Keeping the permissive rule is a deliberate call
        for recall, and it is only defensible while the user can see the
        result and drop individual entries.

        Nothing is logged here.  Every value is typed content.
        """
        tokens = self._predictor._ngram.tokens.tokens
        return [
            {"token": token, "count": count}
            for token, count in sorted(tokens.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    @Slot(str, result=bool)
    def forgetToken(self, token: str) -> bool:
        """Drop one learned structured token.  False if it wasn't there.

        Deliberately not logged, unlike its ``unblacklistWord`` siblings:
        those take a dictionary word, this takes whatever the user
        typed, and the diagnostic log is what gets attached to bug
        reports.
        """
        return self._predictor._ngram.tokens.forget(token)

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
        # Close the same 200 ms race _press_char guards against, mirroring
        # pressPrediction.
        self._check_password_field_sync()
        # A structured token is not a word, and this path is word-shaped
        # throughout: it replaces `len(_current_word)` characters, which
        # is only part of the typed run (leaving "555-555-123-9999" on
        # screen), always appends the space the phone/email shapes
        # deliberately withhold, and persists the result into
        # `capitalization`.  Editing a token is `forgetToken` plus
        # retyping it; the pill's context menu is suppressed for the same
        # reason (see _is_live_token_pill).
        if self._is_live_token_pill(original):
            return
        edited = self._sanitize_edit(edited)
        if not edited:
            return

        # Learn the preferred capitalization. Suppressed in privacy mode:
        # this persists into the model, same as pressPrediction's guards.
        if not self._privacy_mode:
            self._predictor.set_capitalization(edited, edited)

        # Insert the edited word (same as pressPrediction but with edited
        # text). Not gated: the user explicitly typed and saved this
        # correction, so it must still reach the target app.
        self._replace_text(len(self._current_word), edited + " ")

        # Update context. Suppressed in privacy mode: feeds predict() /
        # learn_from_selection() on the next call, same reasoning as
        # pressPrediction. _current_word / caps-lock tracking still clear
        # unconditionally.
        if not self._privacy_mode:
            self._context_buffer += edited + " "
            if len(self._context_buffer) > 100:
                self._context_buffer = self._context_buffer[-100:]
        self._current_word = ""
        self._raw_token = ""
        self._word_typed_under_caps_lock = False
        # The insert replaced the word outright, so a lost opening
        # is no longer owed to anything.  Travels with the flag above.
        self._word_prefix_lost = False

        # Refresh predictions
        self._predictions = []
        self.predictionsChanged.emit([])
        next_preds = self._predictor.predict(self._context_buffer, n=self._prediction_count)
        display = self._display_cased(next_preds)
        self._predictions = display
        self.predictionsChanged.emit(display)

        self._add_debug_log(f"Edited prediction: {original} → {edited}")
        _logger.info(
            "Prediction edited (original_len=%d, edited_len=%d)",
            len(original),
            len(edited),
        )

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
        self._apply_layout_key_positions()

    def _apply_layout_key_positions(self) -> None:
        """Point the fuzzy recogniser's spatial model at the active layout.

        Until this existed the recogniser fell back to its hardcoded
        ``QWERTY_POSITIONS`` unconditionally, and nothing anywhere
        passed it anything else, so a Dvorak or Colemak user was being
        autocorrected against a QWERTY board: "hwllo" resolved to
        "hello" on the strength of w neighbouring e, which on their
        layout it does not.  That was a plain bug rather than a
        multilingual one, and AZERTY is what made it worth fixing now.

        Derivation is layout-driven rather than hardcoded per layout so
        a new ``data/layouts/*.json`` keeps needing no code, which is
        the property the whole layout system is built around.
        """
        rows = self._layouts.get(self._current_layout, {}).get("rows", [])
        if not rows:
            return
        positions = positions_from_layout(rows)
        if positions:
            self._predictor.set_key_positions(positions)

    @Slot(result=list)
    def getAvailableLayouts(self) -> list:
        """Return list of {id, name} dicts for available layouts."""
        return [{"id": lid, "name": data.get("name", lid)} for lid, data in self._layouts.items()]

    @Slot(result=str)
    def getCurrentLayout(self) -> str:
        """Return current layout id."""
        return self._current_layout

    @Slot(str)
    def setLayout(self, layout_id: str) -> None:
        """Switch to a different keyboard layout."""
        if layout_id in self._layouts and layout_id != self._current_layout:
            self._current_layout = layout_id
            self._apply_layout_key_positions()
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
            key=lambda kv: kv[1],
            reverse=True,
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
            "successors": [{"word": w, "count": int(c)} for w, c in successors],
            "predecessors": [{"word": w, "count": int(c)} for w, c in predecessors],
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
        return getattr(self, "_prediction_count", 5)

    predictions = Property(list, _get_predictions, notify=predictionsChanged)
    llmEnabled = Property(bool, _get_llm_enabled, notify=llmEnabledChanged)
    llmAvailable = Property(bool, _get_llm_available, notify=llmAvailableChanged)
    predictionCount = Property(int, _get_prediction_count, notify=predictionCountChanged)
