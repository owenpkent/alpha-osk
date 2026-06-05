#pragma once

class QWindow;

// Windows-specific window tuning. No-ops on other platforms.
namespace winutil {

// Tag the process so the taskbar keeps our icon. Must run before the first
// top-level window is created. (SetCurrentProcessExplicitAppUserModelID)
void setAppUserModelId();

// Apply WS_EX_NOACTIVATE | WS_EX_TOPMOST to a shown window so it never steals
// focus, then force a frame refresh. Re-apply each time a window becomes
// visible (the native handle only exists once shown).
void applyWindowsExtendedStyles(QWindow *window);

} // namespace winutil
