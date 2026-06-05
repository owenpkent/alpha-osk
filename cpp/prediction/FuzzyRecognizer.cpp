#include "FuzzyRecognizer.h"

#include <QFile>
#include <QRegularExpression>
#include <QSet>
#include <QTextStream>

#include <algorithm>
#include <cmath>

// ===== SymSpell ==========================================================

SymSpell::SymSpell(int maxEditDistance, int prefixLength)
    : m_maxEditDistance(maxEditDistance)
    , m_prefixLength(prefixLength)
{
}

void SymSpell::addWord(const QString &word, int freq)
{
    const QString w = word.toLower();
    if (w.isEmpty())
        return;
    auto it = m_words.find(w);
    if (it == m_words.end())
        m_words.insert(w, freq);
    else if (freq > it.value())
        it.value() = freq; // higher freq wins; lower never lowers
    m_built = false;
}

QSet<QString> SymSpell::deletionVariants(const QString &word, int maxDeletes)
{
    QSet<QString> result;
    if (maxDeletes <= 0 || word.size() <= 1)
        return result;
    QSet<QString> current;
    current.insert(word);
    for (int d = 0; d < maxDeletes; ++d) {
        QSet<QString> next;
        for (const QString &w : current) {
            for (int i = 0; i < w.size(); ++i) {
                const QString deleted = w.left(i) + w.mid(i + 1);
                result.insert(deleted);
                if (deleted.size() > 1)
                    next.insert(deleted);
            }
        }
        current = next;
    }
    return result;
}

void SymSpell::buildIndex()
{
    if (m_built)
        return;
    m_deletes.clear();
    for (auto it = m_words.constBegin(); it != m_words.constEnd(); ++it) {
        const QString &w = it.key();
        const QString indexed = w.size() > m_prefixLength ? w.left(m_prefixLength) : w;
        m_deletes[indexed].append(w);
        for (const QString &variant : deletionVariants(indexed, m_maxEditDistance))
            m_deletes[variant].append(w);
    }
    m_built = true;
}

void SymSpell::prepare()
{
    buildIndex();
}

int SymSpell::damerauLevenshtein(const QString &a, const QString &b, int maxDist)
{
    if (a == b)
        return 0;
    const int la = a.size(), lb = b.size();
    if (std::abs(la - lb) > maxDist)
        return maxDist + 1;

    QVector<int> prev2(lb + 1, 0), prev(lb + 1), curr(lb + 1);
    for (int j = 0; j <= lb; ++j)
        prev[j] = j;
    for (int i = 1; i <= la; ++i) {
        curr[0] = i;
        int rowMin = curr[0];
        for (int j = 1; j <= lb; ++j) {
            const int cost = (a.at(i - 1) == b.at(j - 1)) ? 0 : 1;
            curr[j] = std::min({curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost});
            if (i > 1 && j > 1 && a.at(i - 1) == b.at(j - 2) && a.at(i - 2) == b.at(j - 1))
                curr[j] = std::min(curr[j], prev2[j - 2] + cost);
            rowMin = std::min(rowMin, curr[j]);
        }
        if (rowMin > maxDist)
            return maxDist + 1;
        prev2 = prev;
        prev = curr;
    }
    return prev[lb];
}

QVector<SymSpell::Result> SymSpell::lookup(const QString &inputWord, int maxEditDistance)
{
    if (inputWord.isEmpty())
        return {};
    buildIndex();
    int ed = maxEditDistance < 0 ? m_maxEditDistance : std::min(maxEditDistance, m_maxEditDistance);
    if (ed < 0)
        return {};
    const QString input = inputWord.toLower();

    QHash<QString, int> candidates;
    auto wit = m_words.constFind(input);
    if (wit != m_words.constEnd()) {
        candidates.insert(input, 0);
        if (ed == 0)
            return {Result{input, wit.value(), 0}};
    }
    const QString indexedInput = input.size() > m_prefixLength ? input.left(m_prefixLength) : input;
    QSet<QString> variants = deletionVariants(indexedInput, ed);
    variants.insert(indexedInput);

    for (const QString &variant : variants) {
        auto dit = m_deletes.constFind(variant);
        if (dit == m_deletes.constEnd())
            continue;
        for (const QString &source : dit.value()) {
            if (candidates.contains(source))
                continue;
            const int dist = damerauLevenshtein(input, source, ed);
            if (dist <= ed)
                candidates.insert(source, dist);
        }
    }

    QVector<Result> results;
    for (auto it = candidates.constBegin(); it != candidates.constEnd(); ++it)
        results.append(Result{it.key(), m_words.value(it.key(), 0), it.value()});
    std::sort(results.begin(), results.end(), [](const Result &a, const Result &b) {
        if (a.dist != b.dist)
            return a.dist < b.dist;
        return a.freq > b.freq;
    });
    return results;
}

// ===== SpatialKeyModel ===================================================

SpatialKeyModel::SpatialKeyModel(double uncertaintyRadius)
    : m_radius(uncertaintyRadius)
{
    auto P = [&](char c, double row, double col) {
        m_positions.insert(QChar(c), {row, col});
    };
    // Digit row (row -1, no stagger).
    P('1', -1, 0); P('2', -1, 1); P('3', -1, 2); P('4', -1, 3); P('5', -1, 4);
    P('6', -1, 5); P('7', -1, 6); P('8', -1, 7); P('9', -1, 8); P('0', -1, 9);
    // Top row.
    P('q', 0, 0); P('w', 0, 1); P('e', 0, 2); P('r', 0, 3); P('t', 0, 4);
    P('y', 0, 5); P('u', 0, 6); P('i', 0, 7); P('o', 0, 8); P('p', 0, 9);
    // Home row (+0.25 stagger).
    P('a', 1, 0.25); P('s', 1, 1.25); P('d', 1, 2.25); P('f', 1, 3.25); P('g', 1, 4.25);
    P('h', 1, 5.25); P('j', 1, 6.25); P('k', 1, 7.25); P('l', 1, 8.25);
    // Bottom row (+0.75 stagger).
    P('z', 2, 0.75); P('x', 2, 1.75); P('c', 2, 2.75); P('v', 2, 3.75); P('b', 2, 4.75);
    P('n', 2, 5.75); P('m', 2, 6.75);
    buildNeighborCache();
}

void SpatialKeyModel::buildNeighborCache()
{
    const double cacheRadius = m_radius * 1.5;
    for (auto a = m_positions.constBegin(); a != m_positions.constEnd(); ++a) {
        QVector<QPair<QChar, double>> nbrs;
        for (auto b = m_positions.constBegin(); b != m_positions.constEnd(); ++b) {
            const double dr = a.value().first - b.value().first;
            const double dc = a.value().second - b.value().second;
            const double dist = std::sqrt(dr * dr + dc * dc);
            if (dist <= cacheRadius)
                nbrs.append({b.key(), dist});
        }
        std::sort(nbrs.begin(), nbrs.end(),
                  [](const QPair<QChar, double> &x, const QPair<QChar, double> &y) {
                      return x.second < y.second;
                  });
        m_neighbors.insert(a.key(), nbrs);
    }
}

QHash<QChar, double> SpatialKeyModel::getKeyProbabilities(QChar clicked) const
{
    const QChar key = clicked.toLower();
    if (!m_positions.contains(key))
        return {{key, 1.0}};
    const double sigma = m_radius / 2.0;
    QHash<QChar, double> probs;
    double total = 0.0;
    for (const auto &nbr : m_neighbors.value(key)) {
        if (nbr.second <= m_radius) {
            const double p = std::exp(-(nbr.second * nbr.second) / (2.0 * sigma * sigma));
            probs.insert(nbr.first, p);
            total += p;
        }
    }
    if (total > 0.0)
        for (auto it = probs.begin(); it != probs.end(); ++it)
            it.value() /= total;
    return probs;
}

// ===== FuzzyWordGenerator ================================================

FuzzyWordGenerator::FuzzyWordGenerator() = default;

QVector<FuzzyWordGenerator::ScoredWord>
FuzzyWordGenerator::generateFuzzySequences(const QString &typed, double minProb)
{
    QVector<ScoredWord> current;
    current.append({QString(), 1.0});
    for (QChar ch : typed) {
        const QHash<QChar, double> charProbs = m_spatial.getKeyProbabilities(ch);
        QVector<ScoredWord> next;
        for (const ScoredWord &pre : current) {
            for (auto it = charProbs.constBegin(); it != charProbs.constEnd(); ++it) {
                const double combined = pre.second * it.value();
                if (combined >= minProb)
                    next.append({pre.first + it.key(), combined});
            }
        }
        std::sort(next.begin(), next.end(),
                  [](const ScoredWord &a, const ScoredWord &b) { return a.second > b.second; });
        if (next.size() > m_maxCandidates * 2)
            next.resize(m_maxCandidates * 2);
        current = next;
    }
    return current;
}

double FuzzyWordGenerator::classifyEditProb(const QString &typed, const QString &candidate)
{
    const int lt = typed.size(), lc = candidate.size();
    if (lt == lc) {
        QVector<int> diff;
        for (int i = 0; i < lt; ++i)
            if (typed.at(i) != candidate.at(i))
                diff.append(i);
        if (diff.size() == 2 && diff[1] == diff[0] + 1
            && typed.at(diff[0]) == candidate.at(diff[1])
            && typed.at(diff[1]) == candidate.at(diff[0]))
            return kTranspositionProb;
        return kSubstitutionProb;
    }
    if (lc < lt)
        return kDeletionProb;
    if (candidate.contains('\'') && !typed.contains('\''))
        return kApostropheInsertionProb;
    return kInsertionProb;
}

QVector<FuzzyWordGenerator::ScoredWord>
FuzzyWordGenerator::editDistanceCandidates(const QString &typed)
{
    const int n = typed.size();
    if (n < 2)
        return {};
    QHash<QString, double> scored;
    for (const SymSpell::Result &r : m_symspell.lookup(typed)) {
        if (r.dist == 0)
            continue;
        const double prob = (r.dist == 1) ? classifyEditProb(typed, r.word) : kDoubleEditProb;
        const double score = prob / n;
        scored[r.word] = std::max(scored.value(r.word, 0.0), score);
    }
    QVector<ScoredWord> out;
    for (auto it = scored.constBegin(); it != scored.constEnd(); ++it)
        out.append({it.key(), it.value()});
    return out;
}

QVector<FuzzyWordGenerator::ScoredWord>
FuzzyWordGenerator::generateCandidates(const QString &typedIn, double minProb)
{
    const QString typed = typedIn.toLower();
    if (typed.isEmpty())
        return {};
    QHash<QString, double> scored;

    // Source 1: spatial beam search, intersected with the dictionary.
    for (const ScoredWord &seq : generateFuzzySequences(typed, minProb)) {
        auto fit = m_dictionary.constFind(seq.first);
        if (fit == m_dictionary.constEnd())
            continue;
        const double score = seq.second * std::log1p(fit.value());
        scored[seq.first] = std::max(scored.value(seq.first, 0.0), score);
    }
    // Source 2: edit-distance (SymSpell).
    for (const ScoredWord &cand : editDistanceCandidates(typed)) {
        const double score = cand.second * std::log1p(m_dictionary.value(cand.first, 0.0));
        scored[cand.first] = std::max(scored.value(cand.first, 0.0), score);
    }

    QVector<ScoredWord> out;
    for (auto it = scored.constBegin(); it != scored.constEnd(); ++it)
        out.append({it.key(), it.value()});
    std::sort(out.begin(), out.end(),
              [](const ScoredWord &a, const ScoredWord &b) { return a.second > b.second; });
    if (out.size() > m_maxCandidates)
        out.resize(m_maxCandidates);
    return out;
}

void FuzzyWordGenerator::setFrequencies(const QHash<QString, double> &freqs)
{
    for (auto it = freqs.constBegin(); it != freqs.constEnd(); ++it) {
        const QString w = it.key().toLower();
        if (it.value() > m_dictionary.value(w, 0.0))
            m_dictionary[w] = it.value();
        m_symspell.addWord(w, std::max(1, int(it.value())));
    }
    m_symspell.prepare();
}

bool FuzzyWordGenerator::loadDictionary(const QString &path)
{
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly | QIODevice::Text))
        return false;
    QTextStream ts(&f);
    while (!ts.atEnd()) {
        const QString line = ts.readLine().trimmed();
        if (line.isEmpty() || line.startsWith('#'))
            continue;
        const QStringList parts = line.toLower().split(QRegularExpression("\\s+"), Qt::SkipEmptyParts);
        if (parts.isEmpty())
            continue;
        const QString w = parts.first();
        bool ok = false;
        double freq = parts.size() > 1 ? parts[1].toDouble(&ok) : 1.0;
        if (!ok && parts.size() > 1)
            freq = 1.0;
        if (parts.size() <= 1)
            freq = 1.0;
        if (freq > m_dictionary.value(w, 0.0))
            m_dictionary[w] = freq;
        m_symspell.addWord(w, std::max(1, int(freq)));
    }
    return true;
}

// ===== CommonMisspellings ================================================

bool CommonMisspellings::load(const QString &path)
{
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly | QIODevice::Text))
        return false;
    QTextStream ts(&f);
    while (!ts.atEnd()) {
        const QString line = ts.readLine();
        const QString trimmed = line.trimmed();
        if (trimmed.isEmpty() || trimmed.startsWith('#'))
            continue;
        const int sp = trimmed.indexOf(QRegularExpression("\\s"));
        if (sp < 0)
            continue;
        const QString wrong = trimmed.left(sp).trimmed().toLower();
        const QString right = trimmed.mid(sp).trimmed().toLower();
        if (wrong.isEmpty() || right.isEmpty() || wrong == right)
            continue;
        m_table.insert(wrong, right);
    }
    return true;
}

QString CommonMisspellings::lookup(const QString &word) const
{
    return m_table.value(word.toLower());
}

// ===== FuzzyRecognizer ===================================================

FuzzyRecognizer::FuzzyRecognizer() = default;

QVector<FuzzyRecognizer::ScoredWord>
FuzzyRecognizer::getFuzzyPredictions(const QString &typedText, int n)
{
    const QStringList words = typedText.split(QRegularExpression("\\s+"), Qt::SkipEmptyParts);
    const QString current = (!words.isEmpty() && !typedText.endsWith(' ')) ? words.last() : QString();
    if (current.isEmpty())
        return {};
    QVector<ScoredWord> c = m_gen.generateCandidates(current);
    if (c.size() > n)
        c.resize(n);
    return c;
}

FuzzyRecognizer::ScoredWord FuzzyRecognizer::getCorrection(const QString &typedWord)
{
    if (m_gen.contains(typedWord))
        return {QString(), 0.0}; // don't correct a valid word
    const QVector<ScoredWord> c = m_gen.generateCandidates(typedWord);
    if (c.isEmpty())
        return {QString(), 0.0};
    return c.first();
}

double FuzzyRecognizer::typedBaseline(const QString &typedWord)
{
    if (typedWord.isEmpty())
        return 0.0;
    bool hasVowel = false, hasConsonant = false;
    for (QChar qc : typedWord.toLower()) {
        const char c = qc.toLatin1();
        if (c == 'a' || c == 'e' || c == 'i' || c == 'o' || c == 'u') {
            hasVowel = true;
        } else if (c == 'y') {
            hasVowel = true;
            hasConsonant = true;
        } else if (qc.isLetter()) {
            hasConsonant = true;
        }
    }
    if (!(hasVowel && hasConsonant))
        return 0.0;
    return std::log1p(1.0); // ~= 0.6931
}

QString FuzzyRecognizer::shouldAutocorrect(const QString &typedWord, const QString &context)
{
    Q_UNUSED(context);
    if (typedWord.size() < 3)
        return QString();
    const ScoredWord corr = getCorrection(typedWord);
    if (corr.first.isEmpty())
        return QString();
    if (corr.second < m_confidenceThreshold)
        return QString();
    if (corr.second < typedBaseline(typedWord) * m_autocorrectMargin)
        return QString();
    return corr.first;
}
