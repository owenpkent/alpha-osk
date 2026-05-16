"""macOS key-synthesis diagnostic.

Runs outside the OSK UI so we can isolate Quartz behaviour from Qt /
QML / window-flag interactions.  Prints every relevant signal:

  - Process identity (pid, executable path)
  - AXIsProcessTrusted() before send
  - Frontmost app immediately before each post
  - CGEventCreateKeyboardEvent return value (nil = creation failure)
  - CGEventPost return path (void, but at least we know the call
    didn't raise)
  - Frontmost app immediately after send (to spot focus theft)
  - Re-check trust state at end (sometimes TCC flips mid-session)

Then sends a known test pattern ("hello") to whichever app is
frontmost at the moment of the post.  A 4-second countdown gives you
time to alt-tab into TextEdit.

Usage::

    venv/bin/python scripts/mac_keysend_diag.py
"""
from __future__ import annotations

import os
import sys
import time


def banner(msg: str) -> None:
    print("\n" + "=" * 60)
    print(f"  {msg}")
    print("=" * 60)


def report_identity() -> None:
    banner("Process identity")
    print(f"  pid:        {os.getpid()}")
    print(f"  python:     {sys.executable}")
    print(f"  argv[0]:    {sys.argv[0]}")
    print(f"  cwd:        {os.getcwd()}")
    # TCC grants are keyed by the *bundle identifier or executable
    # path* of the process that requests them.  When running from a
    # venv, the grant lands on the terminal app that ran us.  If the
    # path here doesn't match the app you granted, that's the bug.


def report_trust(label: str) -> bool:
    try:
        from ApplicationServices import AXIsProcessTrusted
    except ImportError as exc:
        print(f"  [{label}] AXIsProcessTrusted: <ApplicationServices missing: {exc}>")
        return False
    val = bool(AXIsProcessTrusted())
    print(f"  [{label}] AXIsProcessTrusted: {val}")
    return val


def report_frontmost(label: str) -> str:
    try:
        from AppKit import NSWorkspace
    except ImportError:
        print(f"  [{label}] NSWorkspace: <unavailable>")
        return ""
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        print(f"  [{label}] frontmost: <none>")
        return ""
    name = str(app.localizedName() or "?")
    bid = str(app.bundleIdentifier() or "?")
    pid = int(app.processIdentifier())
    print(f"  [{label}] frontmost: {name!r}  bundleId={bid}  pid={pid}")
    return bid


def send_one(ch: str) -> None:
    """Post a single character via Unicode injection, verbosely."""
    from Quartz import (
        CGEventCreateKeyboardEvent,
        CGEventKeyboardSetUnicodeString,
        CGEventPost,
    )

    print(f"  send_one({ch!r}):")
    for is_down in (True, False):
        ev = CGEventCreateKeyboardEvent(None, 0, is_down)
        if ev is None:
            print(f"    keyDown={is_down}: CGEventCreateKeyboardEvent returned None "
                  "→ Quartz refused (likely TCC denied)")
            return
        CGEventKeyboardSetUnicodeString(ev, len(ch), ch)
        # kCGHIDEventTap = 0
        CGEventPost(0, ev)
        print(f"    keyDown={is_down}: posted (ev id={id(ev):#x})")


def main() -> int:
    report_identity()

    banner("Accessibility trust (before)")
    trusted_before = report_trust("pre")

    if not trusted_before:
        banner("NOT TRUSTED — likely cause of silent drops")
        print("  Grant this exact process's owning app in:")
        print("    System Settings → Privacy & Security → Accessibility")
        print("  Quitting + relaunching the terminal often picks up the grant.")
        print("  Continuing anyway so we can see what Quartz does...")

    banner("Countdown — switch focus to TextEdit NOW")
    for n in range(4, 0, -1):
        print(f"  {n}...")
        time.sleep(1)

    banner("Sending 'hello' one char at a time")
    report_frontmost("before send")
    for ch in "hello":
        send_one(ch)
        time.sleep(0.15)  # small gap so we can correlate logs with on-screen output
    report_frontmost("after send")

    banner("Accessibility trust (after)")
    report_trust("post")

    banner("Done — check the focused field")
    print("  If 'hello' appeared in TextEdit: Quartz path works, the OSK bug is")
    print("    in the bridge / Qt activation layer.")
    print("  If nothing appeared: Quartz / TCC is the blocker — the synthesizer")
    print("    is not reaching the system event stream regardless of UI.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
