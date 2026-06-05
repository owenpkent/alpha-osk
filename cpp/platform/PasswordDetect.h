#pragma once

// Detects whether the currently focused UI element is a password field, so the
// keyboard can suppress prediction + learning for sensitive input. Port of
// src/platform/password_detect.py (Windows paths): UI Automation
// (UIA_IsPasswordPropertyId on the focused element, works for native controls
// and browsers) with a Win32 EM_GETPASSWORDCHAR fallback.
namespace passworddetect {

// True iff focus is currently on a password field. Cheap; returns false on any
// internal failure rather than throwing. Lazily initialises the detector.
bool isPasswordField();

// Release the UIA COM interface + CoInitialize token. Safe to call repeatedly.
void shutdown();

} // namespace passworddetect
