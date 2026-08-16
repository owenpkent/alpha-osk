#include "SnippetStore.h"

#include "Paths.h"

#include <QDebug>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSaveFile>

namespace {
const char *kDefaultLabels[] = {"Name", "Email", "Phone", "Address"};

// The tag names this store recognises. "" is the untagged default and is
// deliberately first: the UI shows it as a plain grey circle, drawn from the
// theme's own key colour rather than from an ink of its own. There is no grey
// *in* the list for the same reason a blue-grey "slate" was dropped from it --
// a tag that reads as the default is a tag that cannot be seen.
const char *kColorNames[] = {"", "red", "amber", "green", "blue", "purple"};
}

SnippetStore::SnippetStore(const QString &path)
    : m_path(path.isEmpty() ? QDir(paths::configDir()).filePath("snippets.json") : path)
{
}

QString SnippetStore::cleanLabel(const QString &label)
{
    QString s = label;
    s.replace('\r', ' ').replace('\n', ' ');
    s = s.trimmed();
    return s.left(kMaxLabelLen);
}

QString SnippetStore::cleanValue(const QString &value)
{
    return value.left(kMaxValueLen); // keep newlines; only bound length
}

QString SnippetStore::cleanColor(const QString &color)
{
    // Anything not on the list becomes untagged rather than being kept or
    // rejecting the whole file. snippets.json is replace-on-import from an
    // archive the user picked and this string ends up in a QML `color`
    // property, so an unrecognised value must never reach it verbatim.
    const QString name = color.trimmed().toLower();
    for (const char *known : kColorNames) {
        if (name == QString::fromLatin1(known))
            return name;
    }
    return QString();
}

QStringList SnippetStore::colorNames()
{
    QStringList out;
    for (const char *name : kColorNames)
        out.append(QString::fromLatin1(name));
    return out;
}

QVector<SnippetStore::Entry> SnippetStore::defaultSnippets()
{
    QVector<Entry> out;
    for (const char *lbl : kDefaultLabels)
        out.append({QString::fromLatin1(lbl), QString(), QString()});
    return out;
}

void SnippetStore::seedDefaults()
{
    m_snippets = defaultSnippets();
}

void SnippetStore::ensureLoaded()
{
    if (!m_loaded)
        load();
}

void SnippetStore::load()
{
    m_loaded = true;
    QFileInfo fi(m_path);
    if (!fi.exists()) {
        seedDefaults();
        save();
        return;
    }
    if (fi.size() > kMaxFileBytes) {
        qWarning() << "snippets.json exceeds cap, reseeding";
        seedDefaults();
        return;
    }
    QFile f(m_path);
    if (!f.open(QIODevice::ReadOnly)) {
        seedDefaults();
        return;
    }
    QJsonParseError err{};
    const QJsonDocument doc = QJsonDocument::fromJson(f.readAll(), &err);
    if (err.error != QJsonParseError::NoError || !doc.isObject()) {
        seedDefaults();
        return;
    }
    const QJsonValue raw = doc.object().value("snippets");
    if (!raw.isArray()) {
        seedDefaults();
        return;
    }

    QVector<Entry> cleaned;
    for (const QJsonValue &v : raw.toArray()) {
        if (!v.isObject())
            continue;
        const QJsonObject o = v.toObject();
        const QString label = cleanLabel(o.value("label").toString());
        const QString value = cleanValue(o.value("value").toString());
        const QString color = cleanColor(o.value("color").toString());
        if (label.isEmpty() && value.isEmpty())
            continue;
        cleaned.append({label, value, color});
        if (cleaned.size() >= kMaxSnippets)
            break;
    }
    m_snippets = cleaned.isEmpty() ? defaultSnippets() : cleaned;
}

void SnippetStore::save()
{
    QJsonArray arr;
    for (const Entry &e : m_snippets) {
        QJsonObject o;
        o.insert("label", e.label);
        o.insert("value", e.value);
        o.insert("color", e.color);
        arr.append(o);
    }
    QJsonObject payload;
    payload.insert("version", kSchemaVersion);
    payload.insert("snippets", arr);

    QDir().mkpath(QFileInfo(m_path).absolutePath());
    QSaveFile f(m_path);
    if (!f.open(QIODevice::WriteOnly)) {
        qWarning() << "Failed to save snippets:" << m_path;
        return;
    }
    f.write(QJsonDocument(payload).toJson(QJsonDocument::Indented));
    if (!f.commit())
        qWarning() << "snippets save failed:" << m_path;
}

void SnippetStore::reloadFromDisk()
{
    m_loaded = false;
    load();
}

QVariantList SnippetStore::getAll()
{
    ensureLoaded();
    QVariantList out;
    for (const Entry &e : m_snippets) {
        QVariantMap m;
        m.insert("label", e.label);
        m.insert("value", e.value);
        m.insert("color", e.color);
        out.append(m);
    }
    return out;
}

QString SnippetStore::getValue(int index)
{
    ensureLoaded();
    if (index >= 0 && index < m_snippets.size())
        return m_snippets[index].value;
    return QString();
}

int SnippetStore::count()
{
    ensureLoaded();
    return m_snippets.size();
}

bool SnippetStore::set(int index, const QString &label, const QString &value,
                       const QString &color)
{
    ensureLoaded();
    if (index < 0 || index >= m_snippets.size())
        return false;
    // A null (not merely empty) colour means "keep what is there". The
    // editor only edits label and value, so replacing the whole record
    // would silently clear a tag set from the actions sheet -- and "" is a
    // real tag value here (untagged), so it cannot double as "unset".
    const QString tag = color.isNull() ? m_snippets[index].color : cleanColor(color);
    m_snippets[index] = {cleanLabel(label), cleanValue(value), tag};
    save();
    return true;
}

bool SnippetStore::setColor(int index, const QString &color)
{
    ensureLoaded();
    if (index < 0 || index >= m_snippets.size())
        return false;
    const QString cleaned = cleanColor(color);
    if (m_snippets[index].color == cleaned)
        return false; // unchanged: the caller must not re-emit
    m_snippets[index].color = cleaned;
    save();
    return true;
}

bool SnippetStore::add(const QString &label, const QString &value, const QString &color)
{
    ensureLoaded();
    if (m_snippets.size() >= kMaxSnippets)
        return false;
    m_snippets.append({cleanLabel(label), cleanValue(value), cleanColor(color)});
    save();
    return true;
}

bool SnippetStore::remove(int index)
{
    ensureLoaded();
    if (index < 0 || index >= m_snippets.size())
        return false;
    m_snippets.removeAt(index);
    save();
    return true;
}

bool SnippetStore::move(int index, int direction)
{
    ensureLoaded();
    if (direction != -1 && direction != 1)
        return false;
    const int target = index + direction;
    if (index < 0 || index >= m_snippets.size())
        return false;
    if (target < 0 || target >= m_snippets.size())
        return false;
    m_snippets.swapItemsAt(index, target);
    save();
    return true;
}
