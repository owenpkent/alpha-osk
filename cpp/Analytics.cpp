#include "Analytics.h"

#include "Paths.h"

#include <QDateTime>
#include <QDebug>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>

#include <algorithm>
#include <cmath>

namespace {

double nowSecs()
{
    return QDateTime::currentMSecsSinceEpoch() / 1000.0;
}

double round1(double x)
{
    return std::round(x * 10.0) / 10.0;
}

// Top-N words from a frequency map as [{word, count}], count desc (word asc on ties).
QVariantList topWords(const QHash<QString, int> &freq, int n)
{
    QVector<QPair<QString, int>> v;
    v.reserve(freq.size());
    for (auto it = freq.constBegin(); it != freq.constEnd(); ++it)
        v.append({it.key(), it.value()});
    std::sort(v.begin(), v.end(), [](const QPair<QString, int> &a, const QPair<QString, int> &b) {
        if (a.second != b.second)
            return a.second > b.second;
        return a.first < b.first;
    });
    QVariantList out;
    for (int i = 0; i < v.size() && i < n; ++i) {
        QVariantMap m;
        m.insert("word", v[i].first);
        m.insert("count", v[i].second);
        out.append(m);
    }
    return out;
}

QHash<QString, int> mergeFreq(const QHash<QString, int> &a, const QHash<QString, int> &b)
{
    QHash<QString, int> out = a;
    for (auto it = b.constBegin(); it != b.constEnd(); ++it)
        out[it.key()] += it.value();
    return out;
}

} // namespace

TypingAnalytics::TypingAnalytics()
{
    m_sessionStart = nowSecs();
    m_lastSampleTime = m_sessionStart;
    loadAlltime();
    m_atSessions += 1;
}

QString TypingAnalytics::statsPath() const
{
    return QDir(paths::configDir()).filePath("analytics.json");
}

void TypingAnalytics::loadAlltime()
{
    QFile f(statsPath());
    if (!f.exists() || !f.open(QIODevice::ReadOnly))
        return;
    QJsonParseError err{};
    const QJsonDocument doc = QJsonDocument::fromJson(f.readAll(), &err);
    if (err.error != QJsonParseError::NoError || !doc.isObject())
        return;
    const QJsonObject o = doc.object();
    m_atKeystrokes = static_cast<long long>(o.value("keystrokes").toDouble(0));
    m_atWords = static_cast<long long>(o.value("words").toDouble(0));
    m_atPredictions = static_cast<long long>(o.value("predictions").toDouble(0));
    m_atKeystrokesSaved = static_cast<long long>(o.value("keystrokes_saved").toDouble(0));
    m_atSessions = static_cast<long long>(o.value("sessions").toDouble(0));
    m_atMinutes = o.value("minutes").toDouble(0);
    m_atBackspaces = static_cast<long long>(o.value("backspaces").toDouble(0));
    m_atPredictionOffers = static_cast<long long>(o.value("prediction_offers").toDouble(0));
    m_atPredictionRankSum = static_cast<long long>(o.value("prediction_rank_sum").toDouble(0));
    m_atPredictionRankCount = static_cast<long long>(o.value("prediction_rank_count").toDouble(0));
    m_atTopPickCount = static_cast<long long>(o.value("top_pick_count").toDouble(0));
    const QJsonObject wf = o.value("word_freq").toObject();
    for (auto it = wf.constBegin(); it != wf.constEnd(); ++it)
        m_atWordFreq[it.key()] = it.value().toInt();
    const QJsonObject kf = o.value("key_freq").toObject();
    for (auto it = kf.constBegin(); it != kf.constEnd(); ++it)
        m_atKeyFreq[it.key()] = it.value().toInt();
}

void TypingAnalytics::reloadFromDisk()
{
    m_atKeystrokes = m_atWords = m_atPredictions = m_atKeystrokesSaved = 0;
    m_atSessions = 0;
    m_atMinutes = 0.0;
    m_atBackspaces = m_atPredictionOffers = m_atPredictionRankSum = 0;
    m_atPredictionRankCount = m_atTopPickCount = 0;
    m_atWordFreq.clear();
    m_atKeyFreq.clear();
    loadAlltime();
}

void TypingAnalytics::save() const
{
    QHash<QString, int> mergedWords = mergeFreq(m_atWordFreq, m_wordFreq);
    if (mergedWords.size() > kWordFreqCap) {
        QVector<QPair<QString, int>> v;
        v.reserve(mergedWords.size());
        for (auto it = mergedWords.constBegin(); it != mergedWords.constEnd(); ++it)
            v.append({it.key(), it.value()});
        std::sort(v.begin(), v.end(),
                  [](const QPair<QString, int> &a, const QPair<QString, int> &b) {
                      return a.second > b.second;
                  });
        QHash<QString, int> capped;
        for (int i = 0; i < kWordFreqCap; ++i)
            capped[v[i].first] = v[i].second;
        mergedWords = capped;
    }
    const QHash<QString, int> mergedKeys = mergeFreq(m_atKeyFreq, m_keyFreq);

    const double elapsedMin = (nowSecs() - m_sessionStart) / 60.0;

    QJsonObject o;
    o.insert("keystrokes", double(m_atKeystrokes + m_keystrokeCount));
    o.insert("words", double(m_atWords + m_wordCount));
    o.insert("predictions", double(m_atPredictions + m_predictionHits));
    o.insert("keystrokes_saved", double(m_atKeystrokesSaved + m_keystrokesSaved));
    o.insert("sessions", double(m_atSessions));
    o.insert("minutes", m_atMinutes + elapsedMin);
    o.insert("backspaces", double(m_atBackspaces + m_backspaceCount));
    o.insert("prediction_offers", double(m_atPredictionOffers + m_predictionOffers));
    o.insert("prediction_rank_sum", double(m_atPredictionRankSum + m_predictionRankSum));
    o.insert("prediction_rank_count", double(m_atPredictionRankCount + m_predictionRankCount));
    o.insert("top_pick_count", double(m_atTopPickCount + m_topPickCount));
    QJsonObject wf;
    for (auto it = mergedWords.constBegin(); it != mergedWords.constEnd(); ++it)
        wf.insert(it.key(), it.value());
    o.insert("word_freq", wf);
    QJsonObject kf;
    for (auto it = mergedKeys.constBegin(); it != mergedKeys.constEnd(); ++it)
        kf.insert(it.key(), it.value());
    o.insert("key_freq", kf);

    QDir().mkpath(QFileInfo(statsPath()).absolutePath());
    QFile f(statsPath());
    if (f.open(QIODevice::WriteOnly))
        f.write(QJsonDocument(o).toJson(QJsonDocument::Indented));
    else
        qWarning() << "Failed to save analytics:" << statsPath();
}

void TypingAnalytics::recordKeystroke(const QString &key)
{
    m_keystrokeCount += 1;
    m_keyFreq[key.toLower()] += 1;
    maybeSampleWpm();
}

void TypingAnalytics::recordWordCompleted(const QString &word)
{
    if (!word.isEmpty()) {
        m_wordCount += 1;
        m_wordFreq[word.toLower()] += 1;
    }
}

void TypingAnalytics::recordPredictionSelected(const QString &word, int rank, int keystrokesSaved)
{
    m_predictionHits += 1;
    m_predictionRankSum += rank;
    m_predictionRankCount += 1;
    if (rank == 1)
        m_topPickCount += 1;
    m_keystrokesSaved += keystrokesSaved;
    recordWordCompleted(word);
}

void TypingAnalytics::recordPredictionOffered()
{
    m_predictionOffers += 1;
}

void TypingAnalytics::recordBackspace()
{
    m_backspaceCount += 1;
    m_keystrokeCount += 1;
}

void TypingAnalytics::maybeSampleWpm()
{
    const double now = nowSecs();
    if (now - m_lastSampleTime >= 60.0) {
        m_wpmSamples.append(double(m_wordCount - m_wordsAtLastSample));
        m_wordsAtLastSample = m_wordCount;
        m_lastSampleTime = now;
        if (m_wpmSamples.size() > 30)
            m_wpmSamples = m_wpmSamples.mid(m_wpmSamples.size() - 30);
    }
}

QVariantMap TypingAnalytics::getSessionStats() const
{
    const double elapsedMin = std::max(0.1, (nowSecs() - m_sessionStart) / 60.0);

    const long long totalTyped = m_keystrokeCount + m_keystrokesSaved;
    const double savingsPct = round1(double(m_keystrokesSaved) / std::max(1LL, totalTyped) * 100.0);

    const long long atKeystrokes = m_atKeystrokes + m_keystrokeCount;
    const long long atWords = m_atWords + m_wordCount;
    const long long atPredictions = m_atPredictions + m_predictionHits;
    const long long atSaved = m_atKeystrokesSaved + m_keystrokesSaved;
    const long long atBackspaces = m_atBackspaces + m_backspaceCount;
    const long long atOffers = m_atPredictionOffers + m_predictionOffers;
    const long long atRankCount = m_atPredictionRankCount + m_predictionRankCount;
    const double atMinutes = m_atMinutes + elapsedMin;
    const long long atTotalTyped = atKeystrokes + atSaved;
    const long long atTopPicks = m_atTopPickCount + m_topPickCount;

    const double sessionTopPickRate =
        round1(double(m_topPickCount) / std::max(1LL, m_predictionRankCount) * 100.0);
    const double atTopPickRate =
        round1(double(atTopPicks) / std::max(1LL, atRankCount) * 100.0);
    const double sessionAcceptance =
        round1(double(m_predictionHits) / std::max(1LL, m_predictionOffers) * 100.0);
    const double atAcceptance =
        round1(double(atPredictions) / std::max(1LL, atOffers) * 100.0);

    const double sessionPace =
        m_keystrokeCount > 0 ? (elapsedMin * 60.0) / m_keystrokeCount : 0.5;
    const double atPace = atKeystrokes > 0 ? (atMinutes * 60.0) / atKeystrokes : 0.5;
    const double sessionTimeSaved = m_keystrokesSaved * sessionPace;
    const double atTimeSaved = atSaved * atPace;

    QVariantList wpm;
    for (double s : m_wpmSamples)
        wpm.append(s);

    QVariantMap r;
    // Session
    r.insert("wpm", round1(double(m_wordCount) / elapsedMin));
    r.insert("sessionMinutes", round1(elapsedMin));
    r.insert("totalWords", double(m_wordCount));
    r.insert("totalKeystrokes", double(m_keystrokeCount));
    r.insert("totalBackspaces", double(m_backspaceCount));
    r.insert("keystrokesSaved", double(m_keystrokesSaved));
    r.insert("savingsPercent", savingsPct);
    r.insert("predictionHitRate",
             round1(double(m_predictionHits) / std::max(1LL, m_wordCount) * 100.0));
    r.insert("predictionHits", double(m_predictionHits));
    r.insert("backspaceRate",
             round1(double(m_backspaceCount) / std::max(1LL, m_keystrokeCount) * 100.0));
    r.insert("topWords", topWords(m_wordFreq, 5));
    r.insert("wpmSamples", wpm);
    r.insert("topPickRate", sessionTopPickRate);
    r.insert("acceptanceRate", sessionAcceptance);
    r.insert("timeSavedSeconds", round1(sessionTimeSaved));
    r.insert("predictionOffers", double(m_predictionOffers));
    // Lifetime
    r.insert("alltimeWords", double(atWords));
    r.insert("alltimeKeystrokes", double(atKeystrokes));
    r.insert("alltimeKeystrokesSaved", double(atSaved));
    r.insert("alltimePredictionHits", double(atPredictions));
    r.insert("alltimeSessions", double(m_atSessions));
    r.insert("alltimeMinutes", round1(atMinutes));
    r.insert("alltimeWpm", round1(double(atWords) / std::max(0.1, atMinutes)));
    r.insert("alltimeSavingsPercent",
             round1(double(atSaved) / std::max(1LL, atTotalTyped) * 100.0));
    r.insert("alltimePredictionHitRate",
             round1(double(atPredictions) / std::max(1LL, atWords) * 100.0));
    r.insert("alltimeBackspaces", double(atBackspaces));
    r.insert("alltimeBackspaceRate",
             round1(double(atBackspaces) / std::max(1LL, atKeystrokes) * 100.0));
    r.insert("alltimePredictionOffers", double(atOffers));
    r.insert("alltimeTopWords", topWords(mergeFreq(m_atWordFreq, m_wordFreq), 5));
    r.insert("alltimeTopPickRate", atTopPickRate);
    r.insert("alltimeAcceptanceRate", atAcceptance);
    r.insert("alltimeTimeSavedSeconds", round1(atTimeSaved));
    return r;
}
