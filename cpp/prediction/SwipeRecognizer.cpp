#include "SwipeRecognizer.h"

#include <algorithm>
#include <cmath>
#include <limits>

SwipeRecognizer::SwipeRecognizer(int sampleCount, int minWordLen,
                                 double endpointTolerance, double shapeWeight)
    : m_sampleCount(sampleCount)
    , m_minWordLen(minWordLen)
    , m_endpointTolerance(endpointTolerance)
    , m_shapeWeight(shapeWeight)
{
}

void SwipeRecognizer::setLayout(const QHash<QChar, QPointF> &keyCenters)
{
    m_layout.clear();
    for (auto it = keyCenters.constBegin(); it != keyCenters.constEnd(); ++it) {
        const QChar c = it.key().toLower();
        if (c.isLetter())
            m_layout.insert(c, it.value());
    }
    m_keySize = estimateKeySize();
}

double SwipeRecognizer::dist(const QPointF &a, const QPointF &b)
{
    const double dx = a.x() - b.x();
    const double dy = a.y() - b.y();
    return std::sqrt(dx * dx + dy * dy);
}

double SwipeRecognizer::estimateKeySize() const
{
    const QList<QPointF> keys = m_layout.values();
    if (keys.size() < 2)
        return 1.0;
    double total = 0.0;
    for (int i = 0; i < keys.size(); ++i) {
        double best = std::numeric_limits<double>::infinity();
        for (int j = 0; j < keys.size(); ++j) {
            if (i == j)
                continue;
            best = std::min(best, dist(keys[i], keys[j]));
        }
        total += best;
    }
    return total / keys.size();
}

std::optional<QVector<QPointF>> SwipeRecognizer::idealTrace(const QString &word) const
{
    QVector<QPointF> pts;
    for (QChar ch : word) {
        auto it = m_layout.constFind(ch);
        if (it == m_layout.constEnd())
            return std::nullopt;
        if (pts.isEmpty() || pts.last() != it.value())
            pts.append(it.value());
    }
    if (pts.size() < 2)
        return std::nullopt; // single-key swipes are ambiguous; let tap handle them
    return pts;
}

std::optional<QVector<QPointF>> SwipeRecognizer::resample(const QVector<QPointF> &points, int n)
{
    if (points.size() < 2 || n < 2)
        return std::nullopt;
    QVector<double> cum;
    cum.reserve(points.size());
    cum.append(0.0);
    for (int i = 1; i < points.size(); ++i)
        cum.append(cum.last() + dist(points[i - 1], points[i]));
    const double total = cum.last();
    if (total <= 0.0)
        return std::nullopt;

    const double step = total / (n - 1);
    QVector<QPointF> out;
    out.append(points.first());
    double target = step;
    int j = 1;
    for (int k = 1; k < n - 1; ++k) {
        while (j < points.size() && cum[j] < target)
            ++j;
        if (j >= points.size()) {
            out.append(points.last());
            target += step;
            continue;
        }
        const double segLen = cum[j] - cum[j - 1];
        const double t = segLen > 0 ? (target - cum[j - 1]) / segLen : 0.0;
        out.append(QPointF(points[j - 1].x() + t * (points[j].x() - points[j - 1].x()),
                           points[j - 1].y() + t * (points[j].y() - points[j - 1].y())));
        target += step;
    }
    out.append(points.last());
    return out;
}

QVector<QPointF> SwipeRecognizer::normalize(const QVector<QPointF> &points)
{
    if (points.isEmpty())
        return points;
    double cx = 0, cy = 0;
    for (const QPointF &p : points) {
        cx += p.x();
        cy += p.y();
    }
    cx /= points.size();
    cy /= points.size();
    QVector<QPointF> translated;
    translated.reserve(points.size());
    double maxExtent = 0.0;
    for (const QPointF &p : points) {
        const QPointF t(p.x() - cx, p.y() - cy);
        translated.append(t);
        maxExtent = std::max(maxExtent, std::max(std::abs(t.x()), std::abs(t.y())));
    }
    if (maxExtent <= 0.0)
        return translated;
    for (QPointF &p : translated)
        p = QPointF(p.x() / maxExtent, p.y() / maxExtent);
    return translated;
}

double SwipeRecognizer::meanDistance(const QVector<QPointF> &a, const QVector<QPointF> &b)
{
    const int n = std::min(a.size(), b.size());
    if (n == 0)
        return std::numeric_limits<double>::infinity();
    double total = 0.0;
    for (int i = 0; i < n; ++i)
        total += dist(a[i], b[i]);
    return total / n;
}

QStringList SwipeRecognizer::decode(const QVector<QPointF> &trace,
                                    const QHash<QString, int> &wordFreq, int topK) const
{
    if (m_layout.isEmpty() || trace.size() < 4)
        return {};

    const auto user = resample(trace, m_sampleCount);
    if (!user)
        return {};
    const QVector<QPointF> userNorm = normalize(*user);

    const QPointF start = trace.first();
    const QPointF end = trace.last();
    const double maxEndpointDist = m_endpointTolerance * m_keySize;

    QVector<QPair<double, QString>> scored;
    for (auto it = wordFreq.constBegin(); it != wordFreq.constEnd(); ++it) {
        const QString w = it.key().toLower();
        if (w.size() < m_minWordLen)
            continue;
        auto first = m_layout.constFind(w.at(0));
        auto last = m_layout.constFind(w.at(w.size() - 1));
        if (first == m_layout.constEnd() || last == m_layout.constEnd())
            continue;
        if (dist(first.value(), start) > maxEndpointDist)
            continue;
        if (dist(last.value(), end) > maxEndpointDist)
            continue;

        const auto ideal = idealTrace(w);
        if (!ideal)
            continue;
        const auto idealResampled = resample(*ideal, m_sampleCount);
        if (!idealResampled)
            continue;
        const QVector<QPointF> idealNorm = normalize(*idealResampled);

        const double distance = meanDistance(userNorm, idealNorm);
        const double score = std::log1p(it.value()) - m_shapeWeight * distance;
        scored.append({score, it.key()});
    }

    std::sort(scored.begin(), scored.end(),
              [](const QPair<double, QString> &a, const QPair<double, QString> &b) {
                  return a.first > b.first;
              });
    QStringList out;
    for (int i = 0; i < scored.size() && i < topK; ++i)
        out << scored[i].second;
    return out;
}
