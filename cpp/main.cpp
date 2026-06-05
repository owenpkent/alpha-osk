#include "KeyboardBridge.h"
#include "Paths.h"
#include "WinUtil.h"
#include "prediction/HybridPredictor.h"

#include <QApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQmlError>
#include <QQuickStyle>
#include <QTextStream>
#include <QUrl>
#include <QWindow>
#include <QtGlobal>

int main(int argc, char *argv[])
{
    // Must run before the first top-level window (ideally before QApplication)
    // so the taskbar keeps our icon.
    winutil::setAppUserModelId();

    // Don't multiply logical sizes by a rounded scale factor across mixed-DPI
    // monitors. Static policy -> set before constructing the app.
    QApplication::setHighDpiScaleFactorRoundingPolicy(
        Qt::HighDpiScaleFactorRoundingPolicy::PassThrough);

    QApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("Alpha-OSK"));
    app.setOrganizationName(QStringLiteral("alpha-osk"));

    // Headless engine check: load the real model + base dicts and print a few
    // predictions, then exit. Used to verify the prediction port end-to-end.
    if (argc > 1 && QString::fromLocal8Bit(argv[1]) == QLatin1String("--selftest")) {
        QTextStream out(stdout);
        out << "alpha-osk engine self-test\n";
        out << "model dir: " << paths::modelDir() << "\n";
        out << "data dir:  " << paths::dataDir() << "\n";
        HybridPredictor predictor;
        const QStringList contexts = {"", "th", "hel", "I want to ", "the quick brown "};
        for (const QString &c : contexts) {
            out << "predict(\"" << c << "\") -> "
                << predictor.predict(c, 8).join(QStringLiteral(", ")) << "\n";
        }
        out.flush();
        return 0;
    }

    // Basic style so QtQuick.Controls customization (ScrollBar/Switch) works
    // without warnings -- matches QT_QUICK_CONTROLS_STYLE=Basic in the Python app.
    QQuickStyle::setStyle(QStringLiteral("Basic"));

    KeyboardBridge bridge;

    QQmlApplicationEngine engine;
    QObject::connect(&engine, &QQmlApplicationEngine::warnings,
                     [](const QList<QQmlError> &warnings) {
                         for (const QQmlError &w : warnings)
                             qWarning().noquote() << "QML:" << w.toString();
                     });
    engine.rootContext()->setContextProperty(QStringLiteral("keyboard"), &bridge);

    const QString mainQml = paths::qmlMainPath();
    engine.load(QUrl::fromLocalFile(mainQml));
    if (engine.rootObjects().isEmpty()) {
        qCritical().noquote() << "Failed to load" << mainQml;
        return 1;
    }

    // Apply the no-focus, always-on-top, frameless window flags to the root.
    auto *root = qobject_cast<QWindow *>(engine.rootObjects().first());
    if (root) {
        Qt::WindowFlags flags = Qt::WindowStaysOnTopHint
                              | Qt::FramelessWindowHint
                              | Qt::WindowDoesNotAcceptFocus;
        root->setFlags(flags);
        winutil::applyWindowsExtendedStyles(root); // WS_EX_NOACTIVATE on Windows

        // The snippets window is a second top-level Window; re-apply the
        // extended styles each time it becomes visible (its native handle only
        // exists once shown).
        if (auto *snipWin = root->findChild<QWindow *>(QStringLiteral("snippetsWindow"))) {
            QObject::connect(snipWin, &QWindow::visibilityChanged, snipWin,
                             [snipWin](QWindow::Visibility v) {
                                 if (v != QWindow::Hidden)
                                     winutil::applyWindowsExtendedStyles(snipWin);
                             });
        }
    }

    QObject::connect(&app, &QApplication::aboutToQuit, [&bridge]() {
        if (bridge.autoSaveOnExit())
            bridge.savePredictionModel();
        bridge.shutdown();
    });

    return app.exec();
}
