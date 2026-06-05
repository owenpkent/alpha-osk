#pragma once

#include <QObject>
#include <QString>
#include <QVariantMap>

#include <functional>

class QNetworkAccessManager;

// Opt-in, anonymous usage telemetry. Off by default. Port of src/telemetry.py.
// State (consent + anon_id + last submit) lives in telemetry.json. The build
// endpoint is EMPTY by default, so every submit silently no-ops and no data
// leaves the machine; the consent toggle + anon_id lifecycle still work.
// Never sends content -- only lifetime integer counters from the analytics
// provider. anon_id is cleared on opt-out so re-opt-in can't be linked.
class TelemetryClient : public QObject
{
    Q_OBJECT
public:
    TelemetryClient(std::function<QVariantMap()> analyticsProvider,
                    QString appVersion, QString osName, QObject *parent = nullptr);

    bool enabled() const { return m_enabled; }
    void enable();   // new anon_id if none; resets the submit clock to now
    void disable();  // clears anon_id

    bool maybeSubmit();   // weekly gate (1h tick calls this)
    bool submitOnQuit();  // last-chance on exit (60s anti-spam)
    bool forget();        // POST /v1/forget

private:
    void loadState();
    void saveState() const;
    QString statePath() const;
    QByteArray buildPayload() const;
    bool submitNow();
    void post(const QString &url, const QByteArray &body, bool updateTsOnOk);
    QNetworkAccessManager *nam();

    QString m_endpoint; // DEFAULT_ENDPOINT, empty -> all submits no-op
    std::function<QVariantMap()> m_provider;
    QString m_appVersion;
    QString m_osName;
    bool m_enabled = false;
    QString m_anonId;
    double m_lastSubmitTs = 0.0;
    QNetworkAccessManager *m_nam = nullptr;

    static constexpr double kSubmitIntervalSeconds = 7 * 24 * 60 * 60;
};
