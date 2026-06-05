#include "HybridPredictor.h"

#include "Paths.h"

#include <QSet>

#include <algorithm>

HybridPredictor::HybridPredictor(QObject *parent)
    : QObject(parent)
    , m_modelPath(paths::ngramModelPath())
{
    const QString dataDir = paths::dataDir();
    m_ngram = std::make_unique<NgramPredictor>(dataDir, m_modelPath);
    m_ngram->loadBaseDictionary(dataDir);
    m_ngram->loadCommonBigrams(dataDir);
    m_ngram->loadCommonTrigrams(dataDir);
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

QStringList HybridPredictor::predict(const QString &context, int n)
{
    const auto ngram = m_ngram->predictWithScores(context, n * 2);
    const bool isNextWord = context.endsWith(' ');
    const double ngramWeight = isNextWord ? 3.0 : 1.0;

    QHash<QString, double> scores;
    QStringList order;
    for (int i = 0; i < ngram.size(); ++i) {
        const QString &word = ngram[i].first;
        if (!candidatePasses(word, isNextWord))
            continue;
        if (!scores.contains(word))
            order << word;
        scores[word] += ngramWeight / (i + 1);
    }
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
    return m_ngram->learn(text);
}

void HybridPredictor::learnFromSelection(const QString &context, const QString &selectedWord)
{
    m_ngram->learnFromPillClick(selectedWord);
    m_ngram->reinforceContext(context, selectedWord);
}

QString HybridPredictor::checkAutocorrect(const QString &typedWord, const QString &context)
{
    Q_UNUSED(typedWord);
    Q_UNUSED(context);
    return QString(); // no fuzzy backend in the MVP
}
