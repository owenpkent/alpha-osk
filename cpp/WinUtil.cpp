#include "WinUtil.h"

#include <QWindow>

#ifdef Q_OS_WIN
#  include <windows.h>
#  include <shobjidl.h>
#endif

namespace winutil {

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
