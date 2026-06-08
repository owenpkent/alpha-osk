#include "PasswordDetect.h"

#include <QStringList>
#include <QtGlobal>

#ifdef Q_OS_WIN
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#  include <objbase.h>
#  include <oleauto.h>
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

    // Join the focused element's UIA RuntimeId (an int array UIA keeps stable
    // for the element's lifetime, unique on the desktop) into "a,b,c". Empty
    // string on any failure -> caller treats it as "don't know".
    QString focusToken()
    {
        if (!m_available || !m_automation)
            return QString();
        IUIAutomationElement *element = nullptr;
        if (FAILED(m_automation->GetFocusedElement(&element)) || !element)
            return QString();
        SAFEARRAY *psa = nullptr;
        QString token;
        if (SUCCEEDED(element->GetRuntimeId(&psa)) && psa) {
            LONG lb = 0, ub = -1;
            if (SUCCEEDED(SafeArrayGetLBound(psa, 1, &lb))
                && SUCCEEDED(SafeArrayGetUBound(psa, 1, &ub))) {
                QStringList parts;
                for (LONG i = lb; i <= ub; ++i) {
                    LONG idx = i;
                    int val = 0;
                    if (SUCCEEDED(SafeArrayGetElement(psa, &idx, &val)))
                        parts << QString::number(val);
                }
                token = parts.join(QLatin1Char(','));
            }
            SafeArrayDestroy(psa);
        }
        element->Release();
        return token;
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

QString passworddetect::focusToken()
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
    // Element-level identity needs UIA. The Win32 EM_GETPASSWORDCHAR fallback
    // can't enumerate web inputs, so there's no token without UIA — return
    // empty ("don't know") and let the foreground-window check carry the load.
    if (g_useUia && g_uia)
        return g_uia->focusToken();
    return QString();
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
QString passworddetect::focusToken() { return QString(); }
void passworddetect::shutdown() {}

#endif
