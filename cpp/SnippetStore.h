#pragma once

#include <QString>
#include <QVariantList>
#include <QVector>

// User-defined quick-insert text (name / email / phone / address / canned
// replies). Port of src/snippets.py. Persisted as snippets.json in the config
// dir, saved synchronously on every mutation (atomic temp-then-rename). Reads
// the user's existing file so snippets carry over from the Python app.
//
//   { "version": 1, "snippets": [ {"label": "...", "value": "..."}, ... ] }
class SnippetStore
{
public:
    explicit SnippetStore(const QString &path = QString());

    void load();
    void save();
    void reloadFromDisk();

    QVariantList getAll();           // [{label, value}], safe for QML
    QString getValue(int index);     // "" if out of range / empty slot
    int count();

    bool set(int index, const QString &label, const QString &value);
    bool add(const QString &label = QString(), const QString &value = QString());
    bool remove(int index);
    bool move(int index, int direction); // -1 up / +1 down

private:
    struct Entry { QString label; QString value; };

    void ensureLoaded();
    void seedDefaults();
    static QVector<Entry> defaultSnippets();
    static QString cleanLabel(const QString &label);
    static QString cleanValue(const QString &value);

    QString m_path;
    QVector<Entry> m_snippets;
    bool m_loaded = false;

    static constexpr int kMaxSnippets = 50;
    static constexpr int kMaxLabelLen = 40;
    static constexpr int kMaxValueLen = 2000;
    static constexpr qint64 kMaxFileBytes = 1LL * 1024 * 1024;
    static constexpr int kSchemaVersion = 1;
};
