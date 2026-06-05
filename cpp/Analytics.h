#pragma once

#include <QHash>
#include <QString>
#include <QVariantMap>
#include <QVector>

// Session + all-time typing statistics. Port of src/analytics.py. Every session
// counter has an all-time mirror loaded from / merged into analytics.json in the
// config dir. get_session_stats() exposes both (session keys + alltime<Metric>
// keys) plus the derived metrics the dashboard binds. Data never leaves the box.
class TypingAnalytics
{
public:
    TypingAnalytics();

    void recordKeystroke(const QString &key);
    void recordWordCompleted(const QString &word);
    void recordPredictionSelected(const QString &word, int rank, int keystrokesSaved = 0);
    void recordPredictionOffered();
    void recordBackspace();

    QVariantMap getSessionStats() const;
    void save() const;
    void reloadFromDisk();

private:
    QString statsPath() const;
    void loadAlltime();
    void maybeSampleWpm();

    // Session counters.
    double m_sessionStart = 0.0;
    long long m_keystrokeCount = 0;
    long long m_wordCount = 0;
    long long m_predictionHits = 0;
    long long m_predictionOffers = 0;
    long long m_backspaceCount = 0;
    long long m_keystrokesSaved = 0;
    QHash<QString, int> m_wordFreq;
    QHash<QString, int> m_keyFreq;
    long long m_predictionRankSum = 0;
    long long m_predictionRankCount = 0;
    long long m_topPickCount = 0;
    QVector<double> m_wpmSamples;
    double m_lastSampleTime = 0.0;
    long long m_wordsAtLastSample = 0;

    // All-time mirrors (persisted).
    long long m_atKeystrokes = 0;
    long long m_atWords = 0;
    long long m_atPredictions = 0;
    long long m_atKeystrokesSaved = 0;
    long long m_atSessions = 0;
    double m_atMinutes = 0.0;
    long long m_atBackspaces = 0;
    long long m_atPredictionOffers = 0;
    long long m_atPredictionRankSum = 0;
    long long m_atPredictionRankCount = 0;
    long long m_atTopPickCount = 0;
    QHash<QString, int> m_atWordFreq;
    QHash<QString, int> m_atKeyFreq;

    static constexpr int kWordFreqCap = 5000;
};
