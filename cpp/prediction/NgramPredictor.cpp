#include "NgramPredictor.h"

#include <QDateTime>
#include <QDebug>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>
#include <QSaveFile>
#include <QTextStream>

#include <algorithm>

namespace {

qint64 nowSecs()
{
    return QDateTime::currentSecsSinceEpoch();
}

bool isAllLowerCased(const QString &s)
{
    bool any = false;
    for (QChar c : s) {
        if (c.isLetter()) {
            any = true;
            if (!c.isLower())
                return false;
        }
    }
    return any;
}

bool isAllUpper(const QString &s)
{
    bool any = false;
    for (QChar c : s) {
        if (c.isLetter()) {
            any = true;
            if (!c.isUpper())
                return false;
        }
    }
    return any;
}

QStringList readLines(const QString &path)
{
    QStringList out;
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly | QIODevice::Text))
        return out;
    QTextStream ts(&f);
    while (!ts.atEnd())
        out << ts.readLine();
    return out;
}

QJsonObject hashToJson(const QHash<QString, int> &h)
{
    QJsonObject o;
    for (auto it = h.constBegin(); it != h.constEnd(); ++it)
        o.insert(it.key(), it.value());
    return o;
}

QJsonObject nestedToJson(const QHash<QString, QHash<QString, int>> &h)
{
    QJsonObject o;
    for (auto it = h.constBegin(); it != h.constEnd(); ++it)
        o.insert(it.key(), hashToJson(it.value()));
    return o;
}

} // namespace

NgramPredictor::NgramPredictor(const QString &dataDir, const QString &modelPath)
{
    loadGoogleWordlists(dataDir);
    loadProperNouns(dataDir);
    if (QFileInfo::exists(modelPath))
        load(modelPath);
}

// ----- tokenization / plausibility ---------------------------------------

QStringList NgramPredictor::tokenize(const QString &text)
{
    QStringList out;
    QString cur;
    for (QChar qc : text.toLower()) {
        const char c = qc.toLatin1();
        if ((c >= 'a' && c <= 'z') || c == '\'') {
            cur += qc;
        } else if (!cur.isEmpty()) {
            out << cur;
            cur.clear();
        }
    }
    if (!cur.isEmpty())
        out << cur;
    return out;
}

bool NgramPredictor::isPlausibleWord(const QString &word)
{
    const int len = word.size();
    if (len == 0)
        return false;
    if (len <= 2) {
        static const QSet<QString> shortWhitelist = {
            "a", "i", "am", "an", "as", "at", "be", "by", "do", "go",
            "he", "hi", "if", "in", "is", "it", "me", "my", "no", "of",
            "oh", "ok", "on", "or", "so", "to", "up", "us", "we", "ya",
            "ha", "ah", "eh", "mm", "hm", "mr", "ms", "dr", "st", "pm"};
        return shortWhitelist.contains(word);
    }
    bool hasVowel = false, hasConsonant = false;
    for (QChar qc : word) {
        const char c = qc.toLatin1();
        if (c < 'a' || c > 'z')
            continue;
        const bool isV = (c == 'a' || c == 'e' || c == 'i' || c == 'o' || c == 'u' || c == 'y');
        if (isV)
            hasVowel = true;
        if (!(c == 'a' || c == 'e' || c == 'i' || c == 'o' || c == 'u')) // y counts as consonant
            hasConsonant = true;
    }
    return hasVowel && hasConsonant;
}

// ----- base vocab loading ------------------------------------------------

void NgramPredictor::loadGoogleWordlists(const QString &dataDir)
{
    // Primary 10K: frequency = count - rank (most frequent word highest).
    QStringList primary;
    for (const QString &raw : readLines(QDir(dataDir).filePath("google-10000-english-usa-no-swears.txt"))) {
        const QString w = raw.trimmed().toLower();
        if (!w.isEmpty() && isPlausibleWord(w))
            primary << w;
    }
    const int total = primary.size();
    for (int i = 0; i < total; ++i) {
        const int freq = total - i;
        m_unigrams[primary[i]] = qMax(m_unigrams.value(primary[i], 0), freq);
        m_baseUnigrams[primary[i]] = qMax(m_baseUnigrams.value(primary[i], 0), freq);
    }

    // 20K supplement: frequency = max(1, 500 - rank/20).
    int rank = 0;
    for (const QString &raw : readLines(QDir(dataDir).filePath("google-20000-supplement.txt"))) {
        const QString w = raw.trimmed().toLower();
        if (w.isEmpty() || !isPlausibleWord(w))
            continue;
        const int freq = qMax(1, 500 - rank / 20);
        ++rank;
        m_unigrams[w] = qMax(m_unigrams.value(w, 0), freq);
        m_baseUnigrams[w] = qMax(m_baseUnigrams.value(w, 0), freq);
    }

    // Fallback if no wordlist shipped.
    if (m_baseUnigrams.isEmpty()) {
        static const char *common[] = {
            "the", "be", "to", "of", "and", "a", "in", "that", "have", "i",
            "it", "for", "not", "on", "with", "he", "as", "you", "do", "at"};
        for (const char *w : common) {
            m_unigrams[QString::fromLatin1(w)] = 100;
            m_baseUnigrams[QString::fromLatin1(w)] = 100;
        }
    }

    m_baseTotal = 0;
    for (int v : m_baseUnigrams)
        m_baseTotal += v;
}

void NgramPredictor::loadProperNouns(const QString &dataDir)
{
    for (const QString &raw : readLines(QDir(dataDir).filePath("proper_nouns.txt"))) {
        const QString w = raw.trimmed();
        if (w.isEmpty() || w.startsWith('#'))
            continue;
        m_capitalization[w.toLower()] = w;
    }
}

void NgramPredictor::loadBaseDictionary(const QString &dataDir)
{
    for (const QString &raw : readLines(QDir(dataDir).filePath("base_dictionary.txt"))) {
        const QString line = raw.trimmed();
        if (line.isEmpty() || line.startsWith('#'))
            continue;
        const QStringList parts = line.split(QRegularExpression("\\s+"), Qt::SkipEmptyParts);
        if (parts.isEmpty())
            continue;
        const QString w = parts.first().toLower();
        if (!isPlausibleWord(w))
            continue;
        bool ok = false;
        int count = parts.size() > 1 ? parts[1].toInt(&ok) : 0;
        if (!ok || count <= 0)
            count = 1;
        m_baseUnigrams[w] = qMax(m_baseUnigrams.value(w, 0), count);
        m_unigrams[w] = qMax(m_unigrams.value(w, 0), count);
    }
    m_baseTotal = 0;
    for (int v : m_baseUnigrams)
        m_baseTotal += v;
}

void NgramPredictor::loadCommonBigrams(const QString &dataDir)
{
    for (const QString &raw : readLines(QDir(dataDir).filePath("common_bigrams.txt"))) {
        const QString line = raw.trimmed();
        if (line.isEmpty() || line.startsWith('#'))
            continue;
        const QStringList p = line.split(QRegularExpression("\\s+"), Qt::SkipEmptyParts);
        if (p.size() >= 2)
            m_bigrams[p[0].toLower()][p[1].toLower()] += 50;
    }
}

void NgramPredictor::loadCommonTrigrams(const QString &dataDir)
{
    for (const QString &raw : readLines(QDir(dataDir).filePath("common_trigrams.txt"))) {
        const QString line = raw.trimmed();
        if (line.isEmpty() || line.startsWith('#'))
            continue;
        const QStringList p = line.split(QRegularExpression("\\s+"), Qt::SkipEmptyParts);
        if (p.size() >= 3) {
            const QString w1 = p[0].toLower(), w2 = p[1].toLower(), w3 = p[2].toLower();
            m_trigrams[w1 + " " + w2][w3] += 50;
            m_bigrams[w1][w2] += 10;
            m_bigrams[w2][w3] += 10;
        }
    }
}

// ----- prediction --------------------------------------------------------

QVector<NgramPredictor::ScoredWord>
NgramPredictor::topUnigramsWithScores(int n) const
{
    QVector<ScoredWord> all;
    all.reserve(m_unigrams.size());
    for (auto it = m_unigrams.constBegin(); it != m_unigrams.constEnd(); ++it)
        all.append({it.key(), double(it.value())});
    std::sort(all.begin(), all.end(), [](const ScoredWord &a, const ScoredWord &b) {
        if (a.second != b.second)
            return a.second > b.second;
        return a.first < b.first;
    });
    if (all.size() > n)
        all.resize(n);
    return all;
}

QVector<NgramPredictor::ScoredWord>
NgramPredictor::predictWithScores(const QString &context, int n) const
{
    const bool endsWithSpace = context.endsWith(' ');
    const QString clean = context.toLower().trimmed();
    if (clean.isEmpty())
        return topUnigramsWithScores(n);

    QStringList words = tokenize(context);
    QString partial;
    if (!endsWithSpace && !words.isEmpty()) {
        partial = words.takeLast();
    }

    QHash<QString, double> triProbs, biProbs;
    if (words.size() >= 2) {
        const QString key = words[words.size() - 2] + " " + words[words.size() - 1];
        auto it = m_trigrams.constFind(key);
        if (it != m_trigrams.constEnd()) {
            long long total = 0;
            for (int v : it.value())
                total += v;
            if (total > 0)
                for (auto j = it.value().constBegin(); j != it.value().constEnd(); ++j)
                    triProbs[j.key()] = double(j.value()) / total;
        }
    }
    if (!words.isEmpty()) {
        auto it = m_bigrams.constFind(words.last());
        if (it != m_bigrams.constEnd()) {
            long long total = 0;
            for (int v : it.value())
                total += v;
            if (total > 0)
                for (auto j = it.value().constBegin(); j != it.value().constEnd(); ++j)
                    biProbs[j.key()] = double(j.value()) / total;
        }
    }

    const bool hasContext = !triProbs.isEmpty() || !biProbs.isEmpty();
    const double wTri = hasContext ? 0.5 : 0.0;
    const double wBi = hasContext ? 0.3 : 0.0;
    const double wUni = hasContext ? 0.2 : 1.0;
    const double alpha = m_personalWeight;

    QSet<QString> candidates;
    for (auto it = triProbs.constBegin(); it != triProbs.constEnd(); ++it)
        candidates.insert(it.key());
    for (auto it = biProbs.constBegin(); it != biProbs.constEnd(); ++it)
        candidates.insert(it.key());
    for (auto it = m_baseUnigrams.constBegin(); it != m_baseUnigrams.constEnd(); ++it)
        candidates.insert(it.key());
    for (auto it = m_userVocab.constBegin(); it != m_userVocab.constEnd(); ++it)
        candidates.insert(it.key());

    QVector<ScoredWord> scored;
    for (const QString &w : candidates) {
        if (!partial.isEmpty() && !w.startsWith(partial))
            continue;
        const double pTri = triProbs.value(w, 0.0);
        const double pBi = biProbs.value(w, 0.0);
        const double pBase = m_baseTotal > 0 ? double(m_baseUnigrams.value(w, 0)) / m_baseTotal : 0.0;
        const double pUser = m_userTotal > 0 ? double(m_userVocab.value(w, 0)) / m_userTotal : 0.0;
        const double pUni = alpha * pUser + (1.0 - alpha) * pBase;
        const double score = wTri * pTri + wBi * pBi + wUni * pUni;
        if (score > 0.0)
            scored.append({w, score});
    }
    std::sort(scored.begin(), scored.end(), [](const ScoredWord &a, const ScoredWord &b) {
        if (a.second != b.second)
            return a.second > b.second;
        return a.first < b.first;
    });
    if (scored.size() > n)
        scored.resize(n);
    return scored;
}

// ----- learning ----------------------------------------------------------

QStringList NgramPredictor::learn(const QString &text)
{
    const QStringList tokens = tokenize(text);
    QStringList learned; // empty string == "not accepted this call" (None)
    QStringList newWords;
    const qint64 now = nowSecs();

    for (const QString &word : tokens) {
        if (!isPlausibleWord(word)) {
            learned << QString();
            continue;
        }
        if (m_baseUnigrams.contains(word) || m_userVocab.contains(word)) {
            const bool isNew = !m_userVocab.contains(word);
            m_unigrams[word] += 1;
            m_userVocab[word] += 1;
            m_userTotal += 1;
            m_totalWords += 1;
            if (isNew)
                newWords << word;
            learned << word;
        } else {
            m_candidateCounts[word] += 1;
            m_candidateLastSeen[word] = now;
            if (m_candidateCounts[word] >= kCandidateThreshold) {
                const int count = m_candidateCounts.take(word);
                m_candidateLastSeen.remove(word);
                m_unigrams[word] += count;
                m_userVocab[word] += count;
                m_userTotal += count;
                m_totalWords += count;
                newWords << word;
                learned << word;
            } else {
                learned << QString();
            }
        }
    }

    for (int i = 1; i < learned.size(); ++i)
        if (!learned[i - 1].isEmpty() && !learned[i].isEmpty())
            m_bigrams[learned[i - 1]][learned[i]] += 1;
    for (int i = 2; i < learned.size(); ++i)
        if (!learned[i - 2].isEmpty() && !learned[i - 1].isEmpty() && !learned[i].isEmpty())
            m_trigrams[learned[i - 2] + " " + learned[i - 1]][learned[i]] += 1;

    if (++m_learnCount >= kDecayInterval) {
        applyDecay();
        m_learnCount = 0;
    }
    return newWords;
}

void NgramPredictor::learnWord(const QString &word)
{
    const QString w = word.toLower();
    m_unigrams[w] += kPillClickWeight;
    m_userVocab[w] += kPillClickWeight;
    m_userTotal += kPillClickWeight;
    m_totalWords += kPillClickWeight;
}

void NgramPredictor::learnFromPillClick(const QString &word)
{
    const QString w = word.toLower();
    if (m_baseUnigrams.contains(w) || m_userVocab.contains(w)) {
        learnWord(w);
        return;
    }
    if (!isPlausibleWord(w))
        return;
    m_candidateCounts[w] += 1;
    m_candidateLastSeen[w] = nowSecs();
    if (m_candidateCounts[w] >= kCandidateThreshold) {
        const int count = m_candidateCounts.take(w);
        m_candidateLastSeen.remove(w);
        const int add = count * kPillClickWeight;
        m_unigrams[w] += add;
        m_userVocab[w] += add;
        m_userTotal += add;
        m_totalWords += add;
    }
}

void NgramPredictor::reinforceContext(const QString &context, const QString &selectedWord)
{
    const QStringList words = tokenize(context);
    const QString sel = selectedWord.toLower();
    if (sel.isEmpty())
        return;
    if (!words.isEmpty())
        m_bigrams[words.last()][sel] += 1;
    if (words.size() >= 2)
        m_trigrams[words[words.size() - 2] + " " + words[words.size() - 1]][sel] += 1;
}

bool NgramPredictor::unlearnWord(const QString &word)
{
    const QString w = word.toLower();
    if (m_candidateCounts.contains(w)) {
        if (--m_candidateCounts[w] <= 0) {
            m_candidateCounts.remove(w);
            m_candidateLastSeen.remove(w);
        }
        return true;
    }
    if (m_userVocab.contains(w)) {
        if (--m_userVocab[w] <= 0)
            m_userVocab.remove(w);
        m_userTotal = qMax(0LL, m_userTotal - 1);
        if (m_unigrams.contains(w) && --m_unigrams[w] <= 0)
            m_unigrams.remove(w);
        m_totalWords = qMax(0LL, m_totalWords - 1);
        return true;
    }
    return false;
}

QString NgramPredictor::recordTypedWord(const QString &word)
{
    const QString w = word.toLower();
    if (m_blacklist.contains(w)) {
        m_blacklistTypeCount[w] += 1;
        if (m_blacklistTypeCount[w] >= kRehabilitateThreshold) {
            unblacklistWord(w);
            return word;
        }
    }
    return QString();
}

// ----- suppression / boosting --------------------------------------------

void NgramPredictor::markGood(const QString &word)
{
    const QString w = word.toLower();
    learnWord(w);
    m_preferred[w] += kPillClickWeight;
}

void NgramPredictor::markBad(const QString &word)
{
    m_dispreference[word.toLower()] += 1;
}

void NgramPredictor::removeDispreference(const QString &word)
{
    m_dispreference.remove(word.toLower());
}

void NgramPredictor::blacklistWord(const QString &word)
{
    const QString w = word.toLower();
    m_blacklist.insert(w);
    m_blacklistTypeCount.remove(w);
}

void NgramPredictor::unblacklistWord(const QString &word)
{
    const QString w = word.toLower();
    m_blacklist.remove(w);
    m_blacklistTypeCount.remove(w);
}

void NgramPredictor::unprefer(const QString &word)
{
    const QString w = word.toLower();
    const int amount = m_preferred.value(w, 0);
    const int rollback = qMin(amount, m_userVocab.value(w, 0));
    if (rollback > 0) {
        if ((m_userVocab[w] -= rollback) <= 0)
            m_userVocab.remove(w);
        m_userTotal = qMax(0LL, m_userTotal - rollback);
        if (m_unigrams.contains(w) && (m_unigrams[w] -= rollback) <= 0)
            m_unigrams.remove(w);
        m_totalWords = qMax(0LL, m_totalWords - rollback);
    }
    m_preferred.remove(w);
}

// ----- capitalization ----------------------------------------------------

bool NgramPredictor::learnCapitalization(const QString &word, bool allowUppercase)
{
    if (word.size() < 2)
        return false;
    if (isAllUpper(word) && !allowUppercase)
        return false;
    const QString lower = word.toLower();
    const QString existing = m_capitalization.value(lower);
    QString lowerCap = lower;
    lowerCap[0] = lowerCap[0].toUpper();

    QString toStore;
    if (word != lower && word != lowerCap) {
        toStore = word; // unusual mixed case, e.g. iPhone
    } else if (word.at(0).isUpper() && isAllLowerCased(word.mid(1))) {
        toStore = word; // standard Title case, e.g. Owen
    } else {
        return false;
    }
    m_capitalization[lower] = toStore;
    return toStore != existing;
}

void NgramPredictor::setCapitalization(const QString &word, const QString &preferred)
{
    m_capitalization[word.toLower()] = preferred;
}

QString NgramPredictor::getCapitalized(const QString &word, bool sentenceStart) const
{
    Q_UNUSED(sentenceStart); // kept for API compatibility; intentionally ignored
    static const QHash<QString, QString> alwaysCapitalize = {
        {"i", "I"}, {"i'm", "I'm"}, {"i'll", "I'll"}, {"i'd", "I'd"}, {"i've", "I've"}};
    auto it = alwaysCapitalize.constFind(word.toLower());
    if (it != alwaysCapitalize.constEnd())
        return it.value();
    return word;
}

// ----- queries -----------------------------------------------------------

bool NgramPredictor::isSuppressed(const QString &word) const
{
    return m_blacklist.contains(word.toLower());
}

int NgramPredictor::dispreferenceOf(const QString &word) const
{
    return m_dispreference.value(word.toLower(), 0);
}

bool NgramPredictor::inVocab(const QString &lowerWord) const
{
    return m_unigrams.contains(lowerWord);
}

void NgramPredictor::injectVocab(const QSet<QString> &words, int unigramWeight,
                                 const QHash<QString, QHash<QString, int>> &bigrams,
                                 const QHash<QString, QHash<QString, int>> &trigrams)
{
    for (const QString &w : words)
        m_unigrams[w] = qMax(m_unigrams.value(w, 0), unigramWeight);
    for (auto it = bigrams.constBegin(); it != bigrams.constEnd(); ++it)
        for (auto j = it.value().constBegin(); j != it.value().constEnd(); ++j)
            m_bigrams[it.key()][j.key()] = qMax(m_bigrams[it.key()].value(j.key(), 0), j.value());
    for (auto it = trigrams.constBegin(); it != trigrams.constEnd(); ++it)
        for (auto j = it.value().constBegin(); j != it.value().constEnd(); ++j)
            m_trigrams[it.key()][j.key()] = qMax(m_trigrams[it.key()].value(j.key(), 0), j.value());
}

void NgramPredictor::clearUserData()
{
    m_userVocab.clear();
    m_candidateCounts.clear();
    m_candidateLastSeen.clear();
    m_dispreference.clear();
    m_preferred.clear();
    m_blacklist.clear();
    m_blacklistTypeCount.clear();
    m_userTotal = 0;
}

// ----- decay -------------------------------------------------------------

void NgramPredictor::applyDecay()
{
    QHash<QString, int> decayed;
    for (auto it = m_userVocab.constBegin(); it != m_userVocab.constEnd(); ++it) {
        const int v = int(it.value() * kDecayFactor);
        if (v >= 1)
            decayed[it.key()] = v;
    }
    m_userVocab = decayed;
    m_userTotal = 0;
    for (int v : m_userVocab)
        m_userTotal += v;

    sweepStaleCandidates();

    QHash<QString, int> dc;
    for (auto it = m_candidateCounts.constBegin(); it != m_candidateCounts.constEnd(); ++it) {
        const int v = int(it.value() * kDecayFactor);
        if (v >= 1)
            dc[it.key()] = v;
    }
    m_candidateCounts = dc;
}

void NgramPredictor::sweepStaleCandidates()
{
    const qint64 now = nowSecs();
    const auto words = m_candidateCounts.keys();
    for (const QString &w : words) {
        if (!m_candidateLastSeen.contains(w)) {
            m_candidateLastSeen[w] = now; // backfill, not expired
            continue;
        }
        if (m_candidateLastSeen[w] < double(now - kCandidateMaxAgeSeconds)) {
            m_candidateCounts.remove(w);
            m_candidateLastSeen.remove(w);
        }
    }
}

// ----- load / save -------------------------------------------------------

void NgramPredictor::load(const QString &path)
{
    QFileInfo fi(path);
    if (!fi.exists())
        return;
    if (fi.size() > 50LL * 1024 * 1024) {
        qWarning() << "ngram model too large, skipping load:" << fi.size();
        return;
    }
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly))
        return;
    QJsonParseError err{};
    const QJsonDocument doc = QJsonDocument::fromJson(f.readAll(), &err);
    if (err.error != QJsonParseError::NoError || !doc.isObject()) {
        qWarning() << "ngram model parse error:" << err.errorString();
        return;
    }
    const QJsonObject o = doc.object();

    const QJsonObject uni = o.value("unigrams").toObject();
    const QJsonObject big = o.value("bigrams").toObject();
    const QJsonObject caps = o.value("capitalization").toObject();
    if (uni.size() > 500000 || big.size() > 500000 || caps.size() > 100000) {
        qWarning() << "ngram model exceeds count caps, skipping load";
        return;
    }

    // Rebuild (replace) the persisted tables, filtering fragments on load.
    m_unigrams.clear();
    for (auto it = uni.constBegin(); it != uni.constEnd(); ++it)
        if (isPlausibleWord(it.key()))
            m_unigrams[it.key()] = it.value().toInt();

    m_userVocab.clear();
    const QJsonObject uv = o.value("user_vocab").toObject();
    for (auto it = uv.constBegin(); it != uv.constEnd(); ++it)
        if (isPlausibleWord(it.key()))
            m_userVocab[it.key()] = it.value().toInt();

    m_bigrams.clear();
    for (auto it = big.constBegin(); it != big.constEnd(); ++it) {
        const QJsonObject inner = it.value().toObject();
        QHash<QString, int> h;
        for (auto j = inner.constBegin(); j != inner.constEnd(); ++j)
            h[j.key()] = j.value().toInt();
        m_bigrams[it.key()] = h;
    }

    m_trigrams.clear();
    const QJsonObject tri = o.value("trigrams").toObject();
    for (auto it = tri.constBegin(); it != tri.constEnd(); ++it) {
        const QJsonObject inner = it.value().toObject();
        QHash<QString, int> h;
        for (auto j = inner.constBegin(); j != inner.constEnd(); ++j)
            h[j.key()] = j.value().toInt();
        m_trigrams[it.key()] = h;
    }

    m_userTotal = 0;
    for (int v : m_userVocab)
        m_userTotal += v;
    m_totalWords = static_cast<long long>(o.value("total_words").toDouble(0));

    m_blacklist.clear();
    for (const QJsonValue &v : o.value("blacklist").toArray())
        m_blacklist.insert(v.toString());

    auto loadInt = [&o](const char *key, QHash<QString, int> &dst) {
        dst.clear();
        const QJsonObject j = o.value(key).toObject();
        for (auto it = j.constBegin(); it != j.constEnd(); ++it)
            dst[it.key()] = it.value().toInt();
    };
    loadInt("dispreference", m_dispreference);
    loadInt("preferred", m_preferred);
    loadInt("blacklist_type_count", m_blacklistTypeCount);
    loadInt("candidate_counts", m_candidateCounts);

    m_candidateLastSeen.clear();
    const QJsonObject cls = o.value("candidate_last_seen").toObject();
    for (auto it = cls.constBegin(); it != cls.constEnd(); ++it)
        if (m_candidateCounts.contains(it.key()))
            m_candidateLastSeen[it.key()] = it.value().toDouble();

    // Merge (not replace) capitalization over proper nouns -- user overrides win.
    for (auto it = caps.constBegin(); it != caps.constEnd(); ++it)
        m_capitalization[it.key()] = it.value().toString();
}

void NgramPredictor::save(const QString &path) const
{
    QJsonObject o;
    o.insert("unigrams", hashToJson(m_unigrams));
    o.insert("bigrams", nestedToJson(m_bigrams));
    o.insert("trigrams", nestedToJson(m_trigrams));
    o.insert("user_vocab", hashToJson(m_userVocab));
    o.insert("total_words", double(m_totalWords));

    QStringList bl = m_blacklist.values();
    bl.sort();
    QJsonArray blArr;
    for (const QString &w : bl)
        blArr.append(w);
    o.insert("blacklist", blArr);

    o.insert("dispreference", hashToJson(m_dispreference));
    o.insert("preferred", hashToJson(m_preferred));
    o.insert("blacklist_type_count", hashToJson(m_blacklistTypeCount));

    QJsonObject capsObj;
    for (auto it = m_capitalization.constBegin(); it != m_capitalization.constEnd(); ++it)
        capsObj.insert(it.key(), it.value());
    o.insert("capitalization", capsObj);

    o.insert("candidate_counts", hashToJson(m_candidateCounts));

    QJsonObject clsObj;
    for (auto it = m_candidateLastSeen.constBegin(); it != m_candidateLastSeen.constEnd(); ++it)
        clsObj.insert(it.key(), it.value());
    o.insert("candidate_last_seen", clsObj);

    QDir().mkpath(QFileInfo(path).absolutePath());
    QSaveFile f(path); // atomic temp-then-rename
    if (!f.open(QIODevice::WriteOnly)) {
        qWarning() << "could not open ngram model for save:" << path;
        return;
    }
    f.write(QJsonDocument(o).toJson(QJsonDocument::Compact));
    if (!f.commit())
        qWarning() << "ngram model save failed:" << path;
}
