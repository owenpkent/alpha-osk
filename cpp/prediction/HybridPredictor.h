#pragma once

#include "FuzzyRecognizer.h"
#include "NgramPredictor.h"
#include "PPMPredictor.h"
#include "VocabularyPack.h"

#include <QObject>
#include <QVariantList>
#include <QString>
#include <QStringList>

#include <memory>

// Orchestrates the prediction pillars and exposes Qt signals to the bridge.
// MVP: the n-gram pillar only (PPM / fuzzy / LLM are stubbed empty and slot in
// later as extra scored sources). Implements the default "rank" merge
// (rank-fusion) + finalize (dispreference penalty, blacklist filter,
// short-word guard, "I"-family capitalization), matching hybrid_predictor.py.
class HybridPredictor : public QObject
{
    Q_OBJECT
public:
    explicit HybridPredictor(QObject *parent = nullptr);

    // Primary call: emits predictionsReady immediately and returns the list.
    QStringList predictWithRefinement(const QString &context, int n = 5);
    // Synchronous list (next-word after a pill, etc.).
    QStringList predict(const QString &context, int n = 5);

    QStringList learn(const QString &text);
    void learnWord(const QString &word) { m_ngram->learnWord(word); }
    bool unlearnWord(const QString &word) { return m_ngram->unlearnWord(word); }
    void learnFromSelection(const QString &context, const QString &selectedWord);

    void save() const;
    void reloadFromDisk();
    void clearUserData() { m_ngram->clearUserData(); }

    QString getCapitalized(const QString &word, bool sentenceStart = false) const
    {
        return m_ngram->getCapitalized(word, sentenceStart);
    }

    // Suppression / boost forwarders.
    void blacklistWord(const QString &w) { m_ngram->blacklistWord(w); }
    void unblacklistWord(const QString &w) { m_ngram->unblacklistWord(w); }
    void markBadSuggestion(const QString &w) { m_ngram->markBad(w); }
    void markGoodSuggestion(const QString &w) { m_ngram->removeDispreference(w); m_ngram->markGood(w); }
    void removeDispreference(const QString &w) { m_ngram->removeDispreference(w); }
    void unprefer(const QString &w) { m_ngram->unprefer(w); }
    QString recordTypedWord(const QString &w) { return m_ngram->recordTypedWord(w); }
    bool learnCapitalization(const QString &w, bool allowUpper = false)
    {
        return m_ngram->learnCapitalization(w, allowUpper);
    }
    void setCapitalization(const QString &w, const QString &pref) { m_ngram->setCapitalization(w, pref); }

    // Autocorrect (no fuzzy backend yet -> always "").
    QString checkAutocorrect(const QString &typedWord, const QString &context = QString());

    NgramPredictor *ngram() const { return m_ngram.get(); }

    // Vocabulary packs.
    QVariantList getAvailablePacks() const;
    QStringList getEnabledPacks() const;
    bool enableVocabularyPack(const QString &id);
    bool disableVocabularyPack(const QString &id);
    QString importVocabularyPack(const QString &sourceDir);
    QString getUserPacksDir() const;

    QString mergeStrategy() const { return m_mergeStrategy; }
    void setMergeStrategy(const QString &s) { m_mergeStrategy = s; }

signals:
    void predictionsReady(const QStringList &predictions);
    void predictionsRefined(const QStringList &predictions);
    void modelLoading(bool loading);
    void llmAvailableChanged(bool available);
    void autocorrectSuggested(const QString &typed, const QString &correction);
    void packsChanged();

private:
    bool isValidWord(const QString &word) const;
    bool candidatePasses(const QString &word, bool isNextWord) const;
    double bigramBonus(const QString &word, const QHash<QString, int> &table) const;
    QHash<QString, int> fuzzyBigramTable(const QString &context) const;
    QStringList finalizeScores(const QHash<QString, double> &scores,
                               const QStringList &order, int n,
                               bool isNextWord, const QString &context) const;

    std::unique_ptr<NgramPredictor> m_ngram;
    std::unique_ptr<PPMPredictor> m_ppm;
    std::unique_ptr<PPMWordPredictor> m_ppmWord;
    std::unique_ptr<FuzzyRecognizer> m_fuzzy;
    std::unique_ptr<PackManager> m_packs;
    CommonMisspellings m_misspellings;
    QString m_modelPath;
    QString m_ppmModelPath;
    QString m_mergeStrategy = QStringLiteral("rank");
};
