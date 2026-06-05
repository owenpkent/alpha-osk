#pragma once

#include <QChar>
#include <QHash>
#include <QPair>
#include <QSet>
#include <QString>
#include <QStringList>
#include <QVector>

// Character-level Prediction by Partial Matching with PPMD escape blending.
// Faithful port of src/prediction/ppm_predictor.py (PPMPredictor + the
// PPMWordPredictor wrapper).
//
// Child iteration order does NOT affect the blended distribution (each
// non-excluded child at an order is assigned independently), so we use an
// unordered child map; only equal-probability tie ordering may differ from
// Python, which is immaterial to prediction quality.
class PPMPredictor
{
public:
    using CharProb = QHash<QChar, double>;
    using ScoredWord = QPair<QString, double>;

    explicit PPMPredictor(int maxOrder = 8, const QString &alphabet = QString());
    ~PPMPredictor();

    void train(const QString &text);
    void learnText(const QString &text) { train(text); }

    CharProb getProbabilities(const QString &context) const;
    QVector<ScoredWord> predictWord(const QString &context, const QString &partial, int n) const;

    bool load(const QString &path);   // ppm_model.json, 50 MB cap
    void save(const QString &path) const;

    int maxOrder() const { return m_maxOrder; }
    const QSet<QChar> &alphabet() const { return m_alphabet; }

private:
    struct Node {
        long long count = 0;
        QHash<QChar, Node *> children;
        ~Node() { qDeleteAll(children); }
        Node *getChild(QChar c) const { return children.value(c, nullptr); }
        Node *addChild(QChar c)
        {
            Node *&n = children[c];
            if (!n)
                n = new Node();
            return n;
        }
        long long totalChildrenCount() const
        {
            long long s = 0;
            for (Node *c : children)
                s += c->count;
            return s;
        }
        int numChildren() const { return children.size(); }
    };

    QString normalize(const QString &text) const;
    void update(const QString &context, QChar ch);
    CharProb blendProbabilities(const QString &context) const;

    Node *m_root;
    int m_maxOrder;
    QSet<QChar> m_alphabet;
    long long m_totalChars = 0;
};

// Dictionary-constrained word completion built on a PPMPredictor: prefix
// completions scored by chained character probabilities (0.01 fallback per
// missing char), plus PPM beam search for novel completions, with a 1000-entry
// completion cache.
class PPMWordPredictor
{
public:
    using ScoredWord = QPair<QString, double>;

    PPMWordPredictor(PPMPredictor *ppm, const QSet<QString> &dictionary = {});

    QVector<ScoredWord> predictWithScores(const QString &context, int n = 5);
    void learn(const QString &text);
    void setDictionary(const QSet<QString> &dictionary) { m_dictionary = dictionary; }
    PPMPredictor *ppm() const { return m_ppm; }

private:
    QVector<ScoredWord> getPredictions(const QString &context, const QString &partial, int n);

    PPMPredictor *m_ppm = nullptr;
    QSet<QString> m_dictionary;
    QHash<QString, QVector<ScoredWord>> m_cache;
    QStringList m_cacheOrder; // insertion order for half-purge eviction
    static constexpr int kCacheMax = 1000;
};
