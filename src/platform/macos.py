"""
macOS Key Synthesizer
======================

Implements key synthesis for macOS using **Quartz Event Services**
(``CGEventCreateKeyboardEvent`` / ``CGEventPost``) via pyobjc.

Two paths:

1. **Character path** (``send_text``): builds an event with a placeholder
   keycode and stamps the Unicode string via
   ``CGEventKeyboardSetUnicodeString``.  Equivalent to Windows
   ``KEYEVENTF_UNICODE`` — apps that read characters get them; apps
   that read raw keycodes (a small set of games / remote-desktop
   clients) don't.  Fine for normal text entry, which is the OSK's job.
2. **Chord / special-key path** (``send_key`` / ``send_combination``):
   builds events with the real US ANSI virtual keycode for the action
   key and ORs the active modifier flags
   (``kCGEventFlagMaskShift`` etc.) onto the event so the target app
   sees Cmd+C, Ctrl+Tab, etc. as a real chord.

Accessibility permission
------------------------
``CGEventPost`` silently no-ops for processes that are not trusted by
the macOS Accessibility subsystem (TCC).  The first post triggers a
prompt — System Settings → Privacy & Security → Accessibility — and
the user must enable Alpha-OSK there.  We log a warning the first
time we try to post without that grant so users know why nothing is
typing.

Modifier model
--------------
Sticky modifiers (the OSK's Shift / Ctrl / Alt / Win toggles) are
held at the OS level by posting a key-down event for the modifier
keycode itself, without a matching key-up, in
:meth:`hold_modifier`.  This is what lets Cmd+click work for opening
links in a new tab from the OSK.  Per-event flags are *also* ORed
onto subsequent ``CGEventSetFlags`` calls so each synthesised
keystroke carries the modifier flag explicitly — both belt and
braces.

The OSK's "win" modifier maps to the macOS **Command** key
(``kVK_Command``, ``kCGEventFlagMaskCommand``).  That mirrors how
"win" maps to Super on Linux: it's the primary OS-level shortcut
modifier on each platform.

Dependencies
------------
- ``pyobjc-framework-Quartz`` (provides the Quartz module).
- ``pyobjc-framework-Cocoa`` is pulled in transitively.

See Also
--------
- ``base.py`` — abstract interface this class implements.
- ``docs/PLATFORM_ARCHITECTURE.md`` — design rationale.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from .base import KeySynthesizerBase

_logger = logging.getLogger("MacOSKeySynthesizer")


# ---------------------------------------------------------------------------
#  Virtual keycodes (US ANSI / kVK_ANSI_*)
# ---------------------------------------------------------------------------
#
# Source: HIToolbox/Events.h.  These are layout-position keycodes (not
# Unicode) — the same physical key on a French AZERTY layout is the
# same kVK_ANSI_A even though it produces 'q'.  We use them for chord
# action keys (where the app reads the keycode) and for the special
# key map below.  For plain text we stamp Unicode strings instead.

_VK_LETTERS: Dict[str, int] = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05,
    "z": 0x06, "x": 0x07, "c": 0x08, "v": 0x09, "b": 0x0B, "q": 0x0C,
    "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10, "t": 0x11, "o": 0x1F,
    "u": 0x20, "i": 0x22, "p": 0x23, "l": 0x25, "j": 0x26, "k": 0x28,
    "n": 0x2D, "m": 0x2E,
}
_VK_DIGITS: Dict[str, int] = {
    "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17,
    "9": 0x19, "7": 0x1A, "8": 0x1C, "0": 0x1D,
}
_VK_PUNCT: Dict[str, int] = {
    "=": 0x18, "-": 0x1B, "]": 0x1E, "[": 0x21, "'": 0x27,
    ";": 0x29, "\\": 0x2A, ",": 0x2B, "/": 0x2C, ".": 0x2F,
    "`": 0x32, " ": 0x31,
}

# Platform-neutral special-key names → kVK_*
_VK_SPECIAL: Dict[str, int] = {
    # Naming follows xdotool / Qt conventions used by the rest of the codebase
    "Return": 0x24,
    "Enter": 0x24,
    "Tab": 0x30,
    "space": 0x31,
    "Space": 0x31,
    "BackSpace": 0x33,
    "Escape": 0x35,
    "Caps_Lock": 0x39,
    "Left": 0x7B,
    "Right": 0x7C,
    "Down": 0x7D,
    "Up": 0x7E,
    "Home": 0x73,
    "End": 0x77,
    "Page_Up": 0x74,
    "Page_Down": 0x79,
    "Delete": 0x75,        # forward delete
    "Insert": 0x72,        # Help key on Apple keyboards; closest analogue
    "F1": 0x7A, "F2": 0x78, "F3": 0x63, "F4": 0x76,
    "F5": 0x60, "F6": 0x61, "F7": 0x62, "F8": 0x64,
    "F9": 0x65, "F10": 0x6D, "F11": 0x67, "F12": 0x6F,
}

# Modifier name (caller-facing) → (keycode, flag-mask)
_KCG_FLAG_SHIFT = 0x00020000
_KCG_FLAG_CONTROL = 0x00040000
_KCG_FLAG_OPTION = 0x00080000
_KCG_FLAG_COMMAND = 0x00100000

_MOD_INFO: Dict[str, Tuple[int, int]] = {
    "shift": (0x38, _KCG_FLAG_SHIFT),
    "ctrl":  (0x3B, _KCG_FLAG_CONTROL),
    "alt":   (0x3A, _KCG_FLAG_OPTION),
    "win":   (0x37, _KCG_FLAG_COMMAND),     # OSK "win" → macOS Command
    "cmd":   (0x37, _KCG_FLAG_COMMAND),     # accept the native name too
    "super": (0x37, _KCG_FLAG_COMMAND),     # accept Linux-ish naming
}

# kCGHIDEventTap — events go in at the lowest tap, indistinguishable
# from a physical keystroke to anything above (Quartz tap chain).
_KCG_HID_EVENT_TAP = 0


class MacOSKeySynthesizer(KeySynthesizerBase):
    """macOS key synthesis via Quartz Event Services.

    Attributes:
        _available: True once pyobjc + Quartz have imported.  False
            disables every send method to a logged no-op so a missing
            framework doesn't crash the whole keyboard.
        _held_mods: Set of modifier names currently held via
            :meth:`hold_modifier`.  Ored into the flags of every event
            we post so apps see the modifier on each keystroke.
    """

    def __init__(self) -> None:
        self._available = False
        self._Quartz: Any = None
        self._NSWorkspace: Any = None
        self._held_mods: set[str] = set()
        # We warn once for missing Accessibility grant; ``CGEventPost``
        # silently fails for untrusted processes, so without the warn
        # the OSK looks broken with no logs.
        self._warned_untrusted = False
        # Direct delivery target.  Updated whenever ANY non-self app
        # becomes frontmost (NSWorkspaceDidActivateApplicationNotification
        # observer).  When set, events go via CGEventPostToPid which
        # is frontmost-independent — Alpha-OSK can be the activated
        # app and the keystroke still lands in the editor.  None on
        # cold start until the user has activated at least one other
        # app since launch; in that fallback case we use CGEventPost
        # and live with the focus theft.
        self._self_pid = os.getpid()
        self._target_pid: Optional[int] = None
        # Strong reference to the notification observer block so it
        # isn't garbage-collected while NSWorkspace still holds a
        # weak ref to it.  Without this the observer fires once or
        # twice then silently stops working.
        self._activation_observer: Any = None

        try:
            import Quartz  # type: ignore[import-not-found]
        except ImportError as exc:
            _logger.warning(
                "pyobjc Quartz module unavailable (%s). "
                "Install with: pip install pyobjc-framework-Quartz",
                exc,
            )
            return

        self._Quartz = Quartz
        # NSWorkspace serves two purposes:
        # - Diagnostic log of who's currently frontmost when we post
        # - Notification source for tracking the "target" app pid so
        #   we can route events via CGEventPostToPid regardless of
        #   focus state.  Sidesteps the Qt-doesn't-give-us-NSPanel
        #   problem entirely.
        # NSApp isn't needed any more (the deactivate-on-post dance
        # didn't help and CGEventPostToPid removes the need).  Missing
        # AppKit shouldn't disable the synthesizer — without it we
        # fall back to CGEventPost and the user will see focus theft.
        self._NSWorkspace = None
        try:
            from AppKit import NSWorkspace  # type: ignore[import-not-found]
            self._NSWorkspace = NSWorkspace
        except ImportError:
            pass
        self._install_target_observer()
        self._seed_target_from_parent()
        self._available = True
        _logger.info(
            "macOS key synthesizer ready (Quartz CGEvent, self pid=%d, "
            "initial target_pid=%s)",
            self._self_pid, self._target_pid,
        )

    # ------------------------------------------------------------------ #
    #  Target pid plumbing
    # ------------------------------------------------------------------ #

    def set_target_pid(self, pid: int) -> None:
        """Set the pid that future events will be posted to.

        Called by the bridge's 250 ms foreground-window poll whenever
        the OS reports a non-self frontmost app — second path beside
        the ``NSWorkspaceDidActivateApplicationNotification`` observer
        installed in ``_install_target_observer``.  Belt and braces:
        the observer is event-driven, the poll catches anything the
        observer missed (e.g. activation transitions during the
        observer install window, or NSWorkspace not firing the
        notification for the very first activation after launch).

        Idempotent: skips logging if the pid is unchanged.
        """
        if pid <= 0 or pid == self._self_pid:
            return
        if pid != self._target_pid:
            _logger.info("target_pid set via bridge poll → %d", pid)
            self._target_pid = pid

    def _seed_target_from_parent(self) -> None:
        """Initial target = the GUI app at the top of our process tree.

        Without this, the first ~hundreds of ms after launch have
        ``_target_pid = None`` — the observer hasn't fired yet because
        no app *changed* activation since we installed it.  Any
        keystroke posted in that window falls back to ``CGEventPost``
        (frontmost-targeted), which delivers to Alpha-OSK itself
        because clicking on the OSK activates us.  Users see the very
        first keystroke vanish.

        ``os.getppid()`` alone is not enough: when launched from a
        terminal via ``python -m src.keyboard_app``, the parent is
        ``bash`` (a CLI process with no NSRunningApplication entry),
        not the terminal app.  Posting events to bash's pid is a
        no-op.  We walk up the process tree until we find a pid that
        ``NSRunningApplication.runningApplicationWithProcessIdentifier_``
        recognises — that's the user-facing app (Terminal / iTerm /
        Cursor / VS Code / etc.) the user was using when they
        launched us.

        For ``.app`` launches via Finder / Dock the parent is
        ``launchd`` (pid 1) and the walk bottoms out without finding
        anything; we just leave ``_target_pid`` as None and rely on
        the observer to fire the moment the user clicks into a real
        editor.  Pre-walk filters reject self-pid and pid<=1.

        The observer overrides this seed the moment the user
        activates any non-self app — so a wrong seed self-heals as
        soon as the user clicks into the real editor they want to
        type into.
        """
        try:
            from AppKit import NSRunningApplication  # type: ignore[import-not-found]
        except ImportError:
            return

        try:
            pid: Optional[int] = os.getppid()
        except OSError:
            return

        # Walk up the parent chain, max 16 hops as a sanity cap so a
        # weird ppid loop or runaway value can't make us spin.
        for _ in range(16):
            if pid is None or pid <= 1 or pid == self._self_pid:
                return
            try:
                app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            except Exception as exc:
                _logger.debug("runningApplicationWithProcessIdentifier failed: %s", exc)
                return
            if app is not None:
                self._target_pid = pid
                try:
                    name = str(app.localizedName() or "?")
                except Exception:
                    name = "?"
                _logger.info(
                    "Seeded target_pid from process tree: %d (%s)",
                    pid, name,
                )
                return
            pid = self._parent_pid_of(pid)

    @staticmethod
    def _parent_pid_of(pid: int) -> Optional[int]:
        """Return the parent pid of *pid*, or None on failure.

        Uses ``ps -o ppid= -p <pid>`` because macOS has no ``/proc``
        and pyobjc doesn't expose the underlying ``kinfo_proc``
        struct in a way that's stable across versions.  ``ps`` is on
        every macOS install and the overhead (~20 ms per call,
        amortised over the seed walk's max-16 iterations) only fires
        once at synth init.
        """
        import subprocess
        try:
            out = subprocess.check_output(
                ["ps", "-o", "ppid=", "-p", str(pid)],
                stderr=subprocess.DEVNULL,
                timeout=1.0,
            )
            return int(out.strip())
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                ValueError, OSError):
            return None

    # ------------------------------------------------------------------ #
    #  Interface implementation
    # ------------------------------------------------------------------ #

    def is_available(self) -> bool:
        return self._available

    def backend_name(self) -> str:
        return "Quartz CGEvent" if self._available else "none"

    def send_key(
        self,
        key_name: str,
        modifiers: Optional[List[str]] = None,
    ) -> None:
        """Send a keycode-based key event (optionally chorded).

        Falls back to :meth:`send_text` when ``key_name`` is a single
        printable character with no resolvable keycode (rare on US
        layouts, but possible for accented input).
        """
        if not self._available:
            _logger.debug("send_key no-op: synth unavailable")
            return

        keycode = self._resolve_keycode(key_name)
        if keycode is None:
            # No keycode for this character — fall back to Unicode
            # injection, which is the right answer for plain text but
            # doesn't carry modifiers.  We don't try to fabricate a
            # chord here because chord keys without a keycode would
            # require synthesising the OS-level layout map.
            if not modifiers:
                self.send_text(key_name)
                return
            _logger.warning(
                "No keycode for %r and modifiers %s — dropping",
                key_name, modifiers,
            )
            return

        flags = self._flags_for(modifiers)
        self._post_keycode(keycode, flags)

    def send_text(self, text: str) -> None:
        """Inject a Unicode string via ``CGEventKeyboardSetUnicodeString``.

        Each char is sent as a key-down + key-up event pair whose
        payload is the Unicode codepoint(s).  Modifier flags currently
        held by the OSK are *not* applied — pure text injection should
        not be modified by Shift / Ctrl / Cmd.
        """
        if not self._available or not text:
            return

        self._log_frontmost_for_send(f"send_text({text!r})")
        Quartz = self._Quartz
        for ch in text:
            # 0 is an unused virtual keycode; CGEventKeyboardSetUnicodeString
            # overrides whatever the OS would have derived from it.
            for is_down in (True, False):
                ev = Quartz.CGEventCreateKeyboardEvent(None, 0, is_down)
                if ev is None:
                    self._warn_untrusted_once()
                    return
                Quartz.CGEventKeyboardSetUnicodeString(ev, len(ch), ch)
                self._post_event(ev)

    def send_combination(self, keys: List[str]) -> None:
        """Send a chord — last element is the action key, rest are mods."""
        if not keys:
            return
        *mods, action = keys
        self.send_key(action, modifiers=mods if mods else None)

    # ------------------------------------------------------------------ #
    #  Modifier hold / release
    # ------------------------------------------------------------------ #

    def hold_modifier(self, key_name: str) -> None:
        """Press a modifier and leave it held at the OS level."""
        if not self._available:
            return
        info = _MOD_INFO.get(key_name)
        if info is None:
            _logger.warning("hold_modifier: unknown modifier %r", key_name)
            return
        keycode, _ = info
        self._post_modifier(keycode, key_down=True)
        self._held_mods.add(key_name)

    def release_modifier(self, key_name: str) -> None:
        """Release a previously held modifier."""
        if not self._available:
            return
        info = _MOD_INFO.get(key_name)
        if info is None:
            return
        keycode, _ = info
        self._post_modifier(keycode, key_down=False)
        self._held_mods.discard(key_name)

    def reset_modifier_state(self) -> None:
        """Defensively release Shift / Ctrl / Option / Command at startup.

        Safe to call at app boot — a key-up event for a modifier the
        user is not physically holding is a no-op at the Quartz tap.
        See the base-class docstring for why this must NOT run during
        interactive use.
        """
        if not self._available:
            return
        _logger.info("Resetting OS modifier state (defensive keyup)")
        for keycode, _ in _MOD_INFO.values():
            self._post_modifier(keycode, key_down=False)
        self._held_mods.clear()

    def replace_text(self, backspace_count: int, text: str) -> None:
        """Atomically select N chars (Shift+Left × N) then type *text*.

        Mirrors the Windows / Linux paths: using Shift+Left keeps the
        target field non-empty between deletion and replacement (some
        chat composers close empty fields and would lose focus).  Each
        Shift+Left chord is independent; the trailing ``send_text``
        clears the selection by overwriting it.
        """
        if not self._available:
            return
        if backspace_count <= 0:
            self.send_text(text)
            return

        for _ in range(backspace_count):
            self.send_key("Left", modifiers=["shift"])
        if text:
            self.send_text(text)

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    def _resolve_keycode(self, key_name: str) -> Optional[int]:
        """Translate a platform-neutral key name to a kVK_* keycode."""
        if key_name in _VK_SPECIAL:
            return _VK_SPECIAL[key_name]
        lower = key_name.lower()
        if lower in _VK_LETTERS:
            return _VK_LETTERS[lower]
        if key_name in _VK_DIGITS:
            return _VK_DIGITS[key_name]
        if key_name in _VK_PUNCT:
            return _VK_PUNCT[key_name]
        return None

    def _flags_for(self, modifiers: Optional[List[str]]) -> int:
        """Compute the CGEventFlags mask for an outgoing event.

        Combines the currently-held sticky modifiers with any per-call
        modifiers so the target app sees a consistent chord regardless
        of whether the modifier is held or one-shot.  ``"win"`` →
        Command on macOS.
        """
        flags = 0
        for name in self._held_mods:
            info = _MOD_INFO.get(name)
            if info is not None:
                flags |= info[1]
        if modifiers:
            for name in modifiers:
                info = _MOD_INFO.get(name)
                if info is not None:
                    flags |= info[1]
        return flags

    def _post_keycode(self, keycode: int, flags: int) -> None:
        """Post key-down then key-up for *keycode* with *flags* on each."""
        self._log_frontmost_for_send(f"_post_keycode(kc={keycode}, flags={flags:#x})")
        Quartz = self._Quartz
        for is_down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(None, keycode, is_down)
            if ev is None:
                self._warn_untrusted_once()
                return
            if flags:
                Quartz.CGEventSetFlags(ev, flags)
            self._post_event(ev)

    def _post_modifier(self, keycode: int, key_down: bool) -> None:
        """Post a single modifier key event (no chord flags)."""
        Quartz = self._Quartz
        ev = Quartz.CGEventCreateKeyboardEvent(None, keycode, key_down)
        if ev is None:
            self._warn_untrusted_once()
            return
        self._post_event(ev)

    def _post_event(self, ev: Any) -> None:
        """Deliver *ev* to the target app, bypassing focus state.

        If we've observed a non-self app activate since launch, route
        via ``CGEventPostToPid`` to that pid — the event lands there
        regardless of who's frontmost at this exact moment.  This is
        the *only* path that reliably defeats macOS click-activation
        on a Qt window: Qt 6 dropped the ``Qt.Tool → NSPanel`` mapping
        for QQuickWindow, and ``NSWindowStyleMaskNonactivatingPanel``
        is a no-op on a plain NSWindow, so the OSK app itself takes
        the foreground when the user clicks a key.  Posting to the
        editor's pid directly sidesteps the whole foreground question.

        Falls back to ``CGEventPost`` only when ``_target_pid`` is
        still None — extremely rare now that ``__init__`` seeds it
        from ``os.getppid()`` (the launching terminal / Finder).  The
        only way this branch fires is if the parent pid was 1
        (launchd) and no other app has activated since launch.
        """
        Quartz = self._Quartz
        if self._target_pid is not None:
            Quartz.CGEventPostToPid(self._target_pid, ev)
        else:
            Quartz.CGEventPost(_KCG_HID_EVENT_TAP, ev)

    def _install_target_observer(self) -> None:
        """Track the last non-self app to become frontmost.

        Hooks ``NSWorkspaceDidActivateApplicationNotification`` on the
        shared workspace's notification center.  Each activation
        change fires the block below; we filter out our own pid and
        record everyone else.  By the time the user clicks an OSK
        key, ``_target_pid`` points at whichever editor / chat / etc.
        they last had focused — exactly the destination we want.

        Strong-references the block on ``self._activation_observer``
        because NSWorkspace's notification center stores it weakly.

        Best-effort: failure to install leaves ``_target_pid`` at None
        forever, in which case posts fall back to the focus-stealing
        ``CGEventPost`` path.  The synth still works in that mode, the
        user just sees keystrokes land in Alpha-OSK itself.
        """
        if self._NSWorkspace is None:
            return
        try:
            from AppKit import (  # type: ignore[import-not-found]
                NSWorkspaceApplicationKey,
                NSWorkspaceDidActivateApplicationNotification,
            )
        except ImportError as exc:
            _logger.warning(
                "Cannot import NSWorkspace activation symbols (%s); "
                "target-pid routing disabled (focus theft expected)",
                exc,
            )
            return

        def _on_activated(notification: Any) -> None:
            try:
                info = notification.userInfo()
                if info is None:
                    return
                app = info.objectForKey_(NSWorkspaceApplicationKey)
                if app is None:
                    return
                pid = int(app.processIdentifier())
                if pid == self._self_pid:
                    return
                if pid != self._target_pid:
                    name = str(app.localizedName() or "?")
                    _logger.info(
                        "Target app updated → %s (pid=%d) — events will "
                        "now post to this app via CGEventPostToPid",
                        name, pid,
                    )
                    self._target_pid = pid
            except Exception as exc:
                _logger.debug("Activation observer failed: %s", exc)

        try:
            nc = self._NSWorkspace.sharedWorkspace().notificationCenter()
            self._activation_observer = nc.addObserverForName_object_queue_usingBlock_(
                NSWorkspaceDidActivateApplicationNotification,
                None,
                None,
                _on_activated,
            )
            _logger.info(
                "Installed NSWorkspace activation observer (self pid=%d)",
                self._self_pid,
            )
        except Exception as exc:
            _logger.warning(
                "Failed to install NSWorkspace activation observer: %s",
                exc,
            )

    def _log_frontmost_for_send(self, send_label: str) -> None:
        """Diagnostic only: log who's frontmost when we post.

        Useful when re-debugging focus / delivery routing.  Both
        branches are DEBUG now that ``_post_event`` routes via
        ``CGEventPostToPid`` and frontmost state is irrelevant to
        actual delivery — keeping this at INFO would spam the log on
        every keystroke without telling the user anything they can
        act on.  Bump back to INFO temporarily if you're investigating
        a delivery regression.

        Best-effort — never raises into the caller.
        """
        if self._NSWorkspace is None or not _logger.isEnabledFor(logging.DEBUG):
            return
        try:
            app = self._NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                ident = "<none>"
            else:
                name = str(app.localizedName() or "?")
                bid = str(app.bundleIdentifier() or "?")
                ident = f"{name} ({bid})"
            _logger.debug(
                "POST %s → frontmost=%s, target_pid=%s",
                send_label, ident, self._target_pid,
            )
        except Exception as exc:
            _logger.debug("Frontmost probe failed: %s", exc)

    def _warn_untrusted_once(self) -> None:
        """Warn once if Accessibility looks denied.

        ``CGEventCreateKeyboardEvent`` returning None is unusual but
        possible under heavy sandboxing; the user-facing failure mode
        for *most* people is "the call succeeds but the post is
        dropped" — that one we can't detect post-hoc.  Either way,
        nudging the user toward Privacy & Security → Accessibility is
        the right next step.
        """
        if self._warned_untrusted:
            return
        self._warned_untrusted = True
        _logger.warning(
            "Quartz CGEvent allocation/post failed — Alpha-OSK may not "
            "have Accessibility permission.  Open System Settings → "
            "Privacy & Security → Accessibility and enable Alpha-OSK."
        )
