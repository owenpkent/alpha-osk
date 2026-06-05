#pragma once

#include "Analytics.h"
#include "SnippetStore.h"
#include "prediction/SwipeRecognizer.h"

#include <QHash>
#include <QJsonObject>
#include <QObject>
#include <QPointF>
#include <QString>
#include <QStringList>
#include <QVariant>
#include <QVariantList>

class KeySynthesizer;
class HybridPredictor;
class TelemetryClient;
class QTimer;
class QNetworkAccessManager;

// The QML context object ("keyboard"). Translates UI events into OS key
// synthesis + prediction updates, and owns the modifier/typing state machine.
// Port of src/keyboard_bridge.py. MVP-critical paths are fully implemented;
// later features (snippets, swipe, telemetry, updater, analytics, data backup,
// vocab packs, model viz) are present as stubs so the reused QML never calls a
// missing method.
class KeyboardBridge : public QObject
{
    Q_OBJECT
    Q_PROPERTY(bool shiftActive READ shiftActive NOTIFY shiftActiveChanged)
    Q_PROPERTY(bool capsLockActive READ capsLockActive NOTIFY capsLockActiveChanged)
    Q_PROPERTY(bool ctrlActive READ ctrlActive NOTIFY ctrlActiveChanged)
    Q_PROPERTY(bool altActive READ altActive NOTIFY altActiveChanged)
    Q_PROPERTY(bool winActive READ winActive NOTIFY winActiveChanged)
    Q_PROPERTY(QString currentLayer READ currentLayer NOTIFY currentLayerChanged)
    Q_PROPERTY(bool synthAvailable READ synthAvailable CONSTANT)
    Q_PROPERTY(QString appVersion READ appVersion CONSTANT)
    Q_PROPERTY(bool privacyMode READ privacyMode NOTIFY privacyModeChanged)
    Q_PROPERTY(QStringList predictions READ predictions NOTIFY predictionsChanged)
    Q_PROPERTY(int predictionCount READ predictionCount NOTIFY predictionCountChanged)

public:
    explicit KeyboardBridge(QObject *parent = nullptr);
    ~KeyboardBridge() override;

    // Property getters.
    bool shiftActive() const { return m_shift; }
    bool capsLockActive() const { return m_caps; }
    bool ctrlActive() const { return m_ctrl; }
    bool altActive() const { return m_alt; }
    bool winActive() const { return m_win; }
    QString currentLayer() const { return m_currentLayer; }
    bool synthAvailable() const;
    QString appVersion() const;
    bool privacyMode() const { return m_privacy; }
    QStringList predictions() const { return m_predictions; }
    int predictionCount() const { return m_predictionCount; }

    // Read by keyboard_app on quit (plain getter, not a slot).
    bool autoSaveOnExit() const { return m_autoSaveOnExit; }

    // Called from QApplication::aboutToQuit.
    void shutdown();
    void saveAnalytics();

    // ----- MVP typing ----------------------------------------------------
    Q_INVOKABLE void pressKey(const QString &key);          // lowercases, then casing
    Q_INVOKABLE void pressKeyLiteral(const QString &ch);    // verbatim
    Q_INVOKABLE void pressSpecialKey(const QString &keyName);
    Q_INVOKABLE void toggleShift();
    Q_INVOKABLE void toggleCapsLock();
    Q_INVOKABLE void toggleCtrl();
    Q_INVOKABLE void toggleAlt();
    Q_INVOKABLE void toggleWin();
    Q_INVOKABLE void switchLayer(const QString &layer);
    Q_INVOKABLE void pressPrediction(const QString &word);
    Q_INVOKABLE void clearPredictions();
    Q_INVOKABLE void resetContext();
    Q_INVOKABLE void setPredictionCount(int count);
    Q_INVOKABLE void setEditMode(bool active);

    // ----- layouts -------------------------------------------------------
    Q_INVOKABLE QVariantList getLayoutRows() const;
    Q_INVOKABLE QVariantList getAvailableLayouts() const;
    Q_INVOKABLE QString getCurrentLayout() const { return m_currentLayout; }
    Q_INVOKABLE void setLayout(const QString &id);

    // ----- model ---------------------------------------------------------
    Q_INVOKABLE void savePredictionModel();
    Q_INVOKABLE void clearUserData();

    // ----- settings forwarders -------------------------------------------
    Q_INVOKABLE void setAutoSpaceAfterPunctuation(bool on) { m_autoSpaceAfterPunct = on; }
    Q_INVOKABLE void setAutoCapitalizeAfterPunctuation(bool on) { m_autoCapAfterPunct = on; }
    Q_INVOKABLE void setAutoSaveOnExit(bool on) { m_autoSaveOnExit = on; }
    Q_INVOKABLE void setAutocorrectEnabled(bool on) { m_autocorrectEnabled = on; }
    Q_INVOKABLE void setCompatMode(bool on) { m_compatManual = on; }
    Q_INVOKABLE void setCompatAutoDetect(bool on);
    Q_INVOKABLE void setMergeStrategy(const QString &s);
    Q_INVOKABLE void setPrivacyMode(bool on);

    // ----- word management (cheap forwards) ------------------------------
    Q_INVOKABLE void markGoodSuggestion(const QString &word);
    Q_INVOKABLE void markBadSuggestion(const QString &word);
    Q_INVOKABLE void blacklistWord(const QString &word);
    Q_INVOKABLE void unprefer(const QString &word);
    Q_INVOKABLE void unblacklistWord(const QString &word);
    Q_INVOKABLE void undisprefer(const QString &word);
    Q_INVOKABLE void editPrediction(const QString &oldWord, const QString &newWord);

    // ----- audio + swipe -------------------------------------------------
    Q_INVOKABLE void setAudioEnabled(bool on);
    Q_INVOKABLE void setSwipeEnabled(bool on);
    Q_INVOKABLE void setSwipeLayout(const QVariant &centers);
    Q_INVOKABLE void processSwipe(const QVariant &points);

    // ----- LATER feature stubs (keep the reused QML from erroring) --------
    Q_INVOKABLE void setDebugMode(bool) {}
    Q_INVOKABLE void clearDebugLog() {}

    Q_INVOKABLE QVariantList getSnippets();
    Q_INVOKABLE void setSnippet(int index, const QString &label, const QString &value);
    Q_INVOKABLE void insertSnippet(int index);
    Q_INVOKABLE void deleteSnippet(int index);
    Q_INVOKABLE void addSnippet();
    Q_INVOKABLE void moveSnippet(int index, int direction);

    Q_INVOKABLE QVariant getAnalytics() const;
    Q_INVOKABLE QVariant getVisualizationData() const;
    Q_INVOKABLE QVariant getWordContext(const QString &) const { return QVariantMap{}; }

    Q_INVOKABLE QVariantList getAvailablePacks() const;
    Q_INVOKABLE QVariantList getEnabledPacks() const;
    Q_INVOKABLE bool enableVocabularyPack(const QString &id);
    Q_INVOKABLE bool disableVocabularyPack(const QString &id);
    Q_INVOKABLE QString importVocabularyPack(const QString &sourceDir);
    Q_INVOKABLE QString getUserPacksDir() const;

    Q_INVOKABLE QString getDefaultExportDir() const;
    Q_INVOKABLE QString getSuggestedExportName() const;
    Q_INVOKABLE QString pickExportPath();
    Q_INVOKABLE QString pickImportPath();
    Q_INVOKABLE QString exportUserData(const QString &dest);
    Q_INVOKABLE QVariant inspectUserExport(const QString &src);
    Q_INVOKABLE QString importUserData(const QString &src);

    Q_INVOKABLE bool getTelemetryEnabled() const;
    Q_INVOKABLE void setTelemetryEnabled(bool on);
    Q_INVOKABLE void forgetTelemetryData();

    Q_INVOKABLE void checkForUpdate();
    Q_INVOKABLE void installUpdate();
    Q_INVOKABLE void dismissUpdate() {}
    Q_INVOKABLE QVariant consumeUpdateHandoff() { return QVariant(); }

signals:
    void shiftActiveChanged(bool active);
    void capsLockActiveChanged(bool active);
    void ctrlActiveChanged(bool active);
    void altActiveChanged(bool active);
    void winActiveChanged(bool active);
    void currentLayerChanged(const QString &layer);
    void predictionsChanged(const QStringList &predictions);
    void predictionsRefined(const QStringList &predictions);
    void predictionLoading(bool loading);
    void predictionCountChanged(int count);
    void layoutChanged(const QString &id);
    void layoutDataChanged(const QVariantList &rows);
    void privacyModeChanged(bool active);
    void activeContextChanged(const QString &prevWord, const QString &currentPartial);
    void llmAvailableChanged(bool available);
    void debugLogChanged(const QStringList &log);
    void snippetsChanged(const QVariantList &snippets);
    void editKeyTyped(const QString &ch);
    void editSpecialPressed(const QString &name);
    void swipeEnabledChanged(bool enabled);
    void audioEnabledChanged(bool enabled);
    // Updater (declared so the QML banner connects; not driven in the MVP).
    void updateAvailable(const QString &version, const QString &assetName, const QString &notes);
    void updateUnavailable();
    void updateInstallStarted();
    void updateInstallFailed(const QString &message);
    void updateInstallHandoffPending(const QString &version);
    void updateDownloadProgress(int bytesWritten, int total);

private slots:
    void onPredictionsReady(const QStringList &predictions);
    void onPredictionsRefined(const QStringList &predictions);

private:
    // State machine helpers.
    void pressChar(const QString &key, bool literal);
    void playClick();
    void sendKeyWithActiveMods(const QString &keyName);
    void updatePredictions();
    void updateLayer();
    void boundContext(int limit);
    void releaseStickyAll();          // press_char block #1 (no nav exception)
    void rehydrateCurrentWordFromContext();
    QStringList displayCased(const QStringList &predictions) const;
    bool inCompatMode() const;
    void loadLayouts();
    static QString mapSpecial(const QString &qmlName);

    // Privacy / password detection.
    void checkPasswordField();        // 200 ms poll
    void checkPasswordFieldSync();    // per-keystroke, rate-limited
    void setAutoPrivacy(bool detected);
    void updatePrivacyState();        // recompute m_privacy from manual || auto
    void enterPrivacyMode();          // scrub buffers + clear pills

    // Foreground-window monitor (app-switch reset + compat auto-detect).
    void checkForegroundWindow();     // 250 ms poll
    void updateCompatAuto(quintptr hwnd);

    // Modifier / layer state.
    bool m_shift = false;
    bool m_caps = false;
    bool m_ctrl = false;
    bool m_alt = false;
    bool m_win = false;
    QString m_currentLayer = QStringLiteral("lower");
    bool m_editMode = false;

    // Typing context (mirrors on-screen text).
    QString m_currentWord;
    QString m_contextBuffer;
    QString m_sentenceBuffer;
    bool m_wordTypedUnderCaps = false;
    QStringList m_predictions;
    bool m_autoSpacePending = false;

    // Settings.
    bool m_autoSpaceAfterPunct = true;
    bool m_autoCapAfterPunct = false;
    bool m_autoSaveOnExit = true;
    bool m_autocorrectEnabled = false;
    int m_predictionCount = 8;

    // Privacy: m_privacy is the effective state (manual OR auto-detected).
    bool m_privacy = false;
    bool m_privacyManual = false;
    bool m_privacyAuto = false;
    bool m_passwordDetectEnabled = true;
    qint64 m_lastSyncPasswordCheck = 0;
    QTimer *m_passwordTimer = nullptr;

    // Compat mode.
    bool m_compatManual = false;
    bool m_compatAutoEnabled = true;
    bool m_compatAutoActive = false;
    QTimer *m_foregroundTimer = nullptr;
    quintptr m_lastForegroundHwnd = 0;

    // Audio + swipe.
    bool m_audioEnabled = false;
    QString m_clickWavPath;
    SwipeRecognizer m_swipe;
    bool m_swipeEnabled = false;

    // Snippets (quick-insert text).
    SnippetStore m_snippetStore;

    // Typing analytics (session + lifetime).
    TypingAnalytics m_analytics;

    // Layouts.
    QHash<QString, QJsonObject> m_layouts;
    QStringList m_layoutOrder;
    QString m_currentLayout = QStringLiteral("qwerty");

    // Telemetry (opt-in; off + endpoint-empty by default) + update check.
    TelemetryClient *m_telemetry = nullptr;
    QTimer *m_telemetryTimer = nullptr;
    QNetworkAccessManager *m_updateNam = nullptr;

    KeySynthesizer *m_synth = nullptr;
    HybridPredictor *m_predictor = nullptr;
};
