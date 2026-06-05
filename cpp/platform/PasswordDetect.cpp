#include "PasswordDetect.h"

#include <QtGlobal>

#ifdef Q_OS_WIN
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#  include <objbase.h>
#  include <uiautomation.h>

namespace {

// UI Automation detector: query UIA_IsPasswordPropertyId on the focused element.
class UiaDetector
{
public:
    bool init()
    {
        // Match the GUI thread's apartment so we don't trip RPC_E_CHANGED_MODE.
        // S_OK -> we own COM; S_FALSE / RPC_E_CHANGED_MODE -> already inited by
        // Qt (we don't own, but COM is available); anything else is a failure.
        const HRESULT hr = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
        if (hr == S_OK)
            m_ownsCom = true;
        else if (hr != S_FALSE && hr != RPC_E_CHANGED_MODE)
            return false;

        const HRESULT cc = CoCreateInstance(CLSID_CUIAutomation, nullptr,
                                            CLSCTX_INPROC_SERVER, IID_IUIAutomation,
                                            reinterpret_cast<void **>(&m_automation));
        if (FAILED(cc) || !m_automation) {
            if (m_ownsCom) {
                CoUninitialize();
                m_ownsCom = false;
            }
            return false;
        }
        m_available = true;
        return true;
    }

    void close()
    {
        if (m_automation) {
            m_automation->Release();
            m_automation = nullptr;
        }
        m_available = false;
        if (m_ownsCom) {
            CoUninitialize();
            m_ownsCom = false;
        }
    }

    bool available() const { return m_available; }

    bool check()
    {
        if (!m_available || !m_automation)
            return false;
        IUIAutomationElement *element = nullptr;
        if (FAILED(m_automation->GetFocusedElement(&element)) || !element)
            return false;
        VARIANT v;
        VariantInit(&v);
        bool isPassword = false;
        if (SUCCEEDED(element->GetCurrentPropertyValue(UIA_IsPasswordPropertyId, &v)))
            isPassword = (v.vt == VT_BOOL && v.boolVal != VARIANT_FALSE);
        VariantClear(&v);
        element->Release();
        return isPassword;
    }

private:
    IUIAutomation *m_automation = nullptr;
    bool m_ownsCom = false;
    bool m_available = false;
};

// Win32 fallback: classic password edit controls answer EM_GETPASSWORDCHAR.
bool win32Check()
{
    HWND hwnd = GetForegroundWindow();
    if (!hwnd)
        return false;
    const DWORD tid = GetWindowThreadProcessId(hwnd, nullptr);
    if (!tid)
        return false;
    GUITHREADINFO info{};
    info.cbSize = sizeof(info);
    if (!GetGUIThreadInfo(tid, &info) || !info.hwndFocus)
        return false;
    const LRESULT r = SendMessageW(info.hwndFocus, EM_GETPASSWORDCHAR, 0, 0);
    return r != 0;
}

UiaDetector *g_uia = nullptr;
bool g_initTried = false;
bool g_useUia = false;

} // namespace

bool passworddetect::isPasswordField()
{
    if (!g_initTried) {
        g_initTried = true;
        g_uia = new UiaDetector();
        if (g_uia->init()) {
            g_useUia = true;
        } else {
            g_uia->close();
            delete g_uia;
            g_uia = nullptr;
        }
    }
    if (g_useUia && g_uia)
        return g_uia->check();
    return win32Check();
}

void passworddetect::shutdown()
{
    if (g_uia) {
        g_uia->close();
        delete g_uia;
        g_uia = nullptr;
    }
    g_useUia = false;
    g_initTried = false;
}

#else // non-Windows: no auto-detection (manual privacy toggle still works)

bool passworddetect::isPasswordField() { return false; }
void passworddetect::shutdown() {}

#endif
