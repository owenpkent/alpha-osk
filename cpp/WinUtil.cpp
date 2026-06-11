#include "WinUtil.h"

#include <QFileInfo>
#include <QSet>
#include <QString>
#include <QWindow>

#ifdef Q_OS_WIN
#  include <windows.h>
#  include <shobjidl.h>
#endif

namespace winutil {

#ifdef Q_OS_WIN
namespace {

// Remote-desktop client window classes (exact match).
const QSet<QString> &compatWindowClasses()
{
    static const QSet<QString> s = {
        "TscShellContainerClass", "RDPViewer", "UIMainClass",       // MS RDP
        "TV_TitleBar", "TV_Client", "TV_FullScreen", "#32770TVMainForm", // TeamViewer
        "AnyDeskMainWindow", "AnyDeskMainView",
        "TightVNCClassName", "VNCMDI_Window", "VNCviewer", "RealVNCClass",
        "UltraVNCClass", "TVNVncCtrl",
        "RustDesk", "ParsecHostWindow", "SplashtopRemoteDesktopClass"};
    return s;
}

// Owning-process exe basenames (lowercase, exact match): remote-desktop tools +
// IDEs whose editors intercept keystrokes (VS Code / Monaco forks, JetBrains).
const QSet<QString> &compatProcessNames()
{
    static const QSet<QString> s = {
        "teamviewer.exe", "tv_w32.exe", "tv_x64.exe", "mstsc.exe", "msrdc.exe",
        "anydesk.exe", "vncviewer.exe", "tvnviewer.exe", "uvnc.exe", "winvnc.exe",
        "rustdesk.exe", "splashtop.exe", "stp.exe", "logmein.exe", "parsecd.exe",
        "moonlight.exe",
        // VS Code + Monaco forks
        "code.exe", "code - insiders.exe", "cursor.exe", "windsurf.exe",
        "codium.exe", "code-oss.exe", "positron.exe", "trae.exe",
        // JetBrains family
        "idea64.exe", "pycharm64.exe", "webstorm64.exe", "phpstorm64.exe",
        "clion64.exe", "goland64.exe", "rider64.exe", "rubymine64.exe",
        "datagrip64.exe", "dataspell64.exe", "studio64.exe", "studio.exe"};
    return s;
}

// Owning-process exe basenames (lowercase, exact match) of games whose
// foreground window should switch key synthesis to the held down/up path.
// Games poll keyboard state per render frame, so a zero-gap key-down+key-up
// injected in one SendInput batch can land between two polls and be missed.
// Extend this set as reports of other unresponsive games come in.
const QSet<QString> &gameProcessNames()
{
    static const QSet<QString> s = {
        "aoe2de_s.exe",       // Age of Empires II: Definitive Edition
        "aoe3de_s.exe",       // Age of Empires III: Definitive Edition
        "aoede_s.exe",        // Age of Empires: Definitive Edition
        "reliccardinal.exe",  // Age of Empires IV
        "age2_x1.exe",        // Age of Empires II: The Conquerors (classic)
        "age2_x2.exe",        // AoE II HD: Forgotten Empires
        "aoe2hd.exe",         // Age of Empires II: HD Edition
        "empires2.exe"};      // Age of Empires II (original)
    return s;
}

// Lowercased basename of hwnd's owning-process exe, or empty on failure.
QString owningExeName(HWND hwnd)
{
    DWORD pid = 0;
    GetWindowThreadProcessId(hwnd, &pid);
    if (!pid)
        return QString();
    HANDLE handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!handle)
        return QString();
    QString name;
    wchar_t exe[512] = {0};
    DWORD size = 512;
    if (QueryFullProcessImageNameW(handle, 0, exe, &size))
        name = QFileInfo(QString::fromWCharArray(exe)).fileName().toLower();
    CloseHandle(handle);
    return name;
}

// A borderless window covering its whole monitor (no title bar, rect spans the
// entire monitor including the taskbar strip): the catch-all shape of an
// unlisted borderless / exclusive-fullscreen game. A normal maximized window
// keeps WS_CAPTION and leaves the taskbar visible, so it doesn't match.
bool windowIsBorderlessFullscreen(HWND hwnd)
{
    RECT rect;
    if (!GetWindowRect(hwnd, &rect))
        return false;
    HMONITOR mon = MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST);
    if (!mon)
        return false;
    MONITORINFO mi;
    mi.cbSize = sizeof(mi);
    if (!GetMonitorInfoW(mon, &mi))
        return false;
    const RECT &m = mi.rcMonitor;
    const bool covers = rect.left <= m.left && rect.top <= m.top
                     && rect.right >= m.right && rect.bottom >= m.bottom;
    if (!covers)
        return false;
    const LONG style = GetWindowLongW(hwnd, GWL_STYLE);
    return (style & WS_CAPTION) == 0;
}

} // namespace
#endif

quintptr winutil::foregroundWindowId()
{
#ifdef Q_OS_WIN
    return reinterpret_cast<quintptr>(GetForegroundWindow());
#else
    return 0;
#endif
}

bool winutil::windowNeedsCompatMode(quintptr hwndInt)
{
#ifdef Q_OS_WIN
    HWND hwnd = reinterpret_cast<HWND>(hwndInt);
    if (!hwnd)
        return false;
    wchar_t cls[256] = {0};
    if (GetClassNameW(hwnd, cls, 256) > 0
        && compatWindowClasses().contains(QString::fromWCharArray(cls)))
        return true;

    DWORD pid = 0;
    GetWindowThreadProcessId(hwnd, &pid);
    if (!pid)
        return false;
    HANDLE handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!handle)
        return false;
    bool match = false;
    wchar_t exe[512] = {0};
    DWORD size = 512;
    if (QueryFullProcessImageNameW(handle, 0, exe, &size)) {
        const QString name = QFileInfo(QString::fromWCharArray(exe)).fileName().toLower();
        match = compatProcessNames().contains(name);
    }
    CloseHandle(handle);
    return match;
#else
    Q_UNUSED(hwndInt);
    return false;
#endif
}

bool winutil::windowIsGame(quintptr hwndInt)
{
#ifdef Q_OS_WIN
    HWND hwnd = reinterpret_cast<HWND>(hwndInt);
    if (!hwnd)
        return false;
    // 1. Exe in the game list (catches games even in windowed mode).
    const QString exe = owningExeName(hwnd);
    if (!exe.isEmpty()) {
        if (gameProcessNames().contains(exe))
            return true;
        // 2. Productivity apps (IDEs / remote desktop) are sometimes run
        // fullscreen; don't let the heuristic add a key-hold that would lag
        // normal typing there.
        if (compatProcessNames().contains(exe))
            return false;
    }
    // 3. Borderless-fullscreen heuristic: zero-config catch-all for unlisted
    // games.
    return windowIsBorderlessFullscreen(hwnd);
#else
    Q_UNUSED(hwndInt);
    return false;
#endif
}

void setAppUserModelId()
{
#ifdef Q_OS_WIN
    SetCurrentProcessExplicitAppUserModelID(L"OKStudio.AlphaOSK");
#endif
}

void applyWindowsExtendedStyles(QWindow *window)
{
#ifdef Q_OS_WIN
    if (!window)
        return;
    HWND hwnd = reinterpret_cast<HWND>(window->winId());
    if (!hwnd)
        return;

    // GetWindowLong returns 0 both for "style is 0" and for error -> disambiguate
    // via SetLastError(0) + GetLastError per MSDN.
    SetLastError(0);
    LONG current = GetWindowLongW(hwnd, GWL_EXSTYLE);
    if (current == 0 && GetLastError() != 0)
        return;

    const LONG updated = current | WS_EX_NOACTIVATE | WS_EX_TOPMOST;
    SetLastError(0);
    const LONG prev = SetWindowLongW(hwnd, GWL_EXSTYLE, updated);
    if (prev == 0 && GetLastError() != 0)
        return;

    // SWP_FRAMECHANGED forces the new ex-style to take effect; without it
    // WS_EX_NOACTIVATE may not apply and clicks steal focus before SendInput.
    SetWindowPos(hwnd, nullptr, 0, 0, 0, 0,
                 SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED);
#else
    Q_UNUSED(window);
#endif
}

} // namespace winutil
