#pragma once

#include <QChar>
#include <QHash>
#include <QList>
#include <QPair>
#include <QString>
#include <QStringList>
#include <QVector>

// Spatial + edit-distance fuzzy recognition. Port of
// src/prediction/{fuzzy_recognizer,symspell}.py. Turns a noisy key sequence
// into ranked real words (adjacent-key tolerance via a Gaussian spatial model,
// plus transposition/insertion/deletion correction via SymSpell), and decides
// whether to autocorrect on space.

// SymSpell: precomputed-deletion edit-distance index + Damerau-Levenshtein
// post-filter.
class SymSpell
{
public:
    struct Result { QString word; int freq; int dist; };

    explicit SymSpell(int maxEditDistance = 2, int prefixLength = 7);
    void addWord(const QString &word, int freq = 1);
    void prepare();
    QVector<Result> lookup(const QString &input, int maxEditDistance = -1);

private:
    void buildIndex();
    static QSet<QString> deletionVariants(const QString &word, int maxDeletes);
    static int damerauLevenshtein(const QString &a, const QString &b, int maxDist);

    QHash<QString, int> m_words;
    QHash<QString, QVector<QString>> m_deletes;
    int m_maxEditDistance;
    int m_prefixLength;
    bool m_built = false;
};

// P(intended key | clicked key) as a Gaussian over Euclidean key distance.
class SpatialKeyModel
{
public:
    explicit SpatialKeyModel(double uncertaintyRadius = 1.4);
    QHash<QChar, double> getKeyProbabilities(QChar clicked) const;

private:
    void buildNeighborCache();
    QHash<QChar, QPair<double, double>> m_positions;            // key -> (row, col)
    QHash<QChar, QVector<QPair<QChar, double>>> m_neighbors;    // key -> [(key, dist)] asc
    double m_radius;
};

// Beam search over spatial neighbours + SymSpell, scored against a frequency
// dictionary.
class FuzzyWordGenerator
{
public:
    using ScoredWord = QPair<QString, double>;

    FuzzyWordGenerator();

    QVector<ScoredWord> generateCandidates(const QString &typed, double minProb = 0.001);
    bool loadDictionary(const QString &path);
    void setFrequencies(const QHash<QString, double> &freqs);
    bool contains(const QString &word) const { return m_dictionary.contains(word.toLower()); }
    SpatialKeyModel &spatial() { return m_spatial; }

    // Edit penalties (tunable in the Python original).
    static constexpr double kTranspositionProb = 0.30;
    static constexpr double kDeletionProb = 0.20;
    static constexpr double kInsertionProb = 0.15;
    static constexpr double kApostropheInsertionProb = 0.50;
    static constexpr double kSubstitutionProb = 0.18;
    static constexpr double kDoubleEditProb = 0.05;

private:
    QVector<ScoredWord> generateFuzzySequences(const QString &typed, double minProb);
    QVector<ScoredWord> editDistanceCandidates(const QString &typed);
    static double classifyEditProb(const QString &typed, const QString &candidate);

    SpatialKeyModel m_spatial;
    SymSpell m_symspell;
    QHash<QString, double> m_dictionary;
    int m_maxCandidates = 50;
};

// One-to-one wrong->right corrections from data/common_misspellings.txt.
class CommonMisspellings
{
public:
    bool load(const QString &path);
    QString lookup(const QString &word) const; // "" if none
private:
    QHash<QString, QString> m_table;
};

// Public interface + autocorrect decision.
class FuzzyRecognizer
{
public:
    using ScoredWord = QPair<QString, double>;

    FuzzyRecognizer();

    QVector<ScoredWord> getFuzzyPredictions(const QString &typedText, int n = 5);
    // Returns the correction word, or "" if none / not confident enough.
    QString shouldAutocorrect(const QString &typedWord, const QString &context = QString());

    bool loadDictionary(const QString &path) { return m_gen.loadDictionary(path); }
    void setFrequencies(const QHash<QString, double> &freqs) { m_gen.setFrequencies(freqs); }
    QHash<QChar, double> getKeyAlternatives(QChar key) { return m_gen.spatial().getKeyProbabilities(key); }

    double predictionWeight() const { return m_predictionWeight; }

    static double typedBaseline(const QString &typedWord);

private:
    ScoredWord getCorrection(const QString &typedWord); // {"",0} if none

    FuzzyWordGenerator m_gen;
    double m_confidenceThreshold = 0.65;
    double m_predictionWeight = 0.6;
    double m_autocorrectMargin = 1.5;
};
