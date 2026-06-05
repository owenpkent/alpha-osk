#include "HybridPredictor.h"

#include "Paths.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QSet>

#include <algorithm>
#include <cmath>

HybridPredictor::HybridPredictor(QObject *parent)
    : QObject(parent)
    , m_modelPath(paths::ngramModelPath())
    , m_ppmModelPath(paths::ppmModelPath())
{
    const QString dataDir = paths::dataDir();

    // N-gram pillar.
    m_ngram = std::make_unique<NgramPredictor>(dataDir, m_modelPath);
    m_ngram->loadBaseDictionary(dataDir);
    m_ngram->loadCommonBigrams(dataDir);
    m_ngram->loadCommonTrigrams(dataDir);

    // PPM pillar: load the user's saved trie if present, else train on corpus.
    m_ppm = std::make_unique<PPMPredictor>(8);
    if (!m_ppm->load(m_ppmModelPath)) {
        QFile corpus(QDir(dataDir).filePath("training_corpus.txt"));
        if (corpus.open(QIODevice::ReadOnly | QIODevice::Text))
            m_ppm->train(QString::fromUtf8(corpus.readAll()));
    }
    // Seed the PPM word dictionary from the known vocabulary so completions
    // have words to score even before the user has typed much.
    QSet<QString> ppmDict;
    for (auto it = m_ngram->unigrams().constBegin(); it != m_ngram->unigrams().constEnd(); ++it)
        ppmDict.insert(it.key());
    m_ppmWord = std::make_unique<PPMWordPredictor>(m_ppm.get(), ppmDict);

    // Fuzzy pillar: spatial + edit-distance over the base dictionary, weighted
    // by the n-gram frequencies (so common words win ties).
    m_fuzzy = std::make_unique<FuzzyRecognizer>();
    m_fuzzy->loadDictionary(QDir(dataDir).filePath("base_dictionary.txt"));
    QHash<QString, double> freqs;
    freqs.reserve(m_ngram->unigrams().size());
    for (auto it = m_ngram->unigrams().constBegin(); it != m_ngram->unigrams().constEnd(); ++it)
        freqs.insert(it.key(), double(it.value()));
    m_fuzzy->setFrequencies(freqs);

    m_misspellings.load(QDir(dataDir).filePath("common_misspellings.txt"));
}

bool HybridPredictor::isValidWord(const QString &word) const
{
    if (m_ngram->isSuppressed(word))
        return false;
    const QString lw = word.toLower();
    if (m_ngram->inVocab(lw))
        return true;
    static const QSet<QString> common = {
        "i", "a", "an", "am", "as", "at", "be", "by", "do", "go", "he", "if",
        "in", "is", "it", "me", "my", "no", "of", "on", "or", "so", "to", "up",
        "us", "we"};
    return common.contains(lw);
}

bool HybridPredictor::candidatePasses(const QString &word, bool isNextWord) const
{
    if (isNextWord && word.size() <= 2 && word != QLatin1String("i"))
        return false;
    return isValidWord(word);
}

double HybridPredictor::bigramBonus(const QString &word, const QHash<QString, int> &table) const
{
    if (table.isEmpty())
        return 1.0;
    const int count = table.value(word, 0);
    if (count <= 0)
        return 1.0;
    return 1.0 + std::log1p(count) / 2.0;
}

QHash<QString, int> HybridPredictor::fuzzyBigramTable(const QString &context) const
{
    const QStringList toks = NgramPredictor::tokenize(context);
    if (toks.isEmpty())
        return {};
    return m_ngram->bigrams().value(toks.last());
}

QStringList HybridPredictor::predict(const QString &context, int n)
{
    const bool isNextWord = context.endsWith(' ');
    const auto ngram = m_ngram->predictWithScores(context, n * 2);
    const auto ppm = m_ppmWord->predictWithScores(context, n * 2);
    const auto fuzzy = m_fuzzy->getFuzzyPredictions(context, n);

    const double ngramWeight = isNextWord ? 3.0 : 1.0;
    const double ppmWeight = isNextWord ? 0.3 : 0.8;
    const double fuzzyWeight = m_fuzzy->predictionWeight();

    QHash<QString, double> scores;
    QStringList order;

    auto addSource = [&](const QVector<QPair<QString, double>> &src, double weight,
                         bool candidateGate, const QHash<QString, int> *bigramTable) {
        for (int i = 0; i < src.size(); ++i) {
            const QString &word = src[i].first;
            if (candidateGate) {
                if (!candidatePasses(word, isNextWord))
                    continue;
            } else if (!isValidWord(word)) {
                continue;
            }
            const double bonus = bigramTable ? bigramBonus(word, *bigramTable) : 1.0;
            if (!scores.contains(word))
                order << word;
            scores[word] += (weight / (i + 1)) * bonus;
        }
    };

    addSource(ngram, ngramWeight, /*candidateGate=*/true, nullptr);
    addSource(ppm, ppmWeight, /*candidateGate=*/true, nullptr);
    const QHash<QString, int> bigramTable = fuzzyBigramTable(context);
    addSource(fuzzy, fuzzyWeight, /*candidateGate=*/false, &bigramTable);

    return finalizeScores(scores, order, n, isNextWord, context);
}

QStringList HybridPredictor::finalizeScores(const QHash<QString, double> &scoresIn,
                                            const QStringList &order, int n,
                                            bool isNextWord, const QString &context) const
{
    Q_UNUSED(context);
    struct Entry { QString word; double score; int idx; };
    QVector<Entry> entries;
    entries.reserve(order.size());
    for (int i = 0; i < order.size(); ++i) {
        const QString &w = order[i];
        double score = scoresIn.value(w);
        const int dp = m_ngram->dispreferenceOf(w);
        if (dp > 0)
            score /= (1.0 + dp * 0.5);
        entries.append({w, score, i});
    }
    std::sort(entries.begin(), entries.end(), [](const Entry &a, const Entry &b) {
        if (a.score != b.score)
            return a.score > b.score;
        return a.idx < b.idx; // stable: preserve first-seen order on ties
    });

    QStringList out;
    for (const Entry &e : entries) {
        if (isNextWord && e.word.size() <= 2 && e.word != QLatin1String("i"))
            continue;
        out << m_ngram->getCapitalized(e.word);
        if (out.size() >= n)
            break;
    }
    return out;
}

QStringList HybridPredictor::predictWithRefinement(const QString &context, int n)
{
    const QStringList result = predict(context, n);
    emit predictionsReady(result);
    return result;
}

QStringList HybridPredictor::learn(const QString &text)
{
    const QStringList newWords = m_ngram->learn(text);
    m_ppmWord->learn(text);
    return newWords;
}

void HybridPredictor::save() const
{
    m_ngram->save(m_modelPath);
    m_ppm->save(m_ppmModelPath);
}

void HybridPredictor::reloadFromDisk()
{
    m_ngram->load(m_modelPath);
    m_ppm->load(m_ppmModelPath);
}

void HybridPredictor::learnFromSelection(const QString &context, const QString &selectedWord)
{
    m_ngram->learnFromPillClick(selectedWord);
    m_ngram->reinforceContext(context, selectedWord);
}

QString HybridPredictor::checkAutocorrect(const QString &typedWord, const QString &context)
{
    if (typedWord.isEmpty())
        return QString();

    // Fast path: the curated misspellings table (not subject to the 3-char
    // guard, so "im" -> "i'm" still corrects).
    const QString misspell = m_misspellings.lookup(typedWord);
    if (!misspell.isEmpty() && misspell != typedWord.toLower()) {
        emit autocorrectSuggested(typedWord, misspell);
        return misspell;
    }

    // Slow path: fuzzy spatial + edit-distance with the confidence gates.
    const QString correction = m_fuzzy->shouldAutocorrect(typedWord, context);
    if (!correction.isEmpty())
        emit autocorrectSuggested(typedWord, correction);
    return correction;
}
