"""
macOS Window Styling
=====================

pyobjc/AppKit-specific window and application tuning for the on-screen
keyboard: the ``Accessory`` activation policy that stops a click on the
OSK from yanking the app to the foreground, and the NSWindow-level flags
(floating level, all-Spaces collection behavior, ``hidesOnDeactivate``)
that Qt does not expose through its own flag system.

This used to live inline in ``keyboard_app.py`` alongside the Windows
ctypes code, which is why that whole file carried a blanket mypy
``ignore_errors`` override.  Window styling is an OS-abstraction concern
like the rest of ``src/platform/`` -- see ``x11_window.py`` for the X11
counterpart and ``windows_window.py`` for the Windows one -- so it belongs
in this package on its own merits, not only for the mypy split.

Unlike ``windows_window.py``, these functions are **not** guarded by a
literal ``if sys.platform == "darwin":`` check.  Doing that would prune
them under both of the project's required mypy runs (``--platform linux``
*and* ``--platform win32`` -- neither one is ``darwin``, so the guard
would be true on both and the body would never be type-checked at all).
Instead, exactly like ``src/platform/password_detect.py``'s
``_MacOSAXDetector``, each function guards its own ``pyobjc``/``AppKit``
import with a plain ``try``/``except ImportError`` and returns early on
failure. Every name that import produces is typed ``Any`` by the
accompanying ``# type: ignore[import-not-found]``, so the function bodies
are genuinely type-checked (weakly, through ``Any``) on every platform,
which is what lets this module carry no blanket exemption either.

Silently no-ops if pyobjc isn't installed, which is always the case off
macOS: the OSK still works, it just steals focus on click (activation
policy) and won't follow Spaces or float over fullscreen apps (window
flags).
"""

from __future__ import annotations

import logging

from PySide6.QtGui import QWindow

_logger = logging.getLogger("macos_window")


def apply_activation_policy() -> None:
    """Switch the NSApplication into ``Accessory`` activation policy.

    This is the **critical** fix for OSK focus theft on macOS.  Qt's
    ``WindowDoesNotAcceptFocus`` flag maps to ``canBecomeKeyWindow:
    NO``, which stops the window from receiving keyboard input — but
    on macOS, clicking on a window *also* activates the owning
    application (yanks it to the foreground, owns the menu bar).  The
    NSWindow-level flag does not prevent that.

    Result before this fix: clicking any OSK key activated Alpha-OSK
    as the foreground app, kicking TextEdit out of the frontmost slot.
    ``CGEventPost`` then sent the synthesised keystroke to Alpha-OSK
    itself (the new foreground), so nothing reached the editor — user
    saw "keystrokes not sending".

    ``NSApplicationActivationPolicyAccessory`` tells AppKit that this
    app should never become the active app: clicks on its windows do
    not steal application focus, and the previously frontmost app
    keeps receiving input.  Same model used by macOS's own
    "Accessibility Keyboard" and by menu-bar utilities like Magnet /
    Rectangle / AltTab.

    Trade-offs:
    - **No Dock icon.** The system tray icon (already wired in
      ``main()``) carries show/hide/quit.
    - **No Cmd+Tab entry.** The OSK isn't an app in the switcher
      sense; it's a system overlay.  Users who want a Cmd+Tab entry
      can comment this out and pay the focus-theft cost, but for
      first ship Accessory is the right answer.
    - **No menu bar.** Qt was already not driving a menu bar for us.

    Must run AFTER ``QApplication(sys.argv)`` so ``NSApp`` exists,
    and BEFORE ``app.exec()``.  Silently no-ops if pyobjc isn't
    available — degraded behaviour is "OSK works but steals focus",
    same as without this function at all.
    """
    try:
        from AppKit import (  # type: ignore[import-not-found]
            NSApp,
            NSApplicationActivationPolicyAccessory,
        )
    except ImportError as exc:
        _logger.warning(
            "pyobjc not available (%s) — cannot set Accessory activation "
            "policy. OSK will likely steal focus on click. "
            "Install: pip install pyobjc-framework-Cocoa",
            exc,
        )
        return

    try:
        # NSApp is the global NSApplication singleton — created by Qt
        # the moment QApplication is instantiated.
        if NSApp is None:
            _logger.warning(
                "NSApp is None — QApplication probably not yet created. "
                "Call apply_activation_policy() after QApplication(sys.argv)."
            )
            return
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        _logger.info(
            "Applied NSApplicationActivationPolicyAccessory — "
            "OSK will not steal application focus on click"
        )
    except Exception as exc:
        _logger.warning(
            "Failed to set Accessory activation policy: %s — OSK may steal focus when clicked",
            exc,
        )


def apply_window_flags(root: QWindow) -> None:
    """Configure the NSWindow backing the QML root for OSK behaviour.

    Three things we ask Cocoa for that Qt does not surface as flags:

    1. **Level = NSFloatingWindowLevel** (3) — float above ordinary
       windows.  Qt's ``WindowStaysOnTopHint`` already requests this
       on macOS, but we restate it for defence-in-depth and to match
       the Windows path, which places the window in the topmost
       Z-order band via ``SetWindowPos(HWND_TOPMOST)`` rather than a
       style bit (see :func:`windows_window.apply_extended_styles`).
    2. **Collection behavior** — join all Spaces so the keyboard
       follows the user when they switch desktops, mark it transient
       so Mission Control won't try to tile it as a real window, and
       add the fullscreen-auxiliary flag so it appears above other
       apps that have entered fullscreen mode.
    3. **hidesOnDeactivate = NO** — keep the keyboard visible the
       moment focus moves to the text editor the user is typing into.
       The default is NO for NSWindow, but Qt sometimes flips it for
       Tool-class windows; setting it explicitly is cheap insurance.

    Qt's ``WindowDoesNotAcceptFocus`` already prevents the window
    from becoming key on macOS (it maps to ``canBecomeKeyWindow`` →
    NO), so we don't need to subclass NSWindow here.  If a future
    regression brings focus-theft back, swizzling
    ``canBecomeKeyWindow`` on the live window is the next step.

    Silently no-ops if pyobjc isn't installed — the OSK will still
    work, the keyboard just won't follow Spaces and may dip behind
    fullscreen apps.
    """
    try:
        import objc  # type: ignore[import-not-found]
        from AppKit import (  # type: ignore[import-not-found]
            NSFloatingWindowLevel,
            NSPanel,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorTransient,
        )
    except ImportError as exc:
        _logger.warning(
            "pyobjc not available (%s) — skipping macOS NSWindow tuning. "
            "Install with: pip install pyobjc-framework-Cocoa",
            exc,
        )
        return

    try:
        # root.winId() returns the native NSView pointer on macOS.
        # Wrap it as a real ObjC object and walk up to the NSWindow.
        ns_view = objc.objc_object(c_void_p=int(root.winId()))
        ns_window = ns_view.window()
        if ns_window is None:
            _logger.warning(
                "Could not obtain NSWindow from QML root — macOS window flags not applied"
            )
            return

        # The actual NSWindow class.  On Qt 6 / PySide6, QQuickWindow
        # produces ``QNSWindow`` here regardless of Qt.Tool flag —
        # Qt 5's Tool→NSPanel mapping was dropped.  We log at DEBUG
        # in case a future Qt version restores the panel mapping (we'd
        # see ``is_panel=True`` here and the NonactivatingPanel style
        # bit below would actually do something).  Focus theft is
        # *not* solved by the NSWindow tuning in this function — the
        # working solution is the ``CGEventPostToPid`` routing in
        # ``MacOSKeySynthesizer._post_event``.
        cls_name = ns_window.className()
        is_panel = bool(ns_window.isKindOfClass_(NSPanel))
        _logger.debug(
            "QML root NSWindow class=%s is_panel=%s",
            cls_name,
            is_panel,
        )

        ns_window.setLevel_(NSFloatingWindowLevel)
        ns_window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorTransient
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        ns_window.setHidesOnDeactivate_(False)

        # NSWindowStyleMaskNonactivatingPanel = 1 << 7 (0x80).  Only
        # NSPanel honors this bit; plain NSWindow ignores it.  We OR
        # it in opportunistically so that a future Qt version that
        # restores the Tool→NSPanel mapping would automatically pick
        # up the non-activating semantics with no further changes
        # here.  Today (Qt 6.10.x) ``is_panel`` is False and this
        # branch is dead — the focus story is handled by
        # CGEventPostToPid in the synthesizer.
        if is_panel:
            NS_WINDOW_STYLE_MASK_NONACTIVATING_PANEL = 1 << 7
            current_mask = int(ns_window.styleMask())
            new_mask = current_mask | NS_WINDOW_STYLE_MASK_NONACTIVATING_PANEL
            ns_window.setStyleMask_(new_mask)
            try:
                ns_window.setWorksWhenModal_(True)
            except Exception:
                pass
            _logger.debug(
                "NSPanel styleMask: %#x → %#x (added NonactivatingPanel)",
                current_mask,
                new_mask,
            )

        _logger.info(
            "Applied macOS NSWindow flags: "
            "floating level, all-Spaces, fullscreen-aux, hides-on-deactivate=NO"
        )
    except Exception as exc:
        _logger.warning("Failed to apply macOS NSWindow flags: %s", exc)
