#include "VocabularyPack.h"

#include "NgramPredictor.h"
#include "../Paths.h"

#include <QDebug>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>
#include <QTextStream>

namespace {

QStringList readPackLines(const QString &path)
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

} // namespace

// ===== VocabularyPack ====================================================

std::unique_ptr<VocabularyPack> VocabularyPack::fromDirectory(const QString &packDir)
{
    QFileInfo fi(packDir);
    if (!fi.isDir())
        return nullptr;

    auto pack = std::make_unique<VocabularyPack>();
    pack->m_path = fi.absoluteFilePath();
    pack->m_name = fi.fileName();
    pack->m_description.clear();
    pack->m_version = 1;

    const QString metaPath = QDir(packDir).filePath("pack.json");
    if (QFileInfo::exists(metaPath)) {
        QFile f(metaPath);
        if (f.open(QIODevice::ReadOnly)) {
            QJsonParseError err{};
            const QJsonDocument doc = QJsonDocument::fromJson(f.readAll(), &err);
            if (err.error == QJsonParseError::NoError && doc.isObject()) {
                const QJsonObject o = doc.object();
                pack->m_name = o.value("name").toString(pack->m_name);
                pack->m_description = o.value("description").toString();
                pack->m_version = o.value("version").toInt(1);
            }
        }
    }
    return pack;
}

QString VocabularyPack::id() const
{
    return QFileInfo(m_path).fileName();
}

bool VocabularyPack::load()
{
    bool loadedAny = false;

    for (const QString &raw : readPackLines(QDir(m_path).filePath("dictionary.txt"))) {
        const QString word = raw.trimmed().toLower();
        if (!word.isEmpty() && !word.startsWith('#'))
            m_words.insert(word);
    }
    loadedAny = !m_words.isEmpty();

    for (const QString &raw : readPackLines(QDir(m_path).filePath("bigrams.txt"))) {
        const QString line = raw.trimmed();
        if (line.isEmpty() || line.startsWith('#'))
            continue;
        const QStringList parts = line.split(QRegularExpression("\\s+"), Qt::SkipEmptyParts);
        if (parts.size() >= 2) {
            const QString w1 = parts[0].toLower(), w2 = parts[1].toLower();
            m_bigrams[w1][w2] = m_bigrams[w1].value(w2, 0) + kBigramWeight;
            loadedAny = true;
        }
    }

    for (const QString &raw : readPackLines(QDir(m_path).filePath("trigrams.txt"))) {
        const QString line = raw.trimmed();
        if (line.isEmpty() || line.startsWith('#'))
            continue;
        const QStringList parts = line.split(QRegularExpression("\\s+"), Qt::SkipEmptyParts);
        if (parts.size() >= 3) {
            const QString key = parts[0].toLower() + " " + parts[1].toLower();
            const QString w3 = parts[2].toLower();
            m_trigrams[key][w3] = m_trigrams[key].value(w3, 0) + kTrigramWeight;
            loadedAny = true;
        }
    }
    return loadedAny;
}

void VocabularyPack::unload()
{
    m_words.clear();
    m_bigrams.clear();
    m_trigrams.clear();
}

QVariantMap VocabularyPack::getInfo() const
{
    int bi = 0;
    for (const auto &t : m_bigrams)
        bi += t.size();
    int tri = 0;
    for (const auto &t : m_trigrams)
        tri += t.size();
    QVariantMap m;
    m.insert("id", id());
    m.insert("name", m_name);
    m.insert("description", m_description);
    m.insert("version", m_version);
    m.insert("enabled", m_enabled);
    m.insert("words", m_words.size());
    m.insert("bigrams", bi);
    m.insert("trigrams", tri);
    return m;
}

// ===== PackManager =======================================================

PackManager::PackManager(const QString &builtinPacksDir, const QString &userPacksDir)
    : m_builtinPacksDir(builtinPacksDir.isEmpty() ? QDir(paths::dataDir()).filePath("packs")
                                                  : builtinPacksDir)
    , m_userPacksDir(userPacksDir.isEmpty() ? QDir(paths::configDir()).filePath("packs")
                                            : userPacksDir)
{
    QDir().mkpath(m_userPacksDir);
    discoverPacks();
}

void PackManager::discoverPacks()
{
    for (const QString &dir : {m_builtinPacksDir, m_userPacksDir}) {
        QDir d(dir);
        if (!d.exists())
            continue;
        const QStringList subdirs = d.entryList(QDir::Dirs | QDir::NoDotAndDotDot, QDir::Name);
        for (const QString &name : subdirs) {
            if (name.startsWith('.') || m_packs.contains(name))
                continue;
            auto pack = VocabularyPack::fromDirectory(d.filePath(name));
            if (pack) {
                m_packs.insert(name, std::shared_ptr<VocabularyPack>(std::move(pack)));
                m_order << name;
            }
        }
    }
}

QStringList PackManager::getAvailablePacks() const
{
    return m_order;
}

QVariantList PackManager::getAllPackInfo() const
{
    QVariantList out;
    for (const QString &id : m_order)
        out.append(m_packs.value(id)->getInfo());
    return out;
}

QStringList PackManager::getEnabledPacks() const
{
    QStringList out;
    for (const QString &id : m_order)
        if (m_packs.value(id)->enabled())
            out << id;
    return out;
}

bool PackManager::enablePack(const QString &id)
{
    auto it = m_packs.constFind(id);
    if (it == m_packs.constEnd())
        return false;
    if (it.value()->enabled())
        return true;
    it.value()->load();
    it.value()->setEnabled(true);
    return true;
}

bool PackManager::disablePack(const QString &id)
{
    auto it = m_packs.constFind(id);
    if (it == m_packs.constEnd())
        return false;
    if (!it.value()->enabled())
        return true;
    it.value()->unload();
    it.value()->setEnabled(false);
    return true;
}

void PackManager::applyToPredictor(NgramPredictor *predictor) const
{
    if (!predictor)
        return;
    for (const QString &id : m_order) {
        const auto &pack = m_packs.value(id);
        if (pack && pack->enabled())
            predictor->injectVocab(pack->words(), VocabularyPack::kUnigramWeight,
                                   pack->bigrams(), pack->trigrams());
    }
}

bool PackManager::copyTreeSkipSymlinks(const QString &src, const QString &dst)
{
    QDir().mkpath(dst);
    QDir srcDir(src);
    const QFileInfoList entries =
        srcDir.entryInfoList(QDir::AllEntries | QDir::NoDotAndDotDot | QDir::Hidden | QDir::System);
    for (const QFileInfo &fi : entries) {
        if (fi.isSymLink())
            continue; // never dereference symlinks out of the pack tree
        const QString destPath = QDir(dst).filePath(fi.fileName());
        if (fi.isDir()) {
            if (!copyTreeSkipSymlinks(fi.absoluteFilePath(), destPath))
                return false;
        } else if (fi.isFile()) {
            if (!QFile::copy(fi.absoluteFilePath(), destPath))
                return false;
        }
    }
    return true;
}

QString PackManager::importPack(const QString &sourceDir)
{
    QFileInfo si(sourceDir);
    if (!si.isDir()) {
        qWarning() << "Import source is not a directory:" << sourceDir;
        return QString();
    }
    if (!QFileInfo::exists(QDir(sourceDir).filePath("dictionary.txt"))) {
        qWarning() << "Import source missing dictionary.txt:" << sourceDir;
        return QString();
    }

    // Derive + sanitise the pack id (filesystem directory name).
    QString rawId = si.fileName().toLower().replace(' ', '_');
    QString packId;
    for (QChar c : rawId) {
        const char l = c.toLatin1();
        if ((l >= 'a' && l <= 'z') || (l >= '0' && l <= '9') || l == '_' || l == '-')
            packId += c;
    }
    while (!packId.isEmpty() && (packId.front() == '_' || packId.front() == '-'))
        packId.remove(0, 1);
    while (!packId.isEmpty() && (packId.back() == '_' || packId.back() == '-'))
        packId.chop(1);

    static const QRegularExpression valid("^[a-z0-9][a-z0-9_\\-]{0,63}$");
    if (packId.isEmpty() || !valid.match(packId).hasMatch()) {
        qWarning() << "Rejected pack import: invalid id derived from" << sourceDir;
        return QString();
    }

    // Defence in depth: the resolved destination must sit strictly under the
    // user packs dir.
    const QString rootAbs = QDir(m_userPacksDir).absolutePath();
    const QString destAbs = QDir::cleanPath(QDir(m_userPacksDir).absoluteFilePath(packId));
    if (QDir(rootAbs).relativeFilePath(destAbs) != packId) {
        qWarning() << "Rejected pack import: destination escapes" << rootAbs;
        return QString();
    }

    // Don't overwrite a built-in pack.
    if (m_packs.contains(packId) && QFileInfo(QDir(m_builtinPacksDir).filePath(packId)).isDir()) {
        qWarning() << "Cannot overwrite built-in pack:" << packId;
        return QString();
    }

    QDir destDir(destAbs);
    if (destDir.exists())
        destDir.removeRecursively();
    if (!copyTreeSkipSymlinks(si.absoluteFilePath(), destAbs)) {
        qWarning() << "Failed to copy pack into" << destAbs;
        return QString();
    }

    if (!QFileInfo::exists(QDir(destAbs).filePath("pack.json"))) {
        QJsonObject meta;
        meta.insert("name", si.fileName());
        meta.insert("description", QStringLiteral("Custom pack imported from %1").arg(si.fileName()));
        meta.insert("version", 1);
        QFile f(QDir(destAbs).filePath("pack.json"));
        if (f.open(QIODevice::WriteOnly))
            f.write(QJsonDocument(meta).toJson(QJsonDocument::Compact));
    }

    auto pack = VocabularyPack::fromDirectory(destAbs);
    if (!pack)
        return QString();
    m_packs.insert(packId, std::shared_ptr<VocabularyPack>(std::move(pack)));
    if (!m_order.contains(packId))
        m_order << packId;
    return packId;
}
