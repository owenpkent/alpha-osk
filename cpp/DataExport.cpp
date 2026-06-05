#include "DataExport.h"

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
#include <QSet>

#include <private/qzipreader_p.h>
#include <private/qzipwriter_p.h>

#ifndef APP_VERSION_STR
#define APP_VERSION_STR "0.0.0"
#endif

namespace {

constexpr int kSchemaVersion = 1;
constexpr qint64 kMaxArchiveBytes = 200LL * 1024 * 1024;
constexpr qint64 kMaxFileBytes = 75LL * 1024 * 1024;
constexpr qint64 kMaxTotalUncompressed = 500LL * 1024 * 1024;

// Archive names == config-relative paths for these.
const QStringList &modelFiles()
{
    static const QStringList m = {
        "models/ngram_model.json", "models/ppm_model.json", "analytics.json", "snippets.json"};
    return m;
}

const QSet<QString> &packFiles()
{
    static const QSet<QString> s = {"dictionary.txt", "bigrams.txt", "trigrams.txt", "pack.json"};
    return s;
}

const QRegularExpression &packIdRe()
{
    static const QRegularExpression re("^[a-z0-9_-]{1,64}$");
    return re;
}

// "" if the entry is acceptable, else a human-readable rejection reason.
QString validateEntry(const QString &name, qint64 size)
{
    if (name.isEmpty() || name.endsWith('/'))
        return QString(); // directory entry: harmless, filtered by the allow-list
    if (name.startsWith('/') || name.startsWith('\\'))
        return QStringLiteral("archive entry with absolute path: %1").arg(name);
    if (name.size() >= 2 && name.at(1) == ':')
        return QStringLiteral("archive entry with drive prefix: %1").arg(name);
    if (name.contains('\\'))
        return QStringLiteral("archive entry with backslash: %1").arg(name);
    for (const QString &part : name.split('/'))
        if (part == QLatin1String(".."))
            return QStringLiteral("archive entry with .. component: %1").arg(name);
    if (size > kMaxFileBytes)
        return QStringLiteral("archive entry exceeds per-file cap: %1").arg(name);
    return QString();
}

bool allowedMember(const QString &name)
{
    if (name == QLatin1String("manifest.json"))
        return false; // consumed separately, never extracted
    if (modelFiles().contains(name))
        return true;
    const QStringList parts = name.split('/');
    if (parts.size() == 3 && parts[0] == QLatin1String("packs"))
        return packIdRe().match(parts[1]).hasMatch() && packFiles().contains(parts[2]);
    return false;
}

bool writeAtomic(const QString &path, const QByteArray &data)
{
    QDir().mkpath(QFileInfo(path).absolutePath());
    QSaveFile f(path);
    if (!f.open(QIODevice::WriteOnly))
        return false;
    f.write(data);
    return f.commit();
}

} // namespace

ExportSummary dataexport::exportUserData(const QString &configDir, const QString &dest)
{
    ExportSummary s;
    s.schemaVersion = kSchemaVersion;
    s.appVersion = QString::fromLatin1(APP_VERSION_STR);

    QDir cfg(configDir);
    if (!cfg.exists()) {
        s.error = QStringLiteral("Config directory not found: %1").arg(configDir);
        return s;
    }
    QDir().mkpath(QFileInfo(dest).absolutePath());

    QList<QPair<QString, QString>> payloads; // (archiveName, srcPath)
    for (const QString &name : modelFiles()) {
        const QString src = cfg.filePath(name);
        if (QFileInfo(src).isFile()) {
            payloads.append({name, src});
            s.files << name;
        }
    }

    QDir packs(cfg.filePath("packs"));
    if (packs.exists()) {
        for (const QString &id : packs.entryList(QDir::Dirs | QDir::NoDotAndDotDot, QDir::Name)) {
            if (!packIdRe().match(id).hasMatch())
                continue;
            const QString packPath = packs.filePath(id);
            if (QFileInfo(packPath).isSymLink())
                continue; // don't follow symlinks out of packs_dir
            QDir pd(packPath);
            if (!QFileInfo(pd.filePath("dictionary.txt")).isFile())
                continue;
            s.packIds << id;
            for (const QString &f : pd.entryList(QDir::Files, QDir::Name)) {
                if (!packFiles().contains(f))
                    continue;
                const QString an = QStringLiteral("packs/%1/%2").arg(id, f);
                payloads.append({an, pd.filePath(f)});
                s.files << an;
            }
        }
    }

    s.exportedAt = QDateTime::currentDateTimeUtc().toString(Qt::ISODate);
    QJsonObject manifest;
    manifest.insert("schema_version", kSchemaVersion);
    manifest.insert("app_version", s.appVersion);
    manifest.insert("exported_at", s.exportedAt);
    manifest.insert("files", QJsonArray::fromStringList(s.files));
    manifest.insert("pack_ids", QJsonArray::fromStringList(s.packIds));

    QZipWriter zw(dest);
    if (zw.status() != QZipWriter::NoError) {
        s.error = QStringLiteral("Failed to open export for writing: %1").arg(dest);
        return s;
    }
    zw.addFile("manifest.json", QJsonDocument(manifest).toJson(QJsonDocument::Indented));
    for (const auto &p : payloads) {
        QFile f(p.second);
        if (f.open(QIODevice::ReadOnly))
            zw.addFile(p.first, f.readAll());
    }
    zw.close();
    if (zw.status() != QZipWriter::NoError) {
        s.error = QStringLiteral("Failed to write export: %1").arg(dest);
        return s;
    }

    s.bytes = QFileInfo(dest).size();
    s.ok = true;
    return s;
}

ExportSummary dataexport::inspectExport(const QString &src)
{
    ExportSummary s;
    QFileInfo fi(src);
    if (!fi.isFile()) {
        s.error = QStringLiteral("Export file not found: %1").arg(src);
        return s;
    }
    if (fi.size() > kMaxArchiveBytes) {
        s.error = QStringLiteral("Export file too large");
        return s;
    }
    QZipReader zr(src);
    if (!zr.exists() || !zr.isReadable()) {
        s.error = QStringLiteral("Not a valid .zip file: %1").arg(src);
        return s;
    }

    qint64 total = 0;
    bool hasManifest = false;
    for (const QZipReader::FileInfo &e : zr.fileInfoList()) {
        const QString err = validateEntry(e.filePath, e.size);
        if (!err.isEmpty()) {
            s.error = QStringLiteral("Refusing %1").arg(err);
            return s;
        }
        if (e.isFile)
            total += e.size;
        if (total > kMaxTotalUncompressed) {
            s.error = QStringLiteral("Archive uncompressed size exceeds cap");
            return s;
        }
        if (e.filePath == QLatin1String("manifest.json"))
            hasManifest = true;
    }
    if (!hasManifest) {
        s.error = QStringLiteral("Archive missing manifest.json");
        return s;
    }

    const QJsonDocument doc = QJsonDocument::fromJson(zr.fileData("manifest.json"));
    if (!doc.isObject()) {
        s.error = QStringLiteral("manifest.json is not valid JSON");
        return s;
    }
    const QJsonObject m = doc.object();
    if (!m.value("schema_version").isDouble()) {
        s.error = QStringLiteral("manifest.json missing integer schema_version");
        return s;
    }
    const int schema = m.value("schema_version").toInt();
    if (schema > kSchemaVersion) {
        s.error = QStringLiteral("Export was written with a newer schema (got %1, max %2). "
                                 "Upgrade Alpha-OSK first.")
                      .arg(schema)
                      .arg(kSchemaVersion);
        return s;
    }

    s.schemaVersion = schema;
    s.appVersion = m.value("app_version").toString("unknown");
    s.exportedAt = m.value("exported_at").toString();
    for (const QJsonValue &v : m.value("files").toArray())
        if (v.isString())
            s.files << v.toString();
    for (const QJsonValue &v : m.value("pack_ids").toArray())
        if (v.isString())
            s.packIds << v.toString();
    s.bytes = fi.size();
    s.ok = true;
    return s;
}

ExportSummary dataexport::importUserData(const QString &src, const QString &configDir)
{
    ExportSummary s = inspectExport(src);
    if (!s.ok)
        return s;

    QDir().mkpath(configDir);
    const QString exportsDir = QDir(configDir).filePath("exports");
    QDir().mkpath(exportsDir);
    const QString rescue = QDir(exportsDir).filePath(
        QStringLiteral("rescue-%1.zip").arg(QDateTime::currentDateTime().toString("yyyy-MM-dd-HHmmss")));
    const ExportSummary r = exportUserData(configDir, rescue);
    if (!r.ok)
        qWarning() << "Rescue export failed (continuing import anyway):" << r.error;

    QZipReader zr(src);
    QSet<QString> names;
    for (const QZipReader::FileInfo &e : zr.fileInfoList())
        names.insert(e.filePath);

    // Model files: atomic replace.
    for (const QString &name : modelFiles()) {
        if (!names.contains(name))
            continue;
        writeAtomic(QDir(configDir).filePath(name), zr.fileData(name));
    }

    // Packs: full replace -- drop existing pack dirs, then extract the allow-list.
    const QString packsDir = QDir(configDir).filePath("packs");
    QDir().mkpath(packsDir);
    for (const QString &id : QDir(packsDir).entryList(QDir::Dirs | QDir::NoDotAndDotDot)) {
        if (!packIdRe().match(id).hasMatch())
            continue;
        const QString p = QDir(packsDir).filePath(id);
        if (QFileInfo(p).isSymLink())
            continue;
        QDir(p).removeRecursively();
    }
    for (const QZipReader::FileInfo &e : zr.fileInfoList()) {
        if (!e.isFile || !validateEntry(e.filePath, e.size).isEmpty())
            continue;
        if (!allowedMember(e.filePath))
            continue;
        const QStringList parts = e.filePath.split('/');
        if (parts.size() != 3 || parts[0] != QLatin1String("packs"))
            continue;
        if (!packIdRe().match(parts[1]).hasMatch() || !packFiles().contains(parts[2]))
            continue;
        writeAtomic(QDir(QDir(packsDir).filePath(parts[1])).filePath(parts[2]),
                    zr.fileData(e.filePath));
    }
    return s;
}
