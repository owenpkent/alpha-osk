#pragma once

#include <QHash>
#include <QPair>
#include <QSet>
#include <QString>
#include <QStringList>
#include <QVector>

// Word-frequency model: unigrams + bigrams + trigrams with linear
// interpolation, recency decay, candidate promotion, suppression/boost, and
// auto-rehabilitation. Faithful port of src/prediction/ngram_predictor.py.
//
// Reads/writes the exact ngram_model.json schema so the user's existing
// learned model (from the Python app) carries over unchanged.
class NgramPredictor
{
public:
    using ScoredWord = QPair<QString, double>;

    // Loads the base vocab (Google 10k + 20k supplement + proper nouns) from
    // dataDir, then the user model from modelPath if it exists.
    NgramPredictor(const QString &dataDir, const QString &modelPath);

    // Extra base sources, loaded by HybridPredictor after construction.
    void loadBaseDictionary(const QString &dataDir);
    void loadCommonBigrams(const QString &dataDir);
    void loadCommonTrigrams(const QString &dataDir);

    void load(const QString &path);          // model json, with load-time caps
    void save(const QString &path) const;

    QVector<ScoredWord> predictWithScores(const QString &context, int n = 5) const;

    // Learning / reinforcement.
    QStringList learn(const QString &text);  // returns words newly added to user_vocab
    void learnWord(const QString &word);     // explicit +5 (bypasses the plausibility gate)
    void learnFromPillClick(const QString &word);
    void reinforceContext(const QString &context, const QString &selectedWord);
    bool unlearnWord(const QString &word);   // backspace-as-negative-signal
    QString recordTypedWord(const QString &word); // auto-rehab; returns word if restored, else ""

    // Suppression / boosting.
    void markGood(const QString &word);      // "show more": +5 then record boost
    void markBad(const QString &word);       // "show less": dispreference++
    void removeDispreference(const QString &word);
    void blacklistWord(const QString &word);
    void unblacklistWord(const QString &word);
    void unprefer(const QString &word);      // roll back a boost

    // Capitalization.
    bool learnCapitalization(const QString &word, bool allowUppercase = false);
    void setCapitalization(const QString &word, const QString &preferred);
    QString getCapitalized(const QString &word, bool sentenceStart = false) const;

    // Queries used by the merge / dashboard.
    bool isSuppressed(const QString &word) const;          // blacklisted
    int dispreferenceOf(const QString &word) const;
    bool inVocab(const QString &lowerWord) const;          // word.lower() in unigrams

    const QHash<QString, int> &unigrams() const { return m_unigrams; }
    const QHash<QString, QHash<QString, int>> &bigrams() const { return m_bigrams; }
    const QHash<QString, QHash<QString, int>> &trigrams() const { return m_trigrams; }
    const QHash<QString, int> &userVocab() const { return m_userVocab; }
    const QSet<QString> &blacklist() const { return m_blacklist; }
    const QHash<QString, int> &preferred() const { return m_preferred; }
    const QHash<QString, int> &dispreference() const { return m_dispreference; }

    void clearUserData();

    static QStringList tokenize(const QString &text);

private:
    static bool isPlausibleWord(const QString &word);
    void loadGoogleWordlists(const QString &dataDir);
    void loadProperNouns(const QString &dataDir);
    void applyDecay();
    void sweepStaleCandidates();
    QVector<ScoredWord> topUnigramsWithScores(int n) const;

    // Persisted state.
    QHash<QString, int> m_unigrams;            // merged base+user freq (vocab set)
    QHash<QString, QHash<QString, int>> m_bigrams;   // prev -> {next -> count}
    QHash<QString, QHash<QString, int>> m_trigrams;  // "w2 w1" -> {next -> count}
    QHash<QString, int> m_userVocab;
    QSet<QString> m_blacklist;
    QHash<QString, int> m_dispreference;
    QHash<QString, int> m_preferred;
    QHash<QString, int> m_blacklistTypeCount;
    QHash<QString, QString> m_capitalization;  // lower -> preferred form
    QHash<QString, int> m_candidateCounts;
    QHash<QString, double> m_candidateLastSeen;
    long long m_totalWords = 0;

    // Rebuilt at runtime (never saved).
    QHash<QString, int> m_baseUnigrams;
    long long m_baseTotal = 0;
    long long m_userTotal = 0;
    double m_personalWeight = 0.7;
    int m_learnCount = 0;

    // Constants (load-bearing; keep verbatim).
    static constexpr int kCandidateThreshold = 3;
    static constexpr int kRehabilitateThreshold = 3;
    static constexpr int kDecayInterval = 50;
    static constexpr double kDecayFactor = 0.95;
    static constexpr long long kCandidateMaxAgeSeconds = 30LL * 86400;
    static constexpr int kPillClickWeight = 5;
};
