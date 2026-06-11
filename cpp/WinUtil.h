#pragma once

#include <QtGlobal>

class QWindow;

// Windows-specific window tuning. No-ops on other platforms.
namespace winutil {

// The foreground window handle as an integer (0 if unavailable / non-Windows).
// Used only to detect app switches, so the exact value doesn't matter.
quintptr foregroundWindowId();

// True if the window's class or owning-process exe matches the curated
// remote-desktop / IDE whitelist that needs compatibility-mode insertion
// (BackSpace+retype rather than suffix-only). False on non-Windows / on error.
bool windowNeedsCompatMode(quintptr hwnd);

// True if the window's owning-process exe is a known polling game (Age of
// Empires, ...). Games read the keyboard by polling state per render frame, so
// a zero-gap key-down+key-up can be missed; single keys are held down briefly
// in this case. False on non-Windows / on error.
bool windowIsGame(quintptr hwnd);

// Tag the process so the taskbar keeps our icon. Must run before the first
// top-level window is created. (SetCurrentProcessExplicitAppUserModelID)
void setAppUserModelId();

// Apply WS_EX_NOACTIVATE | WS_EX_TOPMOST to a shown window so it never steals
// focus, then force a frame refresh. Re-apply each time a window becomes
// visible (the native handle only exists once shown).
void applyWindowsExtendedStyles(QWindow *window);

} // namespace winutil
