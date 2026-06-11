#pragma once

#include <QString>
#include <QStringList>

// Abstract key-synthesis backend. Mirrors src/platform/base.py
// (KeySynthesizerBase). The Windows concrete backend drives SendInput.
//
// All key names use the X11/Qt-keysym style the bridge speaks:
//   "BackSpace", "Return", "Tab", "Escape", "space", "Delete", "Insert",
//   "Left"/"Right"/"Up"/"Down", "Home", "End", "Page_Up", "Page_Down",
//   "F1".."F12", "Num_Lock", "Scroll_Lock", "Pause", "Print", "Caps_Lock",
//   and the modifier names "ctrl"/"alt"/"shift"/"win"/"super".
class KeySynthesizer
{
public:
    virtual ~KeySynthesizer() = default;

    virtual bool isAvailable() const = 0;
    virtual QString backendName() const = 0;

    // Press+release one key, optionally with modifiers held around it. All
    // events are injected atomically (one SendInput on Windows) when
    // holdSeconds == 0. When holdSeconds > 0, the action key is held down for
    // that long between its key-down and key-up (the game-compat path: games
    // poll keyboard state per render frame, so a zero-gap down/up can fall
    // between two polls and be missed). Backends may ignore holdSeconds if they
    // can't hold a key.
    virtual void sendKey(const QString &keyName, const QStringList &modifiers = {},
                         double holdSeconds = 0.0) = 0;

    // Type a string verbatim (per-char scancode mode, Unicode fallback).
    virtual void sendText(const QString &text) = 0;

    // Press all keys in order, release in reverse (a held chord).
    virtual void sendCombination(const QStringList &keys) = 0;

    // Hold/release a modifier at the OS level so modifier+click works in the
    // target app (sticky modifiers). Default no-op.
    virtual void holdModifier(const QString &keyName) { Q_UNUSED(keyName); }
    virtual void releaseModifier(const QString &keyName) { Q_UNUSED(keyName); }

    // Select-back N chars and overwrite with text. Default impl is the simple
    // BackSpace+retype; the Windows backend overrides it with a Shift+Left
    // selection path (safer for compose areas).
    virtual void replaceText(int backspaceCount, const QString &text)
    {
        for (int i = 0; i < backspaceCount; ++i)
            sendKey(QStringLiteral("BackSpace"));
        sendText(text);
    }

    // Clear any modifier the OS thinks is held (defensive, at startup). No-op
    // on Windows (matches the Python backend).
    virtual void resetModifierState() {}
};

// Factory: returns the backend for the current platform (WindowsKeySynthesizer
// on Windows, a null backend elsewhere for now). Caller owns the pointer.
KeySynthesizer *createKeySynthesizer();
