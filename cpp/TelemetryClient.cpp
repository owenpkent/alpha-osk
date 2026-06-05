#include "TelemetryClient.h"

#include "Paths.h"

#include <QDateTime>
#include <QDebug>
#include <QDir>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QUrl>
#include <QUuid>

#include <algorithm>

namespace {
// Empty by default: while empty the client no-ops every submit (no network).
// Set per-build before shipping a telemetry-enabled release.
const QString kDefaultEndpoint = QString();

constexpr long long kMaxKeystrokes = 1000000000LL;
constexpr long long kMaxWords = 200000000LL;
constexpr long long kMaxSessions = 10000000LL;
constexpr double kMaxMinutes = 5000000.0;

double nowSecs()
{
    return QDateTime::currentMSecsSinceEpoch() / 1000.0;
}

long long clampInt(const QVariantMap &m, const char *key, long long cap)
{
    const long long v = static_cast<long long>(m.value(key, 0).toDouble());
    return std::max(0LL, std::min(cap, v));
}
} // namespace

TelemetryClient::TelemetryClient(std::function<QVariantMap()> analyticsProvider,
                                 QString appVersion, QString osName, QObject *parent)
    : QObject(parent)
    , m_endpoint(kDefaultEndpoint)
    , m_provider(std::move(analyticsProvider))
    , m_appVersion(std::move(appVersion))
    , m_osName(std::move(osName))
{
    if (m_endpoint.endsWith('/'))
        m_endpoint.chop(1);
    loadState();
}

QString TelemetryClient::statePath() const
{
    return QDir(paths::configDir()).filePath("telemetry.json");
}

void TelemetryClient::loadState()
{
    QFile f(statePath());
    if (!f.exists() || !f.open(QIODevice::ReadOnly))
        return;
    QJsonParseError err{};
    const QJsonDocument doc = QJsonDocument::fromJson(f.readAll(), &err);
    if (err.error != QJsonParseError::NoError || !doc.isObject())
        return;
    const QJsonObject o = doc.object();
    m_enabled = o.value("enabled").toBool(false);
    const QString anon = o.value("anon_id").toString();
    if (anon.size() >= 32)
        m_anonId = anon;
    m_lastSubmitTs = o.value("last_submit_ts").toDouble(0.0);
}

void TelemetryClient::saveState() const
{
    QJsonObject o;
    o.insert("enabled", m_enabled);
    o.insert("anon_id", m_anonId.isEmpty() ? QJsonValue(QJsonValue::Null) : QJsonValue(m_anonId));
    o.insert("last_submit_ts", m_lastSubmitTs);
    QDir().mkpath(paths::configDir());
    QFile f(statePath());
    if (f.open(QIODevice::WriteOnly))
        f.write(QJsonDocument(o).toJson(QJsonDocument::Indented));
    else
        qWarning() << "failed to persist telemetry state";
}

void TelemetryClient::enable()
{
    if (m_anonId.isEmpty())
        m_anonId = QUuid::createUuid().toString(QUuid::WithoutBraces);
    m_enabled = true;
    m_lastSubmitTs = nowSecs(); // first send lands a week out, not on toggle
    saveState();
}

void TelemetryClient::disable()
{
    m_enabled = false;
    m_anonId.clear(); // re-opt-in gets a fresh id; prior contributions unlinkable
    m_lastSubmitTs = 0.0;
    saveState();
}

bool TelemetryClient::maybeSubmit()
{
    if (!m_enabled || m_endpoint.isEmpty() || m_anonId.isEmpty() || !m_provider)
        return false;
    if (nowSecs() - m_lastSubmitTs < kSubmitIntervalSeconds)
        return false;
    return submitNow();
}

bool TelemetryClient::submitOnQuit()
{
    if (!m_enabled || m_endpoint.isEmpty() || m_anonId.isEmpty() || !m_provider)
        return false;
    if (nowSecs() - m_lastSubmitTs < 60.0) // don't double-up right after a weekly send
        return false;
    return submitNow();
}

QByteArray TelemetryClient::buildPayload() const
{
    const QVariantMap stats = m_provider ? m_provider() : QVariantMap();
    QJsonObject o;
    o.insert("anon_id", m_anonId);
    o.insert("app_version", m_appVersion.isEmpty() ? QStringLiteral("unknown") : m_appVersion);
    o.insert("os", m_osName.isEmpty() ? QStringLiteral("unknown") : m_osName);
    o.insert("keystrokes", double(clampInt(stats, "alltimeKeystrokes", kMaxKeystrokes)));
    o.insert("words", double(clampInt(stats, "alltimeWords", kMaxWords)));
    o.insert("predictions", double(clampInt(stats, "alltimePredictionHits", kMaxWords)));
    o.insert("keystrokes_saved", double(clampInt(stats, "alltimeKeystrokesSaved", kMaxKeystrokes)));
    o.insert("minutes", std::max(0.0, std::min(kMaxMinutes, stats.value("alltimeMinutes", 0.0).toDouble())));
    o.insert("sessions", double(clampInt(stats, "alltimeSessions", kMaxSessions)));
    o.insert("prediction_offers", double(clampInt(stats, "alltimePredictionOffers", kMaxWords)));
    return QJsonDocument(o).toJson(QJsonDocument::Compact);
}

bool TelemetryClient::submitNow()
{
    post(m_endpoint + "/v1/submit", buildPayload(), /*updateTsOnOk=*/true);
    return true;
}

bool TelemetryClient::forget()
{
    if (m_endpoint.isEmpty() || m_anonId.isEmpty())
        return false;
    QJsonObject o;
    o.insert("anon_id", m_anonId);
    post(m_endpoint + "/v1/forget", QJsonDocument(o).toJson(QJsonDocument::Compact), false);
    return true;
}

QNetworkAccessManager *TelemetryClient::nam()
{
    if (!m_nam)
        m_nam = new QNetworkAccessManager(this);
    return m_nam;
}

void TelemetryClient::post(const QString &url, const QByteArray &body, bool updateTsOnOk)
{
    const QUrl u(url);
    if (u.scheme() != QLatin1String("https") || u.host().isEmpty()) {
        qWarning() << "refusing non-https telemetry endpoint";
        return;
    }
    QNetworkRequest req(u);
    req.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    QNetworkReply *reply = nam()->post(req, body);
    connect(reply, &QNetworkReply::finished, this, [this, reply, updateTsOnOk] {
        const int status =
            reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
        if (updateTsOnOk && status >= 200 && status < 300) {
            m_lastSubmitTs = nowSecs();
            saveState();
        }
        reply->deleteLater();
    });
}
