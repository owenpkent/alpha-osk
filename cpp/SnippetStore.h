#pragma once

#include <QString>
#include <QStringList>
#include <QVariantList>
#include <QVector>

// User-defined quick-insert text (name / email / phone / address / canned
// replies). Port of src/snippets.py. Persisted as snippets.json in the config
// dir, saved synchronously on every mutation (atomic temp-then-rename). Reads
// the user's existing file so snippets carry over from the Python app.
//
//   { "version": 2, "snippets": [ {"label": "...", "value": "...",
//                                  "color": "blue"}, ... ] }
//
// Colour tags are stored as a *name* from kColorNames, never as the hex the
// UI draws, and both halves of that matter. snippets.json is replace-on-import
// from an archive the user picked, and the stored string ends up in a QML
// `color` property, so an arbitrary value from an untrusted file must never
// reach it verbatim: cleanColor() normalises case and whitespace and drops
// anything off the list to untagged, per entry rather than rejecting the file
// (a bad tag is not a reason to lose someone else's snippets). And the hexes
// have to stay legible on nine themes, which this class has no way to know,
// so QML owns them (snippetsWindow.tagInks in Main.qml) while this owns which
// names exist.
//
// "" is the grey default, not a missing value: an untagged snippet renders in
// the theme's own key colour. That is also why there is no grey *in* the list.
//
// Nothing reads the version field on load, deliberately: an entry with no
// "color" reads as untagged and one carrying an unknown name is retagged to
// untagged, so a file from either side of the 1 -> 2 bump loads correctly on
// its own merits, and a version gate would refuse files this loader can in
// fact read.
class SnippetStore
{
public:
    explicit SnippetStore(const QString &path = QString());

    void load();
    void save();
    void reloadFromDisk();

    QVariantList getAll();       // [{label, value, color}], safe for QML
    QString getValue(int index); // "" if out of range / empty slot
    int count();

    // Handed to QML so a swatch can never offer a tag this class would
    // silently drop.
    static QStringList colorNames();
    static int maxSnippets() { return kMaxSnippets; }

    // `color` is null by default, meaning "leave the existing tag alone".
    // The editor edits label and value only, so a save that replaced the
    // whole record would silently clear a tag set from the actions sheet.
    bool set(int index, const QString &label, const QString &value,
             const QString &color = QString());
    bool setColor(int index, const QString &color);
    bool add(const QString &label = QString(), const QString &value = QString(),
             const QString &color = QString());
    bool remove(int index);
    bool move(int index, int direction); // -1 up / +1 down

private:
    struct Entry
    {
        QString label;
        QString value;
        QString color;
    };

    void ensureLoaded();
    void seedDefaults();
    static QVector<Entry> defaultSnippets();
    static QString cleanLabel(const QString &label);
    static QString cleanValue(const QString &value);
    static QString cleanColor(const QString &color);

    QString m_path;
    QVector<Entry> m_snippets;
    bool m_loaded = false;

    static constexpr int kMaxSnippets = 50;
    static constexpr int kMaxLabelLen = 40;
    static constexpr int kMaxValueLen = 2000;
    static constexpr qint64 kMaxFileBytes = 1LL * 1024 * 1024;
    static constexpr int kSchemaVersion = 2;
};
