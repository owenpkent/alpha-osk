#pragma once

#include "KeySynthesizer.h"

#include <optional>
#include <vector>

#ifdef Q_OS_WIN
#  include <windows.h>
#endif

// Windows key synthesis via Win32 SendInput. Behavior-identical port of
// src/platform/windows.py (WindowsKeySynthesizer).
//
// Design bias (see docs): prefer scancode-mode events for ASCII and chords so
// input reaches apps that filter on real VKs / read raw scancodes (Blender,
// games, CAD) and relays correctly over remote-desktop tools (RDP/TeamViewer),
// which forward by scancode. Unicode mode (VK_PACKET) is strictly a fallback.
class WindowsKeySynthesizer : public KeySynthesizer
{
public:
    WindowsKeySynthesizer();

    bool isAvailable() const override;
    QString backendName() const override;

    void sendKey(const QString &keyName, const QStringList &modifiers = {},
                 double holdSeconds = 0.0) override;
    void sendText(const QString &text) override;
    void sendCombination(const QStringList &keys) override;
    void holdModifier(const QString &keyName) override;
    void releaseModifier(const QString &keyName) override;
    void replaceText(int backspaceCount, const QString &text) override;

private:
#ifdef Q_OS_WIN
    bool m_uiAccess = false;

    // name -> VK, and the extended-key set.
    static int resolveVk(const QString &keyName);                 // -1 if none
    // Punctuation single-char fallback for chords. Returns false if unresolved.
    static bool resolveCharVk(QChar ch, int &vk, bool &needsShift);
    static bool isExtendedVk(int vk);

    // Decide whether scancode mode is safe for this char and how to send it.
    struct ScanResolve { int vk; UINT scancode; bool needsShift; };
    static std::optional<ScanResolve> resolveCharScancode(QChar ch);

    // INPUT builders (the three modes).
    static INPUT makeScancodeEvent(UINT scancode, bool keyDown);
    static INPUT makeVkScancodeEvent(int vk, bool keyDown);
    static INPUT makeKeyEvent(int vk, bool keyDown);
    static std::vector<INPUT> makeUnicodeEvents(uint codepoint);
    // Returns the scancode-mode events for one char, or empty if it must go
    // Unicode (caller falls back).
    static std::vector<INPUT> makeCharScancodeEvents(QChar ch, bool &ok);

    static std::vector<INPUT> typedEventsFor(const QString &text);
    static void inject(const std::vector<INPUT> &events);

    static QString foregroundWindowClass();
    static bool isTerminalForeground();

    static bool checkUiAccess();
#endif
};
