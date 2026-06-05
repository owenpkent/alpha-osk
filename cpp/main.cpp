#include "Analytics.h"
#include "DataExport.h"
#include "KeyboardBridge.h"
#include "Paths.h"
#include "SnippetStore.h"
#include "WinUtil.h"
#include "prediction/HybridPredictor.h"
#include "prediction/SwipeRecognizer.h"

#include <QPointF>

#include <QApplication>
#include <QDir>
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
        const QStringList contexts = {
            "", "th", "hel", "I want to ", "the quick brown ",
            "teh", "recieve", "wrk", "thsi", "hte"}; // typos -> fuzzy correction
        for (const QString &c : contexts) {
            out << "predict(\"" << c << "\") -> "
                << predictor.predict(c, 8).join(QStringLiteral(", ")) << "\n";
        }

        // Swipe decode check: build a QWERTY layout + a synthetic t->h->e trace.
        SwipeRecognizer sw;
        QHash<QChar, QPointF> layout;
        const char *rows[] = {"qwertyuiop", "asdfghjkl", "zxcvbnm"};
        for (int r = 0; r < 3; ++r)
            for (int col = 0; rows[r][col]; ++col)
                layout.insert(QChar(rows[r][col]), QPointF(col, r));
        sw.setLayout(layout);
        const QVector<QPointF> vertices = {QPointF(4, 0), QPointF(5, 1), QPointF(2, 0)}; // t,h,e
        QVector<QPointF> trace;
        for (int i = 0; i + 1 < vertices.size(); ++i)
            for (int s = 0; s < 8; ++s) {
                const double t = s / 8.0;
                trace.append(QPointF(vertices[i].x() + t * (vertices[i + 1].x() - vertices[i].x()),
                                     vertices[i].y() + t * (vertices[i + 1].y() - vertices[i].y())));
            }
        trace.append(vertices.last());
        out << "swipe t->h->e -> " << sw.decode(trace, predictor.ngram()->unigrams(), 8).join(", ") << "\n";

        SnippetStore snippets;
        QStringList labels;
        for (const QVariant &s : snippets.getAll())
            labels << s.toMap().value("label").toString();
        out << "snippets -> " << labels.join(", ") << "\n";

        TypingAnalytics analytics;
        for (int i = 0; i < 10; ++i)
            analytics.recordKeystroke("a");
        analytics.recordPredictionOffered();
        analytics.recordPredictionSelected("hello", 1, 4);
        const QVariantMap st = analytics.getSessionStats();
        out << "analytics -> keystrokes=" << st.value("totalKeystrokes").toInt()
            << " saved=" << st.value("keystrokesSaved").toInt()
            << " savings%=" << st.value("savingsPercent").toDouble()
            << " acceptance%=" << st.value("acceptanceRate").toDouble() << "\n";

        // Data-backup roundtrip: export the real config dir to a temp zip and
        // inspect it (read-only; does NOT import / overwrite anything).
        const QString tmpZip = QDir::temp().filePath("alpha-osk-selftest-export.zip");
        const ExportSummary ex = dataexport::exportUserData(paths::configDir(), tmpZip);
        const ExportSummary insp = dataexport::inspectExport(tmpZip);
        out << "data-backup -> export ok=" << ex.ok << " files=" << ex.files.size()
            << " inspect ok=" << insp.ok << " schema=" << insp.schemaVersion << "\n";

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
        bridge.saveAnalytics();
        bridge.shutdown();
    });

    return app.exec();
}
