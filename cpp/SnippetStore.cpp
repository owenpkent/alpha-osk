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

QVector<SnippetStore::Entry> SnippetStore::defaultSnippets()
{
    QVector<Entry> out;
    for (const char *lbl : kDefaultLabels)
        out.append({QString::fromLatin1(lbl), QString()});
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
        if (label.isEmpty() && value.isEmpty())
            continue;
        cleaned.append({label, value});
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

bool SnippetStore::set(int index, const QString &label, const QString &value)
{
    ensureLoaded();
    if (index < 0 || index >= m_snippets.size())
        return false;
    m_snippets[index] = {cleanLabel(label), cleanValue(value)};
    save();
    return true;
}

bool SnippetStore::add(const QString &label, const QString &value)
{
    ensureLoaded();
    if (m_snippets.size() >= kMaxSnippets)
        return false;
    m_snippets.append({cleanLabel(label), cleanValue(value)});
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
