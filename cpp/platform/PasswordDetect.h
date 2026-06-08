#pragma once

#include <QString>

// Detects whether the currently focused UI element is a password field, so the
// keyboard can suppress prediction + learning for sensitive input. Port of
// src/platform/password_detect.py (Windows paths): UI Automation
// (UIA_IsPasswordPropertyId on the focused element, works for native controls
// and browsers) with a Win32 EM_GETPASSWORDCHAR fallback.
namespace passworddetect {

// True iff focus is currently on a password field. Cheap; returns false on any
// internal failure rather than throwing. Lazily initialises the detector.
bool isPasswordField();

// Stable identity string for the currently focused UI element (its UIA
// RuntimeId), or an empty string if unknown. Finer-grained than the foreground
// window handle: two text boxes inside the same browser window are different
// elements with different tokens, while the caret moving within one box keeps
// the same token. Lets callers notice focus moving between controls in the
// same window. Empty on non-Windows / UIA-unavailable / on any failure, which
// callers must treat as "don't know" (leave state untouched).
QString focusToken();

// Release the UIA COM interface + CoInitialize token. Safe to call repeatedly.
void shutdown();

} // namespace passworddetect
