#pragma once

#include <QString>
#include <QStringList>

// User-data export / import as a .zip. Port of src/data_export.py. Bundles the
// prediction models, analytics, snippets, and imported vocab packs; telemetry
// state is deliberately excluded. Import is replace (not merge): the archive is
// "the user's full snapshot at export time". Import validates every entry
// (zip-slip, per-file + total size caps, allow-list extraction, schema check).
struct ExportSummary
{
    bool ok = false;
    QString error;
    int schemaVersion = 1;
    QString appVersion;
    QString exportedAt;
    QStringList files;
    QStringList packIds;
    qint64 bytes = 0;
};

namespace dataexport {

// Write config_dir's user data to a .zip at dest.
ExportSummary exportUserData(const QString &configDir, const QString &dest);

// Validate an archive and read its manifest (the full import-time validation,
// so a file that fails inspection can't smuggle anything past import).
ExportSummary inspectExport(const QString &src);

// Replace config_dir's user data with the archive contents (writes a rescue
// archive under <config_dir>/exports/ first).
ExportSummary importUserData(const QString &src, const QString &configDir);

} // namespace dataexport
