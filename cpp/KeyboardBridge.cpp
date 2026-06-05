#include "KeyboardBridge.h"

#include "Paths.h"
#include "WinUtil.h"
#include "platform/KeySynthesizer.h"
#include "platform/PasswordDetect.h"
#include "prediction/HybridPredictor.h"

#include <QDateTime>
#include <QDir>
#include <QTimer>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QRegularExpression>
#include <QStandardPaths>

#ifdef Q_OS_WIN
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#  include <mmsystem.h>
#endif

#ifndef APP_VERSION_STR
#define APP_VERSION_STR "0.0.0"
#endif

namespace {

const QSet<QString> &noSpaceBefore()
{
    static const QSet<QString> s = {"?", "!", ".", ",", ";", ":", ")", "]", "}"};
    return s;
}

bool isSentenceEnd(const QString &ch)
{
    return ch == "." || ch == "!" || ch == "?";
}

bool isMidSentence(const QString &ch)
{
    return ch == "," || ch == ";" || ch == ":";
}

bool isWordInternal(const QString &ch)
{
    // Boundaries that split the word but get NO auto-space. Apostrophe and
    // underscore are deliberately excluded (contractions / snake_case).
    static const QSet<QString> s = {
        "-", "/", "\\", "(", "[", "{", "<", "*", "@", "#",
        "$", "%", "&", "+", "=", "~", "^", "|", "\"", "`"};
    return s.contains(ch);
}

const QSet<QString> &navKeys()
{
    static const QSet<QString> s = {
        "left", "right", "up", "down", "home", "end", "pageup", "pagedown"};
    return s;
}

} // namespace

KeyboardBridge::KeyboardBridge(QObject *parent)
    : QObject(parent)
{
    m_synth = createKeySynthesizer();
    m_synth->resetModifierState();

    m_predictor = new HybridPredictor(this);
    connect(m_predictor, &HybridPredictor::predictionsReady,
            this, &KeyboardBridge::onPredictionsReady);
    connect(m_predictor, &HybridPredictor::predictionsRefined,
            this, &KeyboardBridge::onPredictionsRefined);
    connect(m_predictor, &HybridPredictor::modelLoading,
            this, &KeyboardBridge::predictionLoading);
    connect(m_predictor, &HybridPredictor::llmAvailableChanged,
            this, &KeyboardBridge::llmAvailableChanged);

    loadLayouts();

    const QString clickPath = QDir(QDir(paths::dataDir()).filePath("sounds")).filePath("click.wav");
    if (QFileInfo::exists(clickPath))
        m_clickWavPath = clickPath;

    // Auto-pause learning on password fields: poll focus every 200 ms (the
    // per-keystroke sync check closes the gap between polls).
    m_passwordTimer = new QTimer(this);
    m_passwordTimer->setInterval(200);
    connect(m_passwordTimer, &QTimer::timeout, this, [this] { checkPasswordField(); });
    m_passwordTimer->start();

    // Foreground monitor: reset stale context on app switch + drive compat
    // auto-detect (IDE / remote-desktop insertion path).
    m_foregroundTimer = new QTimer(this);
    m_foregroundTimer->setInterval(250);
    connect(m_foregroundTimer, &QTimer::timeout, this, [this] { checkForegroundWindow(); });
    m_foregroundTimer->start();
}

KeyboardBridge::~KeyboardBridge()
{
    delete m_synth;
}

bool KeyboardBridge::synthAvailable() const
{
    return m_synth && m_synth->isAvailable();
}

QString KeyboardBridge::appVersion() const
{
    return QString::fromLatin1(APP_VERSION_STR);
}

void KeyboardBridge::shutdown()
{
    // Stop the focus poll before tearing anything down (a late timeout firing
    // against a half-destroyed bridge would crash).
    if (m_passwordTimer)
        m_passwordTimer->stop();
    if (m_foregroundTimer)
        m_foregroundTimer->stop();
    passworddetect::shutdown();

    // Release any OS-held sticky modifier so quitting doesn't pin it desktop-wide.
    if (m_shift) { m_synth->releaseModifier("shift"); m_shift = false; }
    if (m_ctrl)  { m_synth->releaseModifier("ctrl");  m_ctrl = false; }
    if (m_alt)   { m_synth->releaseModifier("alt");   m_alt = false; }
    if (m_win)   { m_synth->releaseModifier("win");   m_win = false; }
}

// ----- layouts -----------------------------------------------------------

void KeyboardBridge::loadLayouts()
{
    QDir dir(QDir(paths::dataDir()).filePath("layouts"));
    const QStringList files = dir.entryList(QStringList() << "*.json", QDir::Files, QDir::Name);
    for (const QString &name : files) {
        QFile f(dir.filePath(name));
        if (!f.open(QIODevice::ReadOnly))
            continue;
        QJsonParseError err{};
        const QJsonDocument doc = QJsonDocument::fromJson(f.readAll(), &err);
        if (err.error != QJsonParseError::NoError || !doc.isObject())
            continue;
        const QJsonObject o = doc.object();
        QString id = o.value("id").toString();
        if (id.isEmpty())
            id = QFileInfo(name).baseName();
        m_layouts.insert(id, o);
        m_layoutOrder << id;
    }
    if (!m_layouts.contains(m_currentLayout) && !m_layoutOrder.isEmpty())
        m_currentLayout = m_layoutOrder.first();
}

QVariantList KeyboardBridge::getLayoutRows() const
{
    auto it = m_layouts.constFind(m_currentLayout);
    if (it == m_layouts.constEnd())
        return {};
    return it->value("rows").toArray().toVariantList();
}

QVariantList KeyboardBridge::getAvailableLayouts() const
{
    QVariantList out;
    for (const QString &id : m_layoutOrder) {
        const QJsonObject o = m_layouts.value(id);
        QVariantMap m;
        m.insert("id", id);
        m.insert("name", o.value("name").toString(id));
        out.append(m);
    }
    return out;
}

void KeyboardBridge::setLayout(const QString &id)
{
    if (!m_layouts.contains(id) || id == m_currentLayout)
        return;
    m_currentLayout = id;
    emit layoutChanged(id);
    emit layoutDataChanged(getLayoutRows());
}

// ----- char typing -------------------------------------------------------

void KeyboardBridge::pressKey(const QString &key)
{
    pressChar(key, /*literal=*/false);
}

void KeyboardBridge::pressKeyLiteral(const QString &ch)
{
    pressChar(ch, /*literal=*/true);
}

void KeyboardBridge::pressChar(const QString &key, bool literal)
{
    const auto casedChar = [&]() -> QString {
        if (literal)
            return key;
        return (m_shift || m_caps) ? key.toUpper() : key.toLower();
    };

    playClick();

    // 1. Edit-mode intercept: route to the popup TextField, not the OS.
    if (m_editMode) {
        emit editKeyTyped(casedChar());
        if (m_shift && !m_caps) {
            m_shift = false;
            m_synth->releaseModifier("shift");
            updateLayer();
            emit shiftActiveChanged(false);
        }
        return;
    }

    checkPasswordFieldSync(); // close the 200 ms race before this key is learned

    const QString ch = casedChar();
    if (!m_privacy)
        m_analytics.recordKeystroke(ch);

    // 6. Punctuation-space cleanup: remove an auto-space we inserted.
    if (noSpaceBefore().contains(ch) && m_autoSpacePending
        && m_contextBuffer.endsWith(' ') && m_currentWord.isEmpty()) {
        m_synth->sendKey(QStringLiteral("BackSpace"));
        m_contextBuffer.chop(1);
    }
    m_autoSpacePending = false;

    // 8. Modifier-chord branch: this keystroke is a shortcut, not text.
    if (m_ctrl || m_alt || m_win) {
        sendKeyWithActiveMods(key.toLower());
        releaseStickyAll();
        return;
    }

    // 9. Normal text path.
    m_synth->sendText(ch);

    if (!m_privacy) {
        m_currentWord += ch;
        if (m_caps)
            m_wordTypedUnderCaps = true;

        if (isSentenceEnd(ch)) {
            const QString sentence = (m_sentenceBuffer + m_currentWord).trimmed();
            if (!sentence.isEmpty())
                m_predictor->learn(sentence);
            m_sentenceBuffer.clear();
            m_currentWord.clear();
            m_wordTypedUnderCaps = false;
            if (m_autoSpaceAfterPunct) {
                m_synth->sendText(QStringLiteral(" "));
                m_autoSpacePending = true;
            }
            m_contextBuffer += ch + " ";
            if (m_autoCapAfterPunct) {
                m_shift = true;
                emit shiftActiveChanged(true);
                updateLayer();
            }
            boundContext(200);
        } else if (isMidSentence(ch)) {
            QString wordBefore = m_currentWord;
            wordBefore.chop(1);
            if (!wordBefore.isEmpty()) {
                m_sentenceBuffer += wordBefore + ch + " ";
                m_contextBuffer += wordBefore + ch + " ";
            } else {
                m_contextBuffer += ch + " ";
            }
            m_currentWord.clear();
            m_wordTypedUnderCaps = false;
            if (m_autoSpaceAfterPunct) {
                m_synth->sendText(QStringLiteral(" "));
                m_autoSpacePending = true;
            }
            boundContext(200);
        } else if (isWordInternal(ch)) {
            QString wordBefore = m_currentWord;
            wordBefore.chop(1);
            if (!wordBefore.isEmpty()) {
                m_sentenceBuffer += wordBefore + ch;
                m_contextBuffer += wordBefore + ch;
            } else {
                m_contextBuffer += ch;
            }
            m_currentWord.clear();
            m_wordTypedUnderCaps = false;
            boundContext(200);
        }

        // Prediction trigger (runs for every char).
        if (ch.size() == 1 && ch.at(0).isLetter()) {
            updatePredictions();
        } else {
            m_predictions.clear();
            emit predictionsChanged({});
        }
    }

    releaseStickyAll(); // auto-release block #1
}

void KeyboardBridge::pressSpecialKey(const QString &keyName)
{
    playClick();

    if (m_editMode) {
        emit editSpecialPressed(keyName.toLower());
        return;
    }

    checkPasswordFieldSync();

    m_autoSpacePending = false;
    const QString lower = keyName.toLower();
    const QString mapped = mapSpecial(lower);

    sendKeyWithActiveMods(mapped);

    if (!m_privacy) {
        if (lower == QLatin1String("space")) {
            if (!m_currentWord.isEmpty()) {
                m_predictor->recordTypedWord(m_currentWord);
                m_analytics.recordWordCompleted(m_currentWord);
                m_predictor->learnCapitalization(m_currentWord, !m_wordTypedUnderCaps);
                m_sentenceBuffer += m_currentWord + " ";
                m_contextBuffer += m_currentWord + " ";
                m_predictor->learn(m_sentenceBuffer.trimmed());
                boundContext(200);
                m_currentWord.clear();
                m_wordTypedUnderCaps = false;
                updatePredictions();
            }
        } else if (lower == QLatin1String("backspace")) {
            m_analytics.recordBackspace();
            if (!m_currentWord.isEmpty()) {
                m_currentWord.chop(1);
                if (m_currentWord.isEmpty())
                    m_wordTypedUnderCaps = false;
                updatePredictions();
            } else if (!m_contextBuffer.isEmpty()) {
                m_contextBuffer.chop(1);
                rehydrateCurrentWordFromContext();
                updatePredictions();
            }
        } else if (lower == QLatin1String("return")) {
            if (!m_currentWord.isEmpty()) {
                m_analytics.recordWordCompleted(m_currentWord);
                m_sentenceBuffer += m_currentWord + " ";
            }
            if (!m_sentenceBuffer.trimmed().isEmpty())
                m_predictor->learn(m_sentenceBuffer.trimmed());
            m_sentenceBuffer.clear();
            if (!m_currentWord.isEmpty())
                m_contextBuffer += m_currentWord + " ";
            boundContext(200);
            m_currentWord.clear();
            m_wordTypedUnderCaps = false;
            updatePredictions();
        }
    }

    // Auto-release block #2: keep Shift/Ctrl held on nav keys (selection /
    // word-jump persist across presses); Alt/Win are always one-shot.
    const bool keepSelection = navKeys().contains(lower);
    if (m_shift && !m_caps && !keepSelection) {
        m_shift = false;
        m_synth->releaseModifier("shift");
        updateLayer();
        emit shiftActiveChanged(false);
    }
    if (m_ctrl && !keepSelection) {
        m_ctrl = false;
        m_synth->releaseModifier("ctrl");
        emit ctrlActiveChanged(false);
    }
    if (m_alt) {
        m_alt = false;
        m_synth->releaseModifier("alt");
        emit altActiveChanged(false);
    }
    if (m_win) {
        m_win = false;
        m_synth->releaseModifier("win");
        emit winActiveChanged(false);
    }
}

QString KeyboardBridge::mapSpecial(const QString &qmlName)
{
    static const QHash<QString, QString> m = {
        {"backspace", "BackSpace"}, {"return", "Return"}, {"space", "space"},
        {"tab", "Tab"}, {"escape", "Escape"},
        {"left", "Left"}, {"right", "Right"}, {"up", "Up"}, {"down", "Down"},
        {"delete", "Delete"}, {"home", "Home"}, {"end", "End"},
        {"pageup", "Page_Up"}, {"pagedown", "Page_Down"}, {"insert", "Insert"},
        {"f1", "F1"}, {"f2", "F2"}, {"f3", "F3"}, {"f4", "F4"}, {"f5", "F5"},
        {"f6", "F6"}, {"f7", "F7"}, {"f8", "F8"}, {"f9", "F9"}, {"f10", "F10"},
        {"f11", "F11"}, {"f12", "F12"},
        {"print", "Print"}, {"scrolllock", "Scroll_Lock"}, {"pause", "Pause"},
        {"numlock", "Num_Lock"}};
    return m.value(qmlName, qmlName);
}

void KeyboardBridge::sendKeyWithActiveMods(const QString &keyName)
{
    QStringList mods;
    if (m_shift) mods << "shift";
    if (m_ctrl)  mods << "ctrl";
    if (m_alt)   mods << "alt";
    if (m_win)   mods << "win";
    m_synth->sendKey(keyName, mods.isEmpty() ? QStringList() : mods);
}

void KeyboardBridge::playClick()
{
#ifdef Q_OS_WIN
    if (m_audioEnabled && !m_clickWavPath.isEmpty())
        PlaySoundW(reinterpret_cast<LPCWSTR>(m_clickWavPath.utf16()), nullptr,
                   SND_FILENAME | SND_ASYNC | SND_NODEFAULT);
#endif
}

void KeyboardBridge::setAudioEnabled(bool on)
{
    if (on == m_audioEnabled)
        return;
    m_audioEnabled = on;
    emit audioEnabledChanged(on);
}

void KeyboardBridge::setSwipeEnabled(bool on)
{
    if (on == m_swipeEnabled)
        return;
    m_swipeEnabled = on;
    emit swipeEnabledChanged(on);
}

void KeyboardBridge::setSwipeLayout(const QVariant &centers)
{
    QHash<QChar, QPointF> layout;
    const QVariantMap m = centers.toMap();
    for (auto it = m.constBegin(); it != m.constEnd(); ++it) {
        if (it.key().isEmpty())
            continue;
        const QVariantList xy = it.value().toList();
        if (xy.size() >= 2)
            layout.insert(it.key().at(0), QPointF(xy[0].toDouble(), xy[1].toDouble()));
    }
    m_swipe.setLayout(layout);
}

void KeyboardBridge::processSwipe(const QVariant &points)
{
    if (!m_swipeEnabled || m_privacy)
        return;

    QVector<QPointF> trace;
    const QVariantList pts = points.toList();
    for (const QVariant &e : pts) {
        const QVariantList xy = e.toList();
        if (xy.size() >= 2)
            trace.append(QPointF(xy[0].toDouble(), xy[1].toDouble()));
        else if (e.canConvert<QPointF>())
            trace.append(e.toPointF());
    }
    if (trace.size() < 4)
        return;

    const QStringList results = m_swipe.decode(trace, m_predictor->ngram()->unigrams(), 8);
    if (results.isEmpty())
        return;

    const QString top = results.first();
    const QString capped = m_predictor->getCapitalized(top);
    m_synth->sendText(capped + " ");
    m_autoSpacePending = true;

    m_predictor->learnFromSelection(m_contextBuffer, top);
    m_contextBuffer += capped + " ";
    m_sentenceBuffer += capped + " ";
    boundContext(200);
    m_currentWord.clear();
    m_wordTypedUnderCaps = false;

    m_predictions = displayCased(results);
    emit predictionsChanged(m_predictions);
}

void KeyboardBridge::releaseStickyAll()
{
    if (m_shift && !m_caps) {
        m_shift = false;
        m_synth->releaseModifier("shift");
        updateLayer();
        emit shiftActiveChanged(false);
    }
    if (m_ctrl) {
        m_ctrl = false;
        m_synth->releaseModifier("ctrl");
        emit ctrlActiveChanged(false);
    }
    if (m_alt) {
        m_alt = false;
        m_synth->releaseModifier("alt");
        emit altActiveChanged(false);
    }
    if (m_win) {
        m_win = false;
        m_synth->releaseModifier("win");
        emit winActiveChanged(false);
    }
}

void KeyboardBridge::rehydrateCurrentWordFromContext()
{
    if (m_contextBuffer.isEmpty())
        return;
    const QChar last = m_contextBuffer.back();
    if (last == ' ' || last == '\n' || last == '\t')
        return;
    const int lastWs = qMax(m_contextBuffer.lastIndexOf(' '),
                            qMax(m_contextBuffer.lastIndexOf('\n'),
                                 m_contextBuffer.lastIndexOf('\t')));
    m_currentWord = m_contextBuffer.mid(lastWs + 1);
    m_contextBuffer = lastWs >= 0 ? m_contextBuffer.left(lastWs + 1) : QString();
    if (!m_currentWord.isEmpty() && !m_privacy)
        m_predictor->unlearnWord(m_currentWord);
}

// ----- modifiers / layer -------------------------------------------------

void KeyboardBridge::toggleShift()
{
    m_shift = !m_shift;
    if (m_shift)
        m_synth->holdModifier("shift");
    else
        m_synth->releaseModifier("shift");
    updateLayer();
    emit shiftActiveChanged(m_shift);
}

void KeyboardBridge::toggleCapsLock()
{
    m_caps = !m_caps; // independent of shift
    updateLayer();
    emit capsLockActiveChanged(m_caps);
    if (!m_predictions.isEmpty())
        updatePredictions(); // re-query so visible pills re-case
}

void KeyboardBridge::toggleCtrl()
{
    m_ctrl = !m_ctrl;
    if (m_ctrl)
        m_synth->holdModifier("ctrl");
    else
        m_synth->releaseModifier("ctrl");
    emit ctrlActiveChanged(m_ctrl);
}

void KeyboardBridge::toggleAlt()
{
    m_alt = !m_alt;
    if (m_alt)
        m_synth->holdModifier("alt");
    else
        m_synth->releaseModifier("alt");
    emit altActiveChanged(m_alt);
}

void KeyboardBridge::toggleWin()
{
    m_win = !m_win;
    if (m_win)
        m_synth->holdModifier("win");
    else
        m_synth->releaseModifier("win");
    emit winActiveChanged(m_win);
}

void KeyboardBridge::switchLayer(const QString &layer)
{
    if (layer == m_currentLayer)
        return;
    m_currentLayer = layer;
    emit currentLayerChanged(layer);
}

void KeyboardBridge::updateLayer()
{
    if (m_currentLayer == QLatin1String("numbers") || m_currentLayer == QLatin1String("symbols"))
        return; // don't disturb a user-selected layer
    const QString next = (m_shift || m_caps) ? QStringLiteral("upper") : QStringLiteral("lower");
    if (next != m_currentLayer) {
        m_currentLayer = next;
        emit currentLayerChanged(next);
    }
}

// ----- predictions -------------------------------------------------------

void KeyboardBridge::updatePredictions()
{
    const QString context = m_contextBuffer + m_currentWord;
    m_predictor->predictWithRefinement(context, m_predictionCount);

    if (!m_privacy) {
        const QStringList toks = NgramPredictor::tokenize(context);
        const QString prevWord = toks.isEmpty() ? QString() : toks.last();
        emit activeContextChanged(prevWord, m_currentWord.toLower());
    }
}

void KeyboardBridge::onPredictionsReady(const QStringList &predictions)
{
    m_predictions = displayCased(predictions);
    if (!m_predictions.isEmpty())
        m_analytics.recordPredictionOffered();
    emit predictionsChanged(m_predictions);
}

void KeyboardBridge::onPredictionsRefined(const QStringList &predictions)
{
    m_predictions = displayCased(predictions);
    emit predictionsRefined(m_predictions);
}

QStringList KeyboardBridge::displayCased(const QStringList &predictions) const
{
    if (predictions.isEmpty())
        return predictions;

    if (m_caps) {
        QStringList out;
        out.reserve(predictions.size());
        for (const QString &w : predictions)
            out << w.toUpper();
        return out;
    }

    const QString &cw = m_currentWord;
    bool anyUpper = false;
    for (QChar c : cw)
        if (c.isUpper()) { anyUpper = true; break; }
    if (!anyUpper)
        return predictions;

    // Mirror every uppercase position of the typed prefix onto each pill.
    QStringList out;
    out.reserve(predictions.size());
    for (const QString &w : predictions) {
        QString built;
        built.reserve(w.size());
        for (int i = 0; i < w.size(); ++i) {
            if (i < cw.size() && cw.at(i).isUpper())
                built += w.at(i).toUpper();
            else
                built += w.at(i);
        }
        out << built;
    }
    return out;
}

void KeyboardBridge::pressPrediction(const QString &word)
{
    const int idx = m_predictions.indexOf(word);
    const int rank = idx >= 0 ? idx + 1 : 1;
    const int saved = word.size() - m_currentWord.size() + 1; // +1 for the auto-space
    m_analytics.recordPredictionSelected(word, rank, qMax(0, saved));

    if (inCompatMode() && !m_currentWord.isEmpty()) {
        for (int i = 0; i < m_currentWord.size(); ++i)
            m_synth->sendKey(QStringLiteral("BackSpace"));
        m_synth->sendText(word + " ");
    } else if (!m_currentWord.isEmpty() && word.startsWith(m_currentWord)) {
        m_synth->sendText(word.mid(m_currentWord.size()) + " ");
    } else if (m_currentWord.isEmpty()) {
        m_synth->sendText(word + " ");
    } else {
        m_synth->replaceText(m_currentWord.size(), word + " ");
    }
    m_autoSpacePending = true;

    m_predictor->learnFromSelection(m_contextBuffer, word);
    if (!m_currentWord.isEmpty() && m_currentWord != m_currentWord.toLower())
        m_predictor->learnCapitalization(word, !m_wordTypedUnderCaps);

    m_contextBuffer += word + " ";
    boundContext(100);
    m_currentWord.clear();
    m_wordTypedUnderCaps = false;

    m_predictions.clear();
    emit predictionsChanged({});

    const QStringList next = m_predictor->predict(m_contextBuffer, m_predictionCount);
    m_predictions = displayCased(next);
    emit predictionsChanged(m_predictions);
}

void KeyboardBridge::clearPredictions()
{
    m_predictions.clear();
    emit predictionsChanged({});
}

void KeyboardBridge::resetContext()
{
    m_currentWord.clear();
    m_contextBuffer.clear();
    m_sentenceBuffer.clear();
    m_wordTypedUnderCaps = false;
    m_predictions.clear();
    emit predictionsChanged({});
}

void KeyboardBridge::setPredictionCount(int count)
{
    const int clamped = qBound(1, count, 10);
    if (clamped == m_predictionCount)
        return;
    m_predictionCount = clamped;
    emit predictionCountChanged(clamped);
}

void KeyboardBridge::setEditMode(bool active)
{
    m_editMode = active;
}

void KeyboardBridge::boundContext(int limit)
{
    if (m_contextBuffer.size() > limit)
        m_contextBuffer = m_contextBuffer.right(limit);
}

bool KeyboardBridge::inCompatMode() const
{
    // Auto-detect (foreground-window inspection) is deferred; manual toggle works.
    return m_compatManual || (m_compatAutoEnabled && m_compatAutoActive);
}

// ----- model / settings --------------------------------------------------

void KeyboardBridge::savePredictionModel()
{
    m_predictor->save();
}

void KeyboardBridge::clearUserData()
{
    m_predictor->clearUserData();
    m_predictor->save();
}

void KeyboardBridge::setMergeStrategy(const QString &s)
{
    m_predictor->setMergeStrategy(s);
}

void KeyboardBridge::setPrivacyMode(bool on)
{
    m_privacyManual = on; // manual override; auto-detection layers on top
    updatePrivacyState();
}

void KeyboardBridge::checkPasswordField()
{
    if (m_passwordDetectEnabled)
        setAutoPrivacy(passworddetect::isPasswordField());
}

void KeyboardBridge::checkPasswordFieldSync()
{
    if (!m_passwordDetectEnabled)
        return;
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    if (now - m_lastSyncPasswordCheck < 50) // rate-limit the hot path
        return;
    m_lastSyncPasswordCheck = now;
    setAutoPrivacy(passworddetect::isPasswordField());
}

void KeyboardBridge::setAutoPrivacy(bool detected)
{
    if (detected == m_privacyAuto)
        return;
    m_privacyAuto = detected;
    updatePrivacyState();
}

void KeyboardBridge::updatePrivacyState()
{
    const bool effective = m_privacyManual || m_privacyAuto;
    if (effective == m_privacy)
        return;
    m_privacy = effective;
    if (effective)
        enterPrivacyMode();
    emit privacyModeChanged(effective);
}

void KeyboardBridge::enterPrivacyMode()
{
    m_currentWord.clear();
    m_contextBuffer.clear();
    m_sentenceBuffer.clear();
    m_predictions.clear();
    emit predictionsChanged({});
}

void KeyboardBridge::checkForegroundWindow()
{
    const quintptr hwnd = winutil::foregroundWindowId();
    if (hwnd == 0)
        return; // detection unavailable on this platform
    if (hwnd != m_lastForegroundHwnd && m_lastForegroundHwnd != 0) {
        // App switch: the typing context is stale for the new window.
        m_predictions.clear();
        m_currentWord.clear();
        m_wordTypedUnderCaps = false;
        m_sentenceBuffer.clear();
        m_contextBuffer.clear();
        emit predictionsChanged({});
    }
    updateCompatAuto(hwnd);
    m_lastForegroundHwnd = hwnd;
}

void KeyboardBridge::updateCompatAuto(quintptr hwnd)
{
    if (!m_compatAutoEnabled || !hwnd)
        return;
    const bool active = winutil::windowNeedsCompatMode(hwnd);
    if (active != m_compatAutoActive)
        m_compatAutoActive = active; // debounced: only toggles on change
}

void KeyboardBridge::setCompatAutoDetect(bool on)
{
    m_compatAutoEnabled = on;
    if (on)
        updateCompatAuto(m_lastForegroundHwnd);
}

// ----- word management ---------------------------------------------------

void KeyboardBridge::markGoodSuggestion(const QString &word)
{
    m_predictor->markGoodSuggestion(word);
}

void KeyboardBridge::markBadSuggestion(const QString &word)
{
    m_predictor->markBadSuggestion(word);
}

void KeyboardBridge::blacklistWord(const QString &word)
{
    m_predictor->blacklistWord(word);
}

void KeyboardBridge::unprefer(const QString &word)
{
    m_predictor->unprefer(word);
}

void KeyboardBridge::unblacklistWord(const QString &word)
{
    m_predictor->unblacklistWord(word);
}

void KeyboardBridge::undisprefer(const QString &word)
{
    m_predictor->removeDispreference(word);
}

void KeyboardBridge::editPrediction(const QString &oldWord, const QString &newWord)
{
    QString sanitized = newWord;
    sanitized.remove(QRegularExpression("[\\x00-\\x1f]"));
    sanitized = sanitized.left(64);
    if (sanitized.isEmpty())
        return;
    m_predictor->setCapitalization(oldWord, sanitized);
    const QStringList next = m_predictor->predict(m_contextBuffer + m_currentWord, m_predictionCount);
    m_predictions = displayCased(next);
    emit predictionsChanged(m_predictions);
}

// ----- misc stubs that need a real value ---------------------------------

QVariant KeyboardBridge::getAnalytics() const
{
    return m_analytics.getSessionStats();
}

void KeyboardBridge::saveAnalytics()
{
    m_analytics.save();
}

QVariant KeyboardBridge::getVisualizationData() const
{
    NgramPredictor *ng = m_predictor->ngram();

    QVariantMap stats;
    stats.insert("preferredCount", ng->preferred().size());
    stats.insert("blacklistCount", ng->blacklist().size());
    stats.insert("dispreferenceCount", ng->dispreference().size());

    QVariantList preferredList;
    for (auto it = ng->preferred().constBegin(); it != ng->preferred().constEnd(); ++it) {
        QVariantMap m;
        m.insert("word", it.key());
        m.insert("count", it.value());
        preferredList.append(m);
    }
    stats.insert("preferred", preferredList);

    QStringList bl = ng->blacklist().values();
    bl.sort();
    stats.insert("blacklist", bl);

    QVariantList dispList;
    for (auto it = ng->dispreference().constBegin(); it != ng->dispreference().constEnd(); ++it) {
        QVariantMap m;
        m.insert("word", it.key());
        m.insert("count", it.value());
        dispList.append(m);
    }
    stats.insert("dispreference", dispList);

    QVariantMap viz;
    viz.insert("stats", stats);
    return viz;
}

QVariantList KeyboardBridge::getAvailablePacks() const
{
    return m_predictor->getAvailablePacks();
}

QVariantList KeyboardBridge::getEnabledPacks() const
{
    QVariantList out;
    for (const QString &id : m_predictor->getEnabledPacks())
        out << id;
    return out;
}

bool KeyboardBridge::enableVocabularyPack(const QString &id)
{
    return m_predictor->enableVocabularyPack(id);
}

bool KeyboardBridge::disableVocabularyPack(const QString &id)
{
    return m_predictor->disableVocabularyPack(id);
}

QString KeyboardBridge::importVocabularyPack(const QString &sourceDir)
{
    return m_predictor->importVocabularyPack(sourceDir);
}

QString KeyboardBridge::getUserPacksDir() const
{
    return m_predictor->getUserPacksDir();
}

QString KeyboardBridge::getDefaultExportDir() const
{
    const QString docs = QStandardPaths::writableLocation(QStandardPaths::DocumentsLocation);
    return docs.isEmpty() ? QStandardPaths::writableLocation(QStandardPaths::HomeLocation) : docs;
}

QString KeyboardBridge::getSuggestedExportName() const
{
    const QString ts = QDateTime::currentDateTime().toString("yyyy-MM-dd-HHmmss");
    return QStringLiteral("Alpha-OSK-Export-%1.zip").arg(ts);
}

// ----- snippets ----------------------------------------------------------

QVariantList KeyboardBridge::getSnippets()
{
    return m_snippetStore.getAll();
}

void KeyboardBridge::setSnippet(int index, const QString &label, const QString &value)
{
    m_snippetStore.set(index, label, value);
    emit snippetsChanged(m_snippetStore.getAll());
}

void KeyboardBridge::addSnippet()
{
    m_snippetStore.add(QStringLiteral("New"), QString());
    emit snippetsChanged(m_snippetStore.getAll());
}

void KeyboardBridge::deleteSnippet(int index)
{
    m_snippetStore.remove(index);
    emit snippetsChanged(m_snippetStore.getAll());
}

void KeyboardBridge::moveSnippet(int index, int direction)
{
    m_snippetStore.move(index, direction);
    emit snippetsChanged(m_snippetStore.getAll());
}

void KeyboardBridge::insertSnippet(int index)
{
    if (m_editMode)
        return; // never fire while a snippet field is being edited
    const QString value = m_snippetStore.getValue(index);
    if (value.isEmpty())
        return; // empty slot: the QML opens the editor instead of inserting
    // Verbatim insert (same path swipe / predictions use). Deliberately NOT
    // gated by privacy mode -- dropping an address into a form is a valid need.
    m_synth->sendText(value);
    // Clear typing state so the inserted punctuation/newlines can't corrupt the
    // next prediction's prefix matching.
    m_currentWord.clear();
    m_predictions.clear();
    emit predictionsChanged({});
}
