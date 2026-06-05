#include "PPMPredictor.h"

#include <QDebug>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>
#include <QSaveFile>

#include <algorithm>
#include <functional>

namespace {
const QString kDefaultAlphabet = QStringLiteral("abcdefghijklmnopqrstuvwxyz '.,!?-");
constexpr qint64 kMaxModelBytes = 50LL * 1024 * 1024;
} // namespace

PPMPredictor::PPMPredictor(int maxOrder, const QString &alphabet)
    : m_root(new Node())
    , m_maxOrder(maxOrder)
{
    const QString a = alphabet.isEmpty() ? kDefaultAlphabet : alphabet;
    for (QChar c : a)
        m_alphabet.insert(c);
}

PPMPredictor::~PPMPredictor()
{
    delete m_root;
}

QString PPMPredictor::normalize(const QString &text) const
{
    QString out;
    out.reserve(text.size());
    for (QChar c : text) {
        const QChar lc = c.toLower();
        out += m_alphabet.contains(lc) ? lc : QChar(' ');
    }
    return out;
}

void PPMPredictor::update(const QString &context, QChar ch)
{
    for (int order = 0; order <= context.size(); ++order) {
        const QString suffix = order > 0 ? context.right(order) : QString();
        Node *node = m_root;
        for (QChar c : suffix)
            node = node->addChild(c);
        Node *child = node->addChild(ch);
        child->count += 1;
    }
}

void PPMPredictor::train(const QString &text)
{
    const QString t = normalize(text);
    if (t.size() < 2)
        return;
    for (int i = 0; i < t.size(); ++i) {
        const int start = qMax(0, i - m_maxOrder);
        const QString context = t.mid(start, i - start);
        update(context, t.at(i));
    }
    m_totalChars += t.size();
}

PPMPredictor::CharProb PPMPredictor::blendProbabilities(const QString &context) const
{
    CharProb probs;
    QSet<QChar> excluded;
    double escapeWeight = 1.0;
    for (int order = context.size(); order >= 0; --order) {
        const QString suffix = order > 0 ? context.right(order) : QString();
        Node *node = m_root;
        bool found = true;
        for (QChar c : suffix) {
            Node *child = node->getChild(c);
            if (!child) {
                found = false;
                break;
            }
            node = child;
        }
        if (!found)
            continue;
        const long long total = node->totalChildrenCount();
        const int unique = node->numChildren();
        if (total == 0)
            continue;
        const double escapeProb = double(unique) / (total + unique);
        for (auto it = node->children.constBegin(); it != node->children.constEnd(); ++it) {
            const QChar c = it.key();
            if (!excluded.contains(c)) {
                const double charProb = double(it.value()->count) / (total + unique);
                probs[c] += escapeWeight * charProb;
                excluded.insert(c);
            }
        }
        escapeWeight *= escapeProb;
    }
    return probs;
}

PPMPredictor::CharProb PPMPredictor::getProbabilities(const QString &context) const
{
    QString ctx = normalize(context);
    if (ctx.size() > m_maxOrder)
        ctx = ctx.right(m_maxOrder);

    const double uniform = 1.0 / m_alphabet.size();
    CharProb probs;
    for (QChar c : m_alphabet)
        probs[c] = uniform;

    const CharProb blended = blendProbabilities(ctx);
    for (auto it = blended.constBegin(); it != blended.constEnd(); ++it)
        if (probs.contains(it.key()))
            probs[it.key()] = 0.1 * probs[it.key()] + 0.9 * it.value();

    double total = 0.0;
    for (double v : probs)
        total += v;
    if (total > 0.0)
        for (auto it = probs.begin(); it != probs.end(); ++it)
            it.value() /= total;
    return probs;
}

QVector<PPMPredictor::ScoredWord>
PPMPredictor::predictWord(const QString &context, const QString &partial, int n) const
{
    const QString ctx = normalize(context);
    const QString p = partial.toLower();
    const int beamWidth = n * 3;
    const int maxLength = 15;

    struct B { QString word; double prob; QString ctx; };
    QVector<B> beam;
    beam.append({p, 1.0, ctx + p});
    QVector<ScoredWord> completed;

    const int steps = maxLength - p.size();
    for (int s = 0; s < steps; ++s) {
        QVector<B> newBeam;
        for (const B &b : beam) {
            const CharProb cp = getProbabilities(b.ctx);
            for (auto it = cp.constBegin(); it != cp.constEnd(); ++it) {
                const double np = b.prob * it.value();
                if (it.key() == QChar(' ')) {
                    if (b.word.size() > 1)
                        completed.append({b.word, np});
                } else {
                    newBeam.append({b.word + it.key(), np, b.ctx + it.key()});
                }
            }
        }
        std::sort(newBeam.begin(), newBeam.end(),
                  [](const B &a, const B &c) { return a.prob > c.prob; });
        if (newBeam.size() > beamWidth)
            newBeam.resize(beamWidth);
        beam = newBeam;
        if (beam.isEmpty())
            break;
    }
    for (const B &b : beam)
        if (b.word.size() > 1)
            completed.append({b.word, b.prob});

    std::sort(completed.begin(), completed.end(),
              [](const ScoredWord &a, const ScoredWord &b) { return a.second > b.second; });

    QSet<QString> seen;
    QVector<ScoredWord> unique;
    for (const ScoredWord &w : completed) {
        if (!seen.contains(w.first)) {
            seen.insert(w.first);
            unique.append(w);
        }
    }
    return unique;
}

// ----- serialization -----------------------------------------------------

bool PPMPredictor::load(const QString &path)
{
    QFileInfo fi(path);
    if (!fi.exists())
        return false;
    if (fi.size() > kMaxModelBytes) {
        qWarning() << "ppm model too large, skipping load:" << fi.size();
        return false;
    }
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly))
        return false;
    QJsonParseError err{};
    const QJsonDocument doc = QJsonDocument::fromJson(f.readAll(), &err);
    if (err.error != QJsonParseError::NoError || !doc.isObject())
        return false;
    const QJsonObject o = doc.object();

    m_maxOrder = o.value("max_order").toInt(m_maxOrder);
    const QString alpha = o.value("alphabet").toString();
    if (!alpha.isEmpty()) {
        m_alphabet.clear();
        for (QChar c : alpha)
            m_alphabet.insert(c);
    }
    m_totalChars = static_cast<long long>(o.value("total_chars").toDouble(0));

    delete m_root;
    m_root = new Node();
    // Recursive rebuild (trie depth <= max_order + 1, shallow).
    std::function<void(Node *, const QJsonObject &)> build =
        [&](Node *node, const QJsonObject &d) {
            node->count = static_cast<long long>(d.value("count").toDouble(0));
            const QJsonObject children = d.value("children").toObject();
            for (auto it = children.constBegin(); it != children.constEnd(); ++it) {
                if (it.key().isEmpty())
                    continue;
                Node *c = node->addChild(it.key().at(0));
                build(c, it.value().toObject());
            }
        };
    build(m_root, o.value("root").toObject());
    return true;
}

void PPMPredictor::save(const QString &path) const
{
    std::function<QJsonObject(Node *)> toJson = [&](Node *node) {
        QJsonObject o;
        o.insert("count", double(node->count));
        QJsonObject children;
        for (auto it = node->children.constBegin(); it != node->children.constEnd(); ++it)
            children.insert(QString(it.key()), toJson(it.value()));
        o.insert("children", children);
        return o;
    };

    QString alpha;
    QList<QChar> sorted = m_alphabet.values();
    std::sort(sorted.begin(), sorted.end());
    for (QChar c : sorted)
        alpha += c;

    QJsonObject o;
    o.insert("max_order", m_maxOrder);
    o.insert("alphabet", alpha);
    o.insert("total_chars", double(m_totalChars));
    o.insert("root", toJson(m_root));

    QDir().mkpath(QFileInfo(path).absolutePath());
    QSaveFile f(path);
    if (!f.open(QIODevice::WriteOnly)) {
        qWarning() << "could not open ppm model for save:" << path;
        return;
    }
    f.write(QJsonDocument(o).toJson(QJsonDocument::Compact));
    if (!f.commit())
        qWarning() << "ppm model save failed:" << path;
}

// ===== PPMWordPredictor ==================================================

PPMWordPredictor::PPMWordPredictor(PPMPredictor *ppm, const QSet<QString> &dictionary)
    : m_ppm(ppm)
    , m_dictionary(dictionary)
{
}

QVector<PPMWordPredictor::ScoredWord>
PPMWordPredictor::predictWithScores(const QString &context, int n)
{
    const bool endsWithSpace = context.endsWith(' ');
    const QString clean = context.toLower().trimmed();
    const QStringList words = clean.isEmpty()
        ? QStringList()
        : clean.split(QRegularExpression("\\s+"), Qt::SkipEmptyParts);

    QString partial;
    QString prevContext;
    if (!endsWithSpace && !words.isEmpty()) {
        partial = words.last();
        prevContext = words.size() > 1 ? QStringList(words.mid(0, words.size() - 1)).join(' ') : QString();
    } else {
        prevContext = words.join(' ');
    }

    const QString cacheKey = prevContext.right(20) + "|" + partial;
    auto cached = m_cache.constFind(cacheKey);
    if (cached != m_cache.constEnd())
        return cached.value().mid(0, n);

    const QVector<ScoredWord> predictions = getPredictions(prevContext, partial, n * 2);

    if (m_cache.size() >= kCacheMax) {
        const int drop = m_cacheOrder.size() / 2;
        for (int i = 0; i < drop && !m_cacheOrder.isEmpty(); ++i)
            m_cache.remove(m_cacheOrder.takeFirst());
    }
    m_cache.insert(cacheKey, predictions);
    m_cacheOrder.append(cacheKey);
    return predictions.mid(0, n);
}

QVector<PPMWordPredictor::ScoredWord>
PPMWordPredictor::getPredictions(const QString &context, const QString &partial, int n)
{
    QVector<ScoredWord> predictions;
    QSet<QString> seen;

    // Source 1: dictionary prefix completions scored by chained PPM char probs.
    if (!partial.isEmpty()) {
        QStringList matches;
        for (const QString &w : m_dictionary)
            if (w.size() > partial.size() && w.startsWith(partial))
                matches << w;
        matches.sort(); // deterministic 50-cap (Python's set order is nondeterministic)
        if (matches.size() > 50)
            matches = matches.mid(0, 50);

        QVector<ScoredWord> scored;
        for (const QString &word : matches) {
            const QString completion = word.mid(partial.size());
            QString ctx = context.isEmpty() ? partial : (context + " " + partial);
            double prob = 1.0;
            for (QChar ch : completion) {
                const PPMPredictor::CharProb cp = m_ppm->getProbabilities(ctx);
                prob *= cp.value(ch, 0.01);
                ctx += ch;
            }
            scored.append({word, prob});
        }
        std::sort(scored.begin(), scored.end(),
                  [](const ScoredWord &a, const ScoredWord &b) { return a.second > b.second; });
        for (const ScoredWord &w : scored) {
            if (!seen.contains(w.first)) {
                seen.insert(w.first);
                predictions.append(w);
            }
        }
    }

    // Source 2: PPM beam search for novel completions (only if still short).
    if (predictions.size() < n) {
        const QVector<ScoredWord> ppmPreds =
            m_ppm->predictWord(context, partial, n - predictions.size());
        for (const ScoredWord &w : ppmPreds) {
            if (!seen.contains(w.first)) {
                seen.insert(w.first);
                predictions.append(w);
            }
        }
    }
    return predictions;
}

void PPMWordPredictor::learn(const QString &text)
{
    m_ppm->learnText(text);
    const QStringList words = text.toLower().split(QRegularExpression("\\s+"), Qt::SkipEmptyParts);
    for (const QString &raw : words) {
        QString word;
        for (QChar c : raw)
            if (c.isLetter() || c == '\'')
                word += c;
        if (word.size() > 1)
            m_dictionary.insert(word);
    }
    m_cache.clear();
    m_cacheOrder.clear();
}
