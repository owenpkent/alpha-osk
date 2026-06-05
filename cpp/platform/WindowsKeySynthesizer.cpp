#include "WindowsKeySynthesizer.h"

#include <QDebug>
#include <QHash>

WindowsKeySynthesizer::WindowsKeySynthesizer()
{
#ifdef Q_OS_WIN
    m_uiAccess = checkUiAccess();
#endif
}

#ifdef Q_OS_WIN

// ----- name -> VK table (exact port of _KEY_MAP) -------------------------

static const QHash<QString, int> &keyMap()
{
    static const QHash<QString, int> m = {
        // Special / editing
        {QStringLiteral("BackSpace"), VK_BACK},
        {QStringLiteral("Tab"), VK_TAB},
        {QStringLiteral("Return"), VK_RETURN},
        {QStringLiteral("Escape"), VK_ESCAPE},
        {QStringLiteral("space"), VK_SPACE},
        {QStringLiteral("Delete"), VK_DELETE},
        {QStringLiteral("Insert"), VK_INSERT},
        // Navigation
        {QStringLiteral("Left"), VK_LEFT},
        {QStringLiteral("Right"), VK_RIGHT},
        {QStringLiteral("Up"), VK_UP},
        {QStringLiteral("Down"), VK_DOWN},
        {QStringLiteral("Home"), VK_HOME},
        {QStringLiteral("End"), VK_END},
        {QStringLiteral("Page_Up"), VK_PRIOR},
        {QStringLiteral("Page_Down"), VK_NEXT},
        // Function keys F1..F12 = 0x70..0x7B
        {QStringLiteral("F1"), VK_F1},   {QStringLiteral("F2"), VK_F2},
        {QStringLiteral("F3"), VK_F3},   {QStringLiteral("F4"), VK_F4},
        {QStringLiteral("F5"), VK_F5},   {QStringLiteral("F6"), VK_F6},
        {QStringLiteral("F7"), VK_F7},   {QStringLiteral("F8"), VK_F8},
        {QStringLiteral("F9"), VK_F9},   {QStringLiteral("F10"), VK_F10},
        {QStringLiteral("F11"), VK_F11}, {QStringLiteral("F12"), VK_F12},
        // Lock / misc
        {QStringLiteral("Num_Lock"), VK_NUMLOCK},
        {QStringLiteral("Scroll_Lock"), VK_SCROLL},
        {QStringLiteral("Pause"), VK_PAUSE},
        {QStringLiteral("Print"), VK_SNAPSHOT},
        {QStringLiteral("Caps_Lock"), VK_CAPITAL},
        // Modifiers
        {QStringLiteral("ctrl"), VK_CONTROL},
        {QStringLiteral("alt"), VK_MENU},
        {QStringLiteral("shift"), VK_SHIFT},
        {QStringLiteral("win"), VK_LWIN},
        {QStringLiteral("super"), VK_LWIN},
    };
    return m;
}

bool WindowsKeySynthesizer::isExtendedVk(int vk)
{
    switch (vk) {
    case VK_INSERT: case VK_DELETE: case VK_HOME: case VK_END:
    case VK_PRIOR:  case VK_NEXT:
    case VK_LEFT:   case VK_RIGHT: case VK_UP:   case VK_DOWN:
    case VK_SNAPSHOT: case VK_LWIN: case VK_NUMLOCK:
        return true;
    default:
        return false;
    }
}

int WindowsKeySynthesizer::resolveVk(const QString &keyName)
{
    auto it = keyMap().constFind(keyName);
    if (it != keyMap().constEnd())
        return it.value();
    if (keyName.size() == 1) {
        const ushort c = keyName.at(0).toUpper().unicode();
        if ((c >= 0x41 && c <= 0x5A) || (c >= 0x30 && c <= 0x39))
            return static_cast<int>(c); // VK == uppercase ASCII for A-Z / 0-9
    }
    return -1;
}

bool WindowsKeySynthesizer::resolveCharVk(QChar ch, int &vk, bool &needsShift)
{
    const SHORT raw = VkKeyScanW(static_cast<WCHAR>(ch.unicode()));
    if (raw == -1)
        return false;
    vk = raw & 0xFF;
    if (vk == 0xFF)
        return false;
    const int shiftState = (raw >> 8) & 0xFF;
    if (shiftState & 0x6) // bit1 Ctrl | bit2 Alt -> AltGr, too exotic
        return false;
    needsShift = (shiftState & 1) != 0;
    return true;
}

std::optional<WindowsKeySynthesizer::ScanResolve>
WindowsKeySynthesizer::resolveCharScancode(QChar ch)
{
    const ushort c = ch.unicode();
    if (c == 0)
        return std::nullopt;
    if (c >= 0x80) // non-ASCII always goes Unicode
        return std::nullopt;
    const SHORT raw = VkKeyScanW(static_cast<WCHAR>(c));
    if (raw == -1)
        return std::nullopt;
    const int vk = raw & 0xFF;
    if (vk == 0 || vk == 0xFF)
        return std::nullopt;
    const int shiftState = (raw >> 8) & 0xFF;
    if (shiftState & 0x6) // AltGr / bare-Ctrl chars go Unicode
        return std::nullopt;
    const bool layoutShift = (shiftState & 1) != 0;

    // Dead-key trigger? Arming a dead key would eat the next keypress.
    const UINT deadProbe = MapVirtualKeyW(static_cast<UINT>(vk), MAPVK_VK_TO_CHAR);
    if (deadProbe & 0x80000000u)
        return std::nullopt;

    const UINT scancode = MapVirtualKeyW(static_cast<UINT>(vk), MAPVK_VK_TO_VSC);
    if (scancode == 0)
        return std::nullopt;

    bool needsShift = layoutShift;
    // Caps Lock XORs Shift for letters only.
    if (ch.isLetter() && (GetKeyState(VK_CAPITAL) & 1))
        needsShift = !needsShift;

    // If the user physically holds Shift but this char doesn't want it, we
    // can't release their key -> Unicode bypasses shift state entirely.
    const bool shiftHeld = (GetAsyncKeyState(VK_SHIFT) & 0x8000) != 0;
    if (shiftHeld && !needsShift)
        return std::nullopt;

    return ScanResolve{vk, scancode, needsShift};
}

// ----- INPUT builders ----------------------------------------------------

INPUT WindowsKeySynthesizer::makeScancodeEvent(UINT scancode, bool keyDown)
{
    INPUT in{};
    in.type = INPUT_KEYBOARD;
    in.ki.wVk = 0;
    in.ki.wScan = static_cast<WORD>(scancode);
    in.ki.dwFlags = KEYEVENTF_SCANCODE | (keyDown ? 0 : KEYEVENTF_KEYUP);
    in.ki.time = 0;
    in.ki.dwExtraInfo = 0;
    return in;
}

INPUT WindowsKeySynthesizer::makeVkScancodeEvent(int vk, bool keyDown)
{
    const UINT scancode = MapVirtualKeyW(static_cast<UINT>(vk), MAPVK_VK_TO_VSC);
    if (scancode == 0) // no scancode under active layout -> VK-mode fallback
        return makeKeyEvent(vk, keyDown);
    INPUT in{};
    in.type = INPUT_KEYBOARD;
    in.ki.wVk = 0;
    in.ki.wScan = static_cast<WORD>(scancode);
    DWORD flags = KEYEVENTF_SCANCODE;
    if (!keyDown)
        flags |= KEYEVENTF_KEYUP;
    if (isExtendedVk(vk))
        flags |= KEYEVENTF_EXTENDEDKEY;
    in.ki.dwFlags = flags;
    in.ki.time = 0;
    in.ki.dwExtraInfo = 0;
    return in;
}

INPUT WindowsKeySynthesizer::makeKeyEvent(int vk, bool keyDown)
{
    INPUT in{};
    in.type = INPUT_KEYBOARD;
    in.ki.wVk = static_cast<WORD>(vk);
    in.ki.wScan = static_cast<WORD>(MapVirtualKeyW(static_cast<UINT>(vk), MAPVK_VK_TO_VSC));
    DWORD flags = 0;
    if (!keyDown)
        flags |= KEYEVENTF_KEYUP;
    if (isExtendedVk(vk))
        flags |= KEYEVENTF_EXTENDEDKEY;
    in.ki.dwFlags = flags;
    in.ki.time = 0;
    in.ki.dwExtraInfo = 0;
    return in;
}

std::vector<INPUT> WindowsKeySynthesizer::makeUnicodeEvents(uint codepoint)
{
    std::vector<INPUT> ev;
    auto pushUnit = [&ev](WORD unit) {
        for (bool up : {false, true}) {
            INPUT in{};
            in.type = INPUT_KEYBOARD;
            in.ki.wVk = 0;
            in.ki.wScan = unit;
            in.ki.dwFlags = KEYEVENTF_UNICODE | (up ? KEYEVENTF_KEYUP : 0);
            in.ki.time = 0;
            in.ki.dwExtraInfo = 0;
            ev.push_back(in);
        }
    };
    if (codepoint <= 0xFFFF) {
        pushUnit(static_cast<WORD>(codepoint));
    } else {
        const uint v = codepoint - 0x10000;
        pushUnit(static_cast<WORD>(0xD800 + (v >> 10)));
        pushUnit(static_cast<WORD>(0xDC00 + (v & 0x3FF)));
    }
    return ev;
}

std::vector<INPUT> WindowsKeySynthesizer::makeCharScancodeEvents(QChar ch, bool &ok)
{
    const auto resolved = resolveCharScancode(ch);
    if (!resolved) {
        ok = false;
        return {};
    }
    const bool shiftAlready = (GetAsyncKeyState(VK_SHIFT) & 0x8000) != 0;
    const bool wrap = resolved->needsShift && !shiftAlready;

    std::vector<INPUT> ev;
    if (wrap) {
        const UINT shiftSc = MapVirtualKeyW(VK_SHIFT, MAPVK_VK_TO_VSC);
        if (shiftSc == 0) { // don't send a half chord
            ok = false;
            return {};
        }
        ev.push_back(makeScancodeEvent(shiftSc, true));
    }
    ev.push_back(makeScancodeEvent(resolved->scancode, true));
    ev.push_back(makeScancodeEvent(resolved->scancode, false));
    if (wrap) {
        const UINT shiftSc = MapVirtualKeyW(VK_SHIFT, MAPVK_VK_TO_VSC);
        if (shiftSc == 0)
            ev.push_back(makeKeyEvent(VK_SHIFT, false)); // VK-mode safety release
        else
            ev.push_back(makeScancodeEvent(shiftSc, false));
    }
    ok = true;
    return ev;
}

std::vector<INPUT> WindowsKeySynthesizer::typedEventsFor(const QString &text)
{
    std::vector<INPUT> events;
    const QList<uint> cps = text.toUcs4();
    for (uint cp : cps) {
        if (cp <= 0xFFFF) {
            bool ok = false;
            std::vector<INPUT> e = makeCharScancodeEvents(QChar(static_cast<ushort>(cp)), ok);
            if (ok) {
                events.insert(events.end(), e.begin(), e.end());
                continue;
            }
        }
        std::vector<INPUT> u = makeUnicodeEvents(cp);
        events.insert(events.end(), u.begin(), u.end());
    }
    return events;
}

void WindowsKeySynthesizer::inject(const std::vector<INPUT> &events)
{
    if (events.empty())
        return;
    const UINT n = static_cast<UINT>(events.size());
    const UINT sent = SendInput(n, const_cast<INPUT *>(events.data()), sizeof(INPUT));
    if (sent != n) {
        qWarning() << "SendInput injected" << sent << "of" << n
                   << "events (error=" << GetLastError()
                   << ") -- may need UIAccess/admin for the target window";
    }
}

QString WindowsKeySynthesizer::foregroundWindowClass()
{
    HWND hwnd = GetForegroundWindow();
    if (!hwnd)
        return QString();
    wchar_t buf[256] = {0};
    const int len = GetClassNameW(hwnd, buf, 256);
    if (len <= 0)
        return QString();
    return QString::fromWCharArray(buf, len);
}

bool WindowsKeySynthesizer::isTerminalForeground()
{
    const QString cls = foregroundWindowClass();
    return cls == QLatin1String("ConsoleWindowClass")
        || cls == QLatin1String("CASCADIA_HOSTING_WINDOW_CLASS")
        || cls == QLatin1String("mintty");
}

bool WindowsKeySynthesizer::checkUiAccess()
{
    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token))
        return false;
    DWORD uiAccess = 0;
    DWORD retLen = 0;
    const BOOL ok = GetTokenInformation(token, static_cast<TOKEN_INFORMATION_CLASS>(26 /*TokenUIAccess*/),
                                        &uiAccess, sizeof(uiAccess), &retLen);
    CloseHandle(token);
    return ok && uiAccess != 0;
}

// ----- public API --------------------------------------------------------

bool WindowsKeySynthesizer::isAvailable() const
{
    return true; // SendInput is always present.
}

QString WindowsKeySynthesizer::backendName() const
{
    return m_uiAccess ? QStringLiteral("SendInput+UIAccess") : QStringLiteral("SendInput");
}

void WindowsKeySynthesizer::sendKey(const QString &keyName, const QStringList &modifiers)
{
    QStringList mods = modifiers;
    int vk = resolveVk(keyName);
    bool unicodeFallback = false;

    if (vk < 0 && keyName.size() == 1) {
        int cvk = 0;
        bool needsShift = false;
        if (resolveCharVk(keyName.at(0), cvk, needsShift)) {
            vk = cvk;
            if (needsShift && !mods.contains(QStringLiteral("shift")))
                mods.prepend(QStringLiteral("shift"));
        } else {
            unicodeFallback = true;
        }
    } else if (vk < 0) {
        qWarning() << "send_key: unknown key name" << keyName;
        return;
    }

    std::vector<INPUT> events;
    // Press modifiers (scancode mode -> relays over remote desktop).
    for (const QString &mod : mods) {
        const int mvk = keyMap().value(mod, -1);
        if (mvk >= 0)
            events.push_back(makeVkScancodeEvent(mvk, true));
    }
    // Action key.
    if (unicodeFallback) {
        std::vector<INPUT> u = makeUnicodeEvents(keyName.at(0).unicode());
        events.insert(events.end(), u.begin(), u.end());
    } else {
        events.push_back(makeVkScancodeEvent(vk, true));
        events.push_back(makeVkScancodeEvent(vk, false));
    }
    // Release modifiers in reverse.
    for (auto it = mods.crbegin(); it != mods.crend(); ++it) {
        const int mvk = keyMap().value(*it, -1);
        if (mvk >= 0)
            events.push_back(makeVkScancodeEvent(mvk, false));
    }
    inject(events);
}

void WindowsKeySynthesizer::sendText(const QString &text)
{
    if (text.isEmpty())
        return;
    inject(typedEventsFor(text));
}

void WindowsKeySynthesizer::sendCombination(const QStringList &keys)
{
    if (keys.isEmpty())
        return;
    std::vector<INPUT> events;
    for (const QString &key : keys) {
        const int vk = resolveVk(key);
        if (vk >= 0)
            events.push_back(makeKeyEvent(vk, true)); // VK mode (matches Python)
    }
    for (auto it = keys.crbegin(); it != keys.crend(); ++it) {
        const int vk = resolveVk(*it);
        if (vk >= 0)
            events.push_back(makeKeyEvent(vk, false));
    }
    inject(events);
}

void WindowsKeySynthesizer::holdModifier(const QString &keyName)
{
    const int vk = keyMap().value(keyName, -1);
    if (vk >= 0)
        inject({makeVkScancodeEvent(vk, true)});
}

void WindowsKeySynthesizer::releaseModifier(const QString &keyName)
{
    const int vk = keyMap().value(keyName, -1);
    if (vk >= 0)
        inject({makeVkScancodeEvent(vk, false)});
}

void WindowsKeySynthesizer::replaceText(int backspaceCount, const QString &text)
{
    std::vector<INPUT> events;
    if (isTerminalForeground()) {
        // Terminal: Shift+Left moves rather than selects -> BackSpace + retype.
        for (int i = 0; i < backspaceCount; ++i) {
            events.push_back(makeKeyEvent(VK_BACK, true));
            events.push_back(makeKeyEvent(VK_BACK, false));
        }
    } else {
        // Default: Shift+Left selection then overwrite (keeps compose areas open).
        if (backspaceCount > 0) {
            events.push_back(makeKeyEvent(VK_SHIFT, true));
            for (int i = 0; i < backspaceCount; ++i) {
                events.push_back(makeKeyEvent(VK_LEFT, true));
                events.push_back(makeKeyEvent(VK_LEFT, false));
            }
            events.push_back(makeKeyEvent(VK_SHIFT, false));
        }
    }
    std::vector<INPUT> typed = typedEventsFor(text);
    events.insert(events.end(), typed.begin(), typed.end());
    inject(events);
}

#else // ----- non-Windows stubs (factory does not create this off Windows) --

bool WindowsKeySynthesizer::isAvailable() const { return false; }
QString WindowsKeySynthesizer::backendName() const { return QStringLiteral("none"); }
void WindowsKeySynthesizer::sendKey(const QString &, const QStringList &) {}
void WindowsKeySynthesizer::sendText(const QString &) {}
void WindowsKeySynthesizer::sendCombination(const QStringList &) {}
void WindowsKeySynthesizer::holdModifier(const QString &) {}
void WindowsKeySynthesizer::releaseModifier(const QString &) {}
void WindowsKeySynthesizer::replaceText(int, const QString &) {}

#endif
