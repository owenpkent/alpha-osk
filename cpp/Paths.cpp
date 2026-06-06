#include "Paths.h"

#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QStandardPaths>
#include <QStringList>

#ifndef APP_PROJECT_ROOT
#define APP_PROJECT_ROOT ""
#endif

namespace paths {

QString configDir()
{
    QString appdata = qEnvironmentVariable("APPDATA");
    if (appdata.isEmpty())
        appdata = QStandardPaths::writableLocation(QStandardPaths::GenericConfigLocation);
    return QDir(appdata).filePath(QStringLiteral("alpha-osk"));
}

QString modelDir()
{
    const QString dir = QDir(configDir()).filePath(QStringLiteral("models"));
    QDir().mkpath(dir);
    return dir;
}

QString ngramModelPath()
{
    return QDir(modelDir()).filePath(QStringLiteral("ngram_model.json"));
}

QString ppmModelPath()
{
    return QDir(modelDir()).filePath(QStringLiteral("ppm_model.json"));
}

QString projectRoot()
{
    static QString cached;
    if (!cached.isEmpty())
        return cached;

    // Walk up from the executable directory looking for qml/Main.qml.
    QDir d(QCoreApplication::applicationDirPath());
    for (int i = 0; i < 6; ++i) {
        if (QFileInfo::exists(d.filePath(QStringLiteral("qml/Main.qml")))) {
            cached = d.absolutePath();
            return cached;
        }
        if (!d.cdUp())
            break;
    }

    // Dev fallback: the source tree baked in by CMake.
    cached = QString::fromUtf8(APP_PROJECT_ROOT);
    return cached;
}

QString dataDir()
{
    return QDir(projectRoot()).filePath(QStringLiteral("data"));
}

QString qmlMainPath()
{
    return QDir(projectRoot()).filePath(QStringLiteral("qml/Main.qml"));
}

QString iconPath()
{
    const QDir root(projectRoot());
    QStringList candidates;
#if defined(Q_OS_WIN)
    // .ico is the native multi-resolution Win32 format. Same assets the
    // PyInstaller spec / NSIS installer embed.
    candidates << root.filePath(QStringLiteral("build/windows/alpha-osk.ico"))
               << root.filePath(QStringLiteral("alpha-osk.ico"));
#elif defined(Q_OS_MAC)
    candidates << root.filePath(QStringLiteral("build/macos/alpha-osk.icns"));
#endif
    // PNG fallback for every platform (Linux has no native icon container).
    candidates << root.filePath(QStringLiteral("assets/logo-1024.png"));

    for (const QString &p : candidates) {
        if (QFileInfo::exists(p))
            return p;
    }
    return QString();
}

} // namespace paths
