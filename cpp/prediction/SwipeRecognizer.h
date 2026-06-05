#pragma once

#include <QChar>
#include <QHash>
#include <QPointF>
#include <QString>
#include <QStringList>
#include <QVector>

#include <optional>

// Shape-matching swipe/glide-typing decoder (simplified SHARK2). Port of
// src/prediction/swipe_recognizer.py. Resamples the user trace and each
// candidate word's "ideal trace" (polyline through key centres) to N points,
// normalizes both into a unit box, and scores by
//   log1p(freq) - shape_weight * mean_point_distance,
// with a cheap endpoint pre-filter on the first/last letters.
class SwipeRecognizer
{
public:
    SwipeRecognizer(int sampleCount = 32, int minWordLen = 3,
                    double endpointTolerance = 1.5, double shapeWeight = 8.0);

    // {letter -> key-centre}. Non-letter / multi-char keys ignored.
    void setLayout(const QHash<QChar, QPointF> &keyCenters);
    bool hasLayout() const { return !m_layout.isEmpty(); }

    // Decode a trace into ranked words. Candidates + frequencies come from the
    // same map (keys are the candidate words). Empty if no layout or trace < 4.
    QStringList decode(const QVector<QPointF> &trace,
                       const QHash<QString, int> &wordFreq, int topK = 8) const;

private:
    std::optional<QVector<QPointF>> idealTrace(const QString &word) const;
    double estimateKeySize() const;
    static std::optional<QVector<QPointF>> resample(const QVector<QPointF> &points, int n);
    static QVector<QPointF> normalize(const QVector<QPointF> &points);
    static double dist(const QPointF &a, const QPointF &b);
    static double meanDistance(const QVector<QPointF> &a, const QVector<QPointF> &b);

    int m_sampleCount;
    int m_minWordLen;
    double m_endpointTolerance;
    double m_shapeWeight;
    QHash<QChar, QPointF> m_layout;
    double m_keySize = 1.0;
};
