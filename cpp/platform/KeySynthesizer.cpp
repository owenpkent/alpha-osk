#include "KeySynthesizer.h"
#include "WindowsKeySynthesizer.h"

#include <QtGlobal>

// A backend that does nothing, used on platforms without a port yet. The
// keyboard UI still works; key synthesis silently no-ops (isAvailable=false
// drives the "keystroke synthesis unavailable" warning in the UI).
class NullKeySynthesizer : public KeySynthesizer
{
public:
    bool isAvailable() const override { return false; }
    QString backendName() const override { return QStringLiteral("none"); }
    void sendKey(const QString &, const QStringList &, double) override {}
    void sendText(const QString &) override {}
    void sendCombination(const QStringList &) override {}
};

KeySynthesizer *createKeySynthesizer()
{
#ifdef Q_OS_WIN
    return new WindowsKeySynthesizer();
#else
    return new NullKeySynthesizer();
#endif
}
