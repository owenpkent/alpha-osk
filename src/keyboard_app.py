"""
Keyboard Application - QML engine setup and window configuration.

Launches the on-screen keyboard as a PySide6/QML application with
proper window flags for an OSK (stays on top, doesn't steal focus).

Cross-Platform Behaviour
------------------------
- **Linux (X11)**: Sets ``QT_QPA_PLATFORM=xcb`` and uses Qt window flags
  ``WindowStaysOnTopHint | FramelessWindowHint | WindowDoesNotAcceptFocus``
  to stay above other windows without stealing keyboard focus.

- **Windows**: Uses the same Qt flags.  When the binary is EV code-signed
  with a ``UIAccess="true"`` manifest, the keyboard can also appear above
  UAC prompts and elevated windows.  Additionally, on Windows we call
  ``SetWindowLong`` to apply ``WS_EX_NOACTIVATE`` (focus-suppression).
  ``WS_EX_TOPMOST`` is deliberately *not* written into the style word:
  always-on-top is applied separately with
  ``SetWindowPos(HWND_TOPMOST)``, because writing the style bit directly
  leaves the Z-order band untouched and the window reads as topmost
  without behaving like it.  ``WS_EX_TOOLWINDOW`` is actively cleared
  (Qt adds it on its own once these flags reach an already-shown window)
  and ``WS_EX_APPWINDOW`` is set, so the keyboard keeps a normal taskbar
  entry for the standard minimize button to drop into; the accepted
  trade-off is that the OSK also shows up in Alt+Tab.  ``Qt.Tool`` is
  not applied on Windows either, for the same taskbar reason.

- **macOS**: Same Qt flags (``WindowDoesNotAcceptFocus`` maps to
  ``-canBecomeKeyWindow`` returning NO).  On top of that, pyobjc is
  used to set the NSWindow level to ``NSFloatingWindowLevel``,
  collection behavior to ``CanJoinAllSpaces | Transient | FullScreenAuxiliary``
  (so the keyboard follows the user across Spaces and floats over
  fullscreen apps), and ``hidesOnDeactivate=NO`` so the window stays
  visible when another app gains focus — without that, clicking into
  a text editor would make the keyboard vanish on the next event.

See Also
--------
- ``src/platform/``: OS-specific key synthesis and window-styling
  backends (``windows_window.py``, ``macos_window.py``, ``x11_window.py``).
- ``docs/architecture/PLATFORM_ARCHITECTURE.md`` — design rationale.
- ``docs/build/WINDOWS.md`` — Windows build / signing guide.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import cast

from PySide6.QtCore import QSettings, QSharedMemory, Qt, QUrl
from PySide6.QtGui import QIcon, QWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .__version__ import __version__
from .keyboard_bridge import KeyboardBridge
from .platform import (
    CURRENT_PLATFORM,
    LOG_FILENAME,
    get_config_dir,
    get_platform_info,
    macos_window,
    windows_window,
)

_logger = logging.getLogger("KeyboardApp")

# Module-level holder for the single-instance lock.  QSharedMemory's
# segment is released when this object is destroyed, so it must outlive
# the QApplication for the lock to mean anything.
_SINGLETON_LOCK: QSharedMemory | None = None


def _acquire_singleton_or_surface() -> bool:
    """Take the single-instance lock; surface the running instance otherwise.

    Returns True if this process is the (sole) running instance and
    should continue starting up.  Returns False if another Alpha-OSK is
    already running — in that case we attempt to un-minimise / focus the
    existing window on Windows so the user gets some visible response.

    The lock is a QSharedMemory segment keyed on a stable name.  On
    Windows the OS reclaims the segment automatically when the owning
    process exits, so a crashed prior instance never strands the lock.
    On Linux/X11 the SysV segment can persist after a crash; we recover
    by attach-then-detach which forces cleanup if no one holds it, then
    retry create.
    """
    global _SINGLETON_LOCK
    lock = QSharedMemory("alpha-osk-singleton-v1")

    if lock.create(1):
        _SINGLETON_LOCK = lock
        return True

    # On POSIX a crashed process leaves the segment behind.  attach()
    # binds us to it; detach() will free it if we were the last
    # reference (i.e. the previous owner is gone).  Then retry.
    if lock.error() == QSharedMemory.SharedMemoryError.AlreadyExists:
        if lock.attach():
            lock.detach()
        if lock.create(1):
            _SINGLETON_LOCK = lock
            return True

    # A real duplicate is running.  Try to bring its window forward
    # (Windows-only — there's no portable way to do this on Linux
    # without a DBus IPC layer we don't have yet).
    _logger.info("Another Alpha-OSK is already running; surfacing it.")
    if sys.platform == "win32":
        windows_window.surface_existing_instance()
    return False


def _project_root() -> Path:
    """Resolve the project root (handles both dev and PyInstaller frozen)."""
    here = Path(__file__).resolve().parent
    return here.parent


def qml_path() -> Path:
    """Resolve the path to Main.qml relative to this file."""
    return _project_root() / "qml" / "Main.qml"


def _icon_path() -> Path | None:
    """Find the app icon for the system tray.

    The native-format icon list is chosen per platform:

    - macOS:   ``.icns`` (multi-resolution Apple format)
    - Windows: ``.ico`` (multi-resolution Win32 format)
    - Linux:   the PNG directly — neither .ico nor .icns is native

    Then a PNG fallback so a stripped-down dev checkout still gets
    *some* icon.  Without per-platform gating, every platform would
    pick whichever native asset happens to sit earliest in the
    candidate list — the macOS build was loading
    ``build/windows/alpha-osk.ico`` because the iteration order was
    Windows-first.
    """
    root = _project_root()
    exe_dir = Path(sys.executable).parent

    native_candidates: list[Path]
    if CURRENT_PLATFORM == "macos":
        native_candidates = [
            root / "build" / "macos" / "alpha-osk.icns",
            exe_dir / "alpha-osk.icns",
        ]
    elif CURRENT_PLATFORM == "windows":
        native_candidates = [
            root / "build" / "windows" / "alpha-osk.ico",
            root / "alpha-osk.ico",
            exe_dir / "alpha-osk.ico",
        ]
    else:
        # Linux + unsupported — PNG only
        native_candidates = []

    candidates = native_candidates + [
        root / "assets" / "logo-1024.png",
        exe_dir / "_internal" / "assets" / "logo-1024.png",
        exe_dir / "assets" / "logo-1024.png",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _setup_platform_env() -> None:
    """
    Apply platform-specific environment variables before QGuiApplication
    is created.

    - **Linux**: Force the ``xcb`` (X11) Qt platform adapter so the
      keyboard works correctly with ``xdotool``.  Wayland users who
      prefer ``ydotool`` can override with ``QT_QPA_PLATFORM=wayland``.
    - **Windows**: No environment overrides needed — the ``windows``
      platform adapter is used automatically.
    - **macOS**: No environment overrides needed — the ``cocoa``
      platform adapter is used automatically.  NSWindow tuning
      happens in ``src.platform.macos_window.apply_window_flags`` after
      the QML root window is created.
    """
    if CURRENT_PLATFORM == "linux":
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    # Use Basic style so ScrollBar/Switch customization works without warnings
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")


def _apply_window_flags(root: QWindow) -> None:
    """
    Apply OS-specific window flags to make the keyboard behave as a
    proper on-screen keyboard:

    - Stays on top of all other windows.
    - Never steals keyboard focus from the user's active application.
    - Frameless (Alpha-OSK draws its own title bar in QML).
    - Has a normal taskbar entry so the standard Windows minimize
      button can drop the OSK to the taskbar and clicking the
      taskbar entry restores it.  (Earlier builds used ``Qt.Tool``
      and ``WS_EX_TOOLWINDOW`` to suppress the taskbar entry, which
      meant minimize had to ``hide()`` and the only way back was the
      tray icon — easy to miss.  Trade-off: the OSK now appears in
      Alt+Tab.  Acceptable since ``WS_EX_NOACTIVATE`` still prevents
      focus theft on every click.)
    """
    # Qt flags — work on all platforms.  WindowDoesNotAcceptFocus
    # is the Linux/Wayland equivalent of WS_EX_NOACTIVATE; on
    # Windows the Win32 path below handles focus suppression.
    base_flags = (
        Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowDoesNotAcceptFocus
    )

    # macOS needs Qt.Tool ON TOP of the above.  On macOS, Qt.Tool
    # makes the QML root a native NSPanel rather than a plain
    # NSWindow.  Only NSPanel honors the "non-activating" semantics
    # we need so clicks on the OSK don't pull Alpha-OSK to the
    # foreground.  Qt.WindowDoesNotAcceptFocus alone maps to
    # ``canBecomeKeyWindow: NO`` — that stops keyboard input but
    # does NOT stop app-level activation on mouse-down.  Without
    # Qt.Tool, clicking a key activates the OSK as the foreground
    # app and ``CGEventPost`` then delivers the synthesised
    # keystroke to Alpha-OSK itself rather than to the editor
    # behind us.  Confirmed in dev logs as
    # ``POST send_text('h') → frontmost=Python``.
    #
    # On Windows and Linux, Qt.Tool *also* removes the taskbar entry
    # (Windows) and changes WM hinting in ways the bridge / minimise
    # / tray icons don't expect — see the WS_EX_TOOLWINDOW note in
    # ``src/platform/windows_window.py::apply_extended_styles`` for the
    # full rationale.  So we only add Qt.Tool when we're actually on
    # macOS, where the Accessory activation policy has already
    # eliminated the taskbar/Dock entry anyway.
    if CURRENT_PLATFORM == "macos":
        base_flags = base_flags | Qt.WindowType.Tool

    root.setFlags(base_flags)

    # Windows-specific: apply WS_EX_NOACTIVATE via Win32 API
    if CURRENT_PLATFORM == "windows":
        windows_window.apply_extended_styles(root, taskbar_button=True)
    elif CURRENT_PLATFORM == "macos":
        macos_window.apply_window_flags(root)


def _wire_floating_windows(root: QWindow) -> None:
    """Apply OSK focus-suppression to the floating Snippets and Symbols windows.

    Both are separate top-level ``Window``s declared in Main.qml
    (objectNames ``snippetsWindow`` and ``symbolsWindow``) so they can
    float anywhere on the desktop, outside the keyboard. Like the main
    window they must never steal focus from the app the user is typing
    into, so on Windows each needs ``WS_EX_NOACTIVATE`` applied via Win32,
    since the Qt ``WindowDoesNotAcceptFocus`` flag alone doesn't stop
    click-activation there. The native handle only exists once a window
    has been shown, so we (re)apply on every visibility change rather
    than once at startup. A missing window is skipped rather than
    aborting the rest: one unstyled picker is a smaller problem than
    both of them going unwired.

    No-op on non-Windows (the Qt flag is sufficient on X11/Wayland, and
    macOS uses the Accessory activation policy applied app-wide). Silent
    on any failure — the feature still works, it just might briefly take
    focus when first shown.
    """
    if CURRENT_PLATFORM != "windows":
        return
    try:
        from PySide6.QtCore import QObject

        for name in ("snippetsWindow", "symbolsWindow"):
            win = root.findChild(QObject, name)
            if win is None:
                _logger.warning("%s not found; skipping focus-suppression", name)
                continue
            # findChild() is typed to return a bare QObject; the QML side
            # only ever names real top-level Window items here, so this is
            # always a QWindow at runtime.
            win_window = cast(QWindow, win)

            # Bound as a default argument rather than closed over: the loop
            # variable is rebound on the next pass, so a plain closure would
            # leave every handler applying styles to the last window found.
            def _apply(target: QWindow = win_window, label: str = name) -> None:
                try:
                    if target.property("visible"):
                        windows_window.apply_extended_styles(target)
                except Exception as exc:  # pragma: no cover, defensive
                    _logger.debug("%s style apply failed: %s", label, exc)

            win_window.visibleChanged.connect(_apply)
            _logger.info("Wired %s focus-suppression", name)
    except Exception as exc:  # pragma: no cover, defensive
        _logger.warning("Failed to wire the floating windows: %s", exc)


def _migrate_legacy_compat_settings() -> None:
    """Rename legacy compat-mode setting keys to the current names.

    Pre-rename keys: ``savedRemoteCompatMode``, ``savedRemoteCompatAuto``.
    Current keys:    ``savedCompatMode``,       ``savedCompatAutoDetect``.

    The rename happened when compat mode grew from "remote desktop only"
    to "remote desktop + IDEs that intercept keystrokes" — see CHANGELOG.
    Without migration, every existing user who had explicitly toggled
    either flag would silently revert to the new defaults.

    Idempotent: a ``compatSettingsMigrated`` flag prevents re-running.
    The legacy keys are removed once their values have been copied so
    they don't sit around polluting the registry indefinitely.

    Reads the QML ``Settings``-managed registry section directly via
    ``QSettings`` (with the same org/app names QML uses), so it must
    run after ``setOrganizationName`` / ``setApplicationName`` and
    before the QML engine instantiates its ``Settings`` element.
    """
    # QML's Settings element in Main.qml uses `category: "ui"`, which
    # scopes every key under a "ui" group in QSettings.  Match that
    # scope here so the keys we read/write line up.
    settings = QSettings()
    settings.beginGroup("ui")
    try:
        if settings.value("compatSettingsMigrated", False, type=bool):
            return
        legacy_manual_key = "savedRemoteCompatMode"
        legacy_auto_key = "savedRemoteCompatAuto"
        if settings.contains(legacy_manual_key):
            legacy_manual = settings.value(legacy_manual_key, False, type=bool)
            settings.setValue("savedCompatMode", legacy_manual)
            settings.remove(legacy_manual_key)
            _logger.info(
                "Migrated %s=%s → savedCompatMode",
                legacy_manual_key,
                legacy_manual,
            )
        if settings.contains(legacy_auto_key):
            legacy_auto = settings.value(legacy_auto_key, True, type=bool)
            settings.setValue("savedCompatAutoDetect", legacy_auto)
            settings.remove(legacy_auto_key)
            _logger.info(
                "Migrated %s=%s → savedCompatAutoDetect",
                legacy_auto_key,
                legacy_auto,
            )
        settings.setValue("compatSettingsMigrated", True)
    finally:
        settings.endGroup()
    settings.sync()


#: Sentinel recording that the one-time pre-fix log purge has run.  Its
#: presence is the only state: the contents are informational.
_LOG_PURGE_SENTINEL = ".log-privacy-purge"


def _purge_pre_fix_logs(config_dir: Path) -> int:
    """Delete diagnostic logs written before the typed-content fix.

    Releases up to and including 1.0.30 logged the user's typed text to
    ``alpha-osk.log`` at INFO, including up to 200 characters of the
    context buffer, and did so even while privacy mode was active.  The
    logging sites are fixed, but that only stops *new* leakage: an
    upgrading user still has up to four rotated files on disk holding a
    transcript of what they typed, potentially including a password.
    Purging them is part of the fix, not housekeeping.

    Runs once, guarded by a sentinel, so a user who later wants to keep
    logs across restarts is not fighting us.  Returns the number of
    files removed.  Never raises: a failure here must not stop the
    keyboard from starting.
    """
    sentinel = config_dir / _LOG_PURGE_SENTINEL
    if sentinel.exists():
        return 0

    removed = 0
    try:
        # Matches "alpha-osk.log" plus RotatingFileHandler's .1 / .2 / .3.
        for stale in sorted(config_dir.glob(f"{LOG_FILENAME}*")):
            try:
                stale.unlink()
                removed += 1
            except OSError:
                # A locked or already-gone file is not worth failing over;
                # the sentinel still gets written so we do not retry forever.
                continue
        sentinel.write_text(
            f"purged={removed} version={__version__}\n",
            encoding="utf-8",
        )
    except OSError:
        return removed
    return removed


def _configure_logging() -> Path | None:
    """Wire up stderr + rotating file logging.

    The frozen build runs without a console, so stderr is /dev/null, and
    file logging is the only way users can capture updater errors,
    crash tracebacks, etc. Returns the log path (or None on failure).

    Nothing written here may contain typed content: see the diagnostic
    log invariant in CLAUDE.md under "Where User Data Lives".
    """
    fmt = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)

    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter(fmt))
    root.addHandler(stream)

    log_path: Path | None = None
    purged = 0
    try:
        config_dir = get_config_dir()
        # Must happen before the handler opens the file: on Windows the
        # active log cannot be unlinked while the handler holds it.
        purged = _purge_pre_fix_logs(config_dir)
        log_path = config_dir / LOG_FILENAME
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(fmt))
        root.addHandler(file_handler)
    except OSError as e:
        # Non-fatal: stderr handler still works in dev. Frozen users
        # without a writable APPDATA are vanishingly rare.
        root.warning("Could not open log file %s: %s", log_path, e)
        log_path = None

    if purged:
        # Logged after the handler is attached so it lands in the new file,
        # giving the user a record of why their old logs are gone.
        root.info(
            "Removed %d diagnostic log file(s) written before the "
            "typed-content fix; they could contain text you typed.",
            purged,
        )

    return log_path


#: Guards ``_install_exception_hooks`` against chaining onto itself if
#: ``main()`` is ever re-entered in one process (the test suite does it
#: deliberately).  Two copies of the hook would log every traceback twice.
_exception_hooks_installed = False


def _install_exception_hooks() -> None:
    """Route uncaught tracebacks into the diagnostic log.

    Without this the log only ever holds the failures somebody
    remembered to wrap in ``try`` / ``except`` + ``_logger.exception``.
    Everything else goes to ``sys.excepthook``, which writes to stderr,
    and a windowed PyInstaller build has no stderr: ``sys.stderr`` is
    ``None``, so the traceback for an actual crash is discarded at the
    exact moment it is worth the most.  That is the file we ask users to
    attach to a bug report, so it has to contain the crash.

    Both hooks chain to whatever was there before, so stderr still gets
    the traceback in a dev run and PySide's own handling is untouched.
    ``KeyboardInterrupt`` is passed straight through unlogged (Ctrl-C is
    a user action, not a fault), and the log call is wrapped because a
    hook that raises replaces the crash being reported with its own.

    Threads get the same treatment via ``threading.excepthook``: the
    dictation capture, the updater's download worker and Linux's AT-SPI
    listener all run off the main thread, and an exception there never
    reaches ``sys.excepthook`` at all.
    """
    global _exception_hooks_installed
    if _exception_hooks_installed:
        return

    previous_hook = sys.excepthook

    def _log_uncaught(
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        if not issubclass(exc_type, KeyboardInterrupt):
            try:
                _logger.critical("Uncaught exception", exc_info=(exc_type, exc, tb))
            except Exception:  # pragma: no cover - logging itself failed
                pass
        previous_hook(exc_type, exc, tb)

    previous_thread_hook = threading.excepthook

    def _log_uncaught_in_thread(args: threading.ExceptHookArgs) -> None:
        # SystemExit in a thread is how a worker asks to stop; it is not
        # a fault and the default hook already ignores it.
        if args.exc_type is not None and not issubclass(args.exc_type, SystemExit):
            thread_name = args.thread.name if args.thread is not None else "<unknown>"
            try:
                # exc_value is typed Optional to mirror sys.exc_info()'s
                # general (type, value, tb) shape, but Logger.critical's
                # exc_info tuple form requires a real exception instance,
                # not None, alongside a real type. A live thread crash
                # always carries both together; this branch only exists
                # so an all-type-no-value ExceptHookArgs (which nothing in
                # this codebase produces, but the type does not rule out)
                # still gets logged instead of silently falling through.
                if args.exc_value is not None:
                    _logger.critical(
                        "Uncaught exception in thread %s",
                        thread_name,
                        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
                    )
                else:
                    _logger.critical(
                        "Uncaught exception in thread %s (exc_type=%s, no exception instance)",
                        thread_name,
                        args.exc_type,
                    )
            except Exception:  # pragma: no cover - logging itself failed
                pass
        previous_thread_hook(args)

    sys.excepthook = _log_uncaught
    threading.excepthook = _log_uncaught_in_thread
    _exception_hooks_installed = True


# Stable per-application identity for the Windows taskbar.  Must match the
# AppUserModelID set on the installer's Start Menu / Desktop shortcuts so a
# pinned shortcut groups with the running window.  Format convention is
# ``Company.Product`` (see Microsoft's AppUserModelID guidance).  Kept here
# rather than in windows_window.py: it must also match the AppUserModelID
# stamped on the installer's shortcuts, which is an app packaging concern,
# not a windowing one.
APP_USER_MODEL_ID = "OKStudio.AlphaOSK"


def _toggle_keyboard_window(root: QWindow, platform_name: str = CURRENT_PLATFORM) -> None:
    """Put the keyboard away, or bring it back.  One click each way.

    On Windows and Linux "away" means **minimized**, not hidden.  The OSK
    carries a normal taskbar entry (``WS_EX_TOOLWINDOW`` is actively
    cleared and ``WS_EX_APPWINDOW`` set, see
    :func:`src.platform.windows_window.apply_extended_styles`), so a
    minimized keyboard is still visibly parked somewhere the user can get
    at it, and the tray icon and the taskbar entry agree about where the
    window went.  Hiding it outright threw that away: the window vanished
    from the taskbar and the tray icon became the only route back.

    macOS keeps the hide/show pair because it has no taskbar or Dock entry
    to minimize *into* (the app runs under the Accessory activation policy,
    see :func:`src.platform.macos_window.apply_window_flags`), so there
    minimizing would be the version that leaves the user with only the
    tray icon.

    A **tucked** window falls back to hiding for the same reason: while
    parked off-screen it is DOCK-typed, which costs it the taskbar entry
    and makes ``showMinimized()`` inert, so minimizing there would be a
    dead tray icon rather than a stash.  ``tucked`` is QML-side state
    (X11 only, see ``toggleTuck`` in ``Main.qml``); reading it through
    ``property()`` yields ``None`` on any window that doesn't declare it,
    which is the right answer everywhere else.

    The restore branch is keyed on the window's own visibility rather than
    on what this function did last, so it also picks up a window the user
    minimized with the title-bar minus button or the taskbar.

    ``platform_name`` is a parameter rather than a direct read of
    ``CURRENT_PLATFORM`` so the tests can drive every branch on one host.
    """
    if root.visibility() in (QWindow.Visibility.Hidden, QWindow.Visibility.Minimized):
        root.showNormal()
        root.raise_()
    elif platform_name == "macos" or bool(root.property("tucked")):
        root.hide()
    else:
        root.showMinimized()


class _TrayClickRouter:
    """Turn raw tray activations into exactly one window toggle per click.

    *Which* activations count lives here; *what* the toggle does lives in
    :func:`_toggle_keyboard_window`.  Lives at module level, rather than as
    a closure in ``main()``, purely so the tests can reach it without a
    running ``QApplication``.

    **A double click toggles once, not twice.** Windows delivers a double
    click as Trigger, DoubleClick, Trigger, so acting on every activation
    would flip the window straight back to where it started.  Collapsing a
    burst into one toggle is also what lets the toggle fire *immediately*:
    the old code had to sit on each click for the full double-click
    interval to learn whether a second one was coming, and a tray click
    that lags half a second reads as broken.

    ``double_click_interval_ms`` is a callable (normally
    ``QApplication.doubleClickInterval``) so the live system setting is
    read per click rather than snapshotted at startup.
    """

    def __init__(
        self,
        toggle: Callable[[], None],
        double_click_interval_ms: Callable[[], int],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._toggle = toggle
        self._double_click_interval_ms = double_click_interval_ms
        self._clock = clock
        # Time of the last *toggle*, not the last activation: stamping this
        # on suppressed events too would let a stream of clicks keep
        # re-arming the guard, and the tray icon would go dead.
        self._last_toggle: float | None = None

    def __call__(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason not in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            return
        now = self._clock()
        if self._last_toggle is not None:
            if (now - self._last_toggle) * 1000.0 < self._double_click_interval_ms():
                return
        self._last_toggle = now
        self._toggle()


def main() -> int:
    """Launch the Alpha-OSK on-screen keyboard."""
    # CLI dispatch — the post-update relauncher re-invokes this binary
    # with ``--update-relauncher`` and runs in a detached process owned
    # by the user session, so it can launch the freshly-installed OSK
    # at user IL after the elevated installer has exited. Skipping the
    # singleton lock and the QApplication setup here keeps the helper
    # cheap and side-effect-free; see ``src/_update_relauncher.py``
    # for the polling logic and rationale.
    if "--update-relauncher" in sys.argv:
        from src._update_relauncher import run_relauncher

        return run_relauncher(sys.argv)

    log_path = _configure_logging()
    # Must follow _configure_logging: the hook is only worth anything
    # once there is a file handler for it to write into.
    _install_exception_hooks()
    if log_path is not None:
        _logger.info("Log file: %s", log_path)
    # Enable debug logging for prediction to see sources
    logging.getLogger("HybridPredictor").setLevel(logging.DEBUG)

    # Platform-specific environment setup (must happen before QApp)
    _setup_platform_env()

    # Claim an explicit taskbar identity before any window exists, so the
    # taskbar button keeps our icon instead of reverting to the generic
    # default once the OSK window appears.  No-op off Windows.
    windows_window.set_app_user_model_id(APP_USER_MODEL_ID)

    # Log platform info
    pinfo = get_platform_info()
    _logger.info("Platform: %s", pinfo.get("platform"))
    if CURRENT_PLATFORM == "windows":
        _logger.info(
            "UIAccess: %s",
            "active" if pinfo.get("ui_access") else "not active",
        )

    # Use PassThrough rounding so Qt does not multiply logical window sizes
    # by a rounded scale factor when moving between monitors with different DPI.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Alpha-OSK")
    app.setOrganizationName("alpha-osk")

    # macOS: drop into Accessory activation policy so clicking the
    # OSK doesn't yank the app to the foreground (and thereby steal
    # focus from the text field the user is typing into).  Must
    # happen after QApplication() because NSApp is created during
    # QApplication's __init__.
    if CURRENT_PLATFORM == "macos":
        macos_window.apply_activation_policy()

    # Migrate any legacy "Remote Desktop Mode" setting keys to the new
    # "Compatibility Mode" names before QML's Settings element binds.
    # Idempotent — guarded by a flag, so it costs nothing after the
    # first run.
    _migrate_legacy_compat_settings()

    # Single-instance check.  Run before any expensive setup (QML
    # engine, prediction model load) so a duplicate launch returns
    # almost immediately.  We need QApplication for the message-loop
    # plumbing QSharedMemory uses on some platforms; otherwise this
    # would be cheaper still.
    if not _acquire_singleton_or_surface():
        return 0

    # Set app icon
    icon_file = _icon_path()
    if icon_file:
        app_icon = QIcon(str(icon_file))
        app.setWindowIcon(app_icon)
        _logger.info("App icon loaded: %s", icon_file)
    else:
        app_icon = QIcon()
        _logger.warning("App icon not found")

    # Create the bridge (auto-detects platform key synthesizer)
    bridge = KeyboardBridge()

    if not bridge.synthAvailable:
        if CURRENT_PLATFORM == "linux":
            _logger.warning(
                "No key synthesis tool found. Install xdotool: sudo apt install xdotool"
            )
        elif CURRENT_PLATFORM == "macos":
            _logger.warning(
                "macOS key synthesis unavailable. "
                "Install pyobjc-framework-Quartz, and grant Alpha-OSK "
                "Accessibility permission in System Settings → "
                "Privacy & Security → Accessibility."
            )
        else:
            _logger.warning(
                "Key synthesis not available. Keystrokes will not be sent to other applications."
            )

    # Set up QML engine
    engine = QQmlApplicationEngine()

    # Surface QML diagnostics through the Python logger.  Without this,
    # QQmlApplicationEngine silently swallows parse / binding errors and
    # rootObjects() just returns empty — past startup crashes were much
    # harder to diagnose than they needed to be.
    def _on_qml_warnings(warnings: list) -> None:
        for w in warnings:
            _logger.warning("QML: %s", w.toString())

    engine.warnings.connect(_on_qml_warnings)

    # Expose bridge to QML
    engine.rootContext().setContextProperty("keyboard", bridge)

    # Load QML
    main_qml = qml_path()
    if not main_qml.exists():
        _logger.error("QML file not found: %s", main_qml)
        return 1

    _logger.info("Loading QML from: %s", main_qml)
    engine.load(QUrl.fromLocalFile(str(main_qml)))

    if not engine.rootObjects():
        _logger.error("Failed to load QML — see preceding QML: warnings")
        return 1

    # Apply window flags for OSK behavior (cross-platform + Windows extras).
    # rootObjects() is typed to return QObject; the loaded root is always
    # the top-level QML Window, i.e. a QWindow, at runtime.
    root = cast(QWindow, engine.rootObjects()[0])
    if root:
        _apply_window_flags(root)
        _wire_floating_windows(root)

    # --- System tray icon ---
    tray = QSystemTrayIcon(app_icon, app)
    tray_menu = QMenu()
    show_action = tray_menu.addAction(
        "Show / Hide" if CURRENT_PLATFORM == "macos" else "Show / Minimize"
    )
    tray_menu.addSeparator()
    quit_action = tray_menu.addAction("Quit Alpha-OSK")

    def _toggle_visibility() -> None:
        _toggle_keyboard_window(root)

    # Held in a local (not inlined into connect) so the router outlives the
    # call: PySide does not promise to keep a strong reference to a callable
    # object used as a slot, and main() is on the stack for the app's life.
    tray_click_router = _TrayClickRouter(_toggle_visibility, app.doubleClickInterval)

    show_action.triggered.connect(_toggle_visibility)
    tray.activated.connect(tray_click_router)
    quit_action.triggered.connect(app.quit)
    tray.setContextMenu(tray_menu)
    tray.setToolTip("Alpha-OSK")
    tray.show()
    _logger.info("System tray icon active")

    # Save state on quit, then stop background timers so nothing fires
    # after the bridge / predictor start being torn down.
    def _on_about_to_quit() -> None:
        if bridge.autoSaveOnExit:
            _logger.info("Auto-saving prediction model on exit...")
            bridge.savePredictionModel()
        bridge.saveAnalytics()
        bridge.shutdown()

    app.aboutToQuit.connect(_on_about_to_quit)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
