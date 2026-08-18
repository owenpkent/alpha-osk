import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// Editor for one programmable function key.
//
// A Popup rather than the floating Window the snippets editor uses, and
// deliberately so: that window exists to be dragged clear of the field
// the user is filling in, and this one is not editing anything in the
// app behind us. It is kept **short and parked at the top** for the
// reason that matters instead: the user has to click OSK keys to type a
// label, so the editor must not cover the letter grid it is being typed
// with. The three rows below are the whole budget.
//
// Two invariants inherited from the prediction-edit popup, both
// load-bearing and both easy to undo by accident:
//
//   * `modal: false`. A modal popup installs an event-blocking overlay,
//     which swallows the MouseArea clicks on the keys below, so no OSK
//     key would fire and the field could never be typed into.
//   * `closePolicy: Popup.CloseOnEscape` only. Every OSK key click is a
//     press-outside, so CloseOnPressOutside would slam this shut on the
//     first keystroke.
//
// Keystrokes arrive through the bridge's edit-mode intercept
// (`setEditMode(true)` on open, the `editKeyTyped` / `editSpecialPressed`
// signals while open), never through Qt focus: the keyboard window
// cannot hold OS focus, so focus-driven text entry does not work here at
// all.
Popup {
    id: editor

    // --- Inputs -------------------------------------------------------
    property string keyId: ""                  // "f13"
    property var actionTypes: []               // bridge's registry
    property var unboundKeys: []               // key ids nothing binds
    property var chordFn: null                 // root.applyEditChord
    property var savedFn: null                 // flash the "Saved" toast
    property var problemFn: null               // flash the failure toast

    property color bgColor: "#252535"
    property color fieldColor: "#1a1a2a"
    property color inkColor: "#f0f0f0"
    property color accentColor: "#4a9eff"
    property color borderColor: "#505050"
    property var inkOnFn: null                 // root.inkOn

    // --- Working state ------------------------------------------------
    property string typeId: "key"
    property string labelText: ""
    property string chordKey: ""
    property var chordMods: []
    property string textValue: ""
    // Which control the OSK types into. "chord" is a capture mode rather
    // than a text field: the next key tapped on the keyboard becomes the
    // chord's action key, which is the only way to name Enter or an arrow
    // without a second picker listing every key we can send.
    property string editTarget: "label"

    readonly property bool isUnbound: unboundKeys.indexOf(keyId) >= 0
    readonly property var typeInfo: {
        for (var i = 0; i < actionTypes.length; ++i)
            if (actionTypes[i].id === typeId)
                return actionTypes[i]
        return null
    }
    readonly property var typeFields: typeInfo ? typeInfo.fields : []

    function _has(field) {
        return typeFields.indexOf(field) >= 0
    }

    function _modLabel(m) {
        return m === "ctrl" ? "Ctrl" : m === "alt" ? "Alt"
             : m === "shift" ? "Shift" : "Win"
    }

    function _chordSummary() {
        if (!chordKey)
            return "Tap Key, then press a key on the keyboard"
        var parts = []
        var order = ["ctrl", "alt", "shift", "win"]
        for (var i = 0; i < order.length; ++i)
            if (chordMods.indexOf(order[i]) >= 0)
                parts.push(_modLabel(order[i]))
        parts.push(chordKey.length === 1 ? chordKey.toUpperCase()
                                         : chordKey.charAt(0).toUpperCase() + chordKey.slice(1))
        return parts.join("+")
    }

    function _toggleMod(m) {
        var next = []
        var found = false
        for (var i = 0; i < chordMods.length; ++i) {
            if (chordMods[i] === m) { found = true; continue }
            next.push(chordMods[i])
        }
        if (!found)
            next.push(m)
        chordMods = next
    }

    // Load *existing* (the bridge's stored record, or null) into the
    // working state. Called by the opener rather than bound, so a
    // half-finished edit is never overwritten by a store update landing
    // underneath it.
    function loadFor(id, existing) {
        keyId = id
        labelText = (existing && existing.label) ? existing.label : ""
        typeId = (existing && existing.type) ? existing.type : "key"
        chordKey = (existing && existing.key) ? existing.key : ""
        chordMods = (existing && existing.modifiers) ? existing.modifiers.slice() : []
        textValue = (existing && existing.text) ? existing.text : ""
        editTarget = "label"
        open()
    }

    function _save() {
        var payload = { "type": typeId }
        if (labelText.trim())
            payload["label"] = labelText.trim()
        if (typeId === "hotkey") {
            payload["key"] = chordKey
            payload["modifiers"] = chordMods
        } else if (typeId === "text") {
            payload["text"] = textValue
        }
        // "Send the key" with no label is not an assignment at all, it is
        // the default. Storing it would leave an entry that changes
        // nothing, so it clears instead: Reset and this branch then agree
        // about what an unprogrammed key looks like on disk.
        var ok = (typeId === "key" && !labelText.trim())
                 ? (keyboard.clearKeyAction(keyId) || true)
                 : keyboard.setKeyAction(keyId, payload)
        // The bool is honoured rather than assumed: the store refuses a
        // hotkey with no action key, and flashing "Saved" over a write
        // that never happened is the failure setSnippet was given a bool
        // return for.
        if (ok) {
            if (savedFn) savedFn()
            close()
        } else if (problemFn) {
            problemFn()
        }
    }

    function _reset() {
        keyboard.clearKeyAction(keyId)
        close()
    }

    // Sized and centred against the overlay rather than a `root` id:
    // a component in this directory cannot see Main.qml's root.
    parent: Overlay.overlay
    x: parent ? (parent.width - width) / 2 : 0
    y: 36
    width: parent ? Math.min(parent.width - 24, 560) : 560
    padding: 10
    modal: false
    dim: false
    closePolicy: Popup.CloseOnEscape
    // The same value as a plain int, purely so the headless test can read
    // it: PySide has no converter for
    // QFlags<QQuickPopup::ClosePolicyFlag>, so reading `closePolicy` from
    // Python raises rather than returning a number, and an assertion on
    // the real property errors instead of guarding anything. Bound to
    // `closePolicy` rather than restating the constant, so it cannot
    // drift from what the popup actually does.
    readonly property int closePolicyBits: closePolicy

    onOpened: if (keyboard) keyboard.setEditMode(true)
    onClosed: if (keyboard) keyboard.setEditMode(false)

    background: Rectangle {
        color: editor.bgColor
        border.color: editor.accentColor
        border.width: 1.5
        radius: 10
    }

    Connections {
        target: keyboard
        enabled: editor.opened

        function onEditKeyTyped(ch) {
            if (editor.editTarget === "chord") {
                editor.chordKey = ch.toLowerCase()
                return
            }
            var f = editor.editTarget === "text" ? textField : labelField
            if (f.selectedText)
                f.remove(f.selectionStart, f.selectionEnd)
            f.insert(f.cursorPosition, ch)
        }

        function onEditSpecialPressed(name) {
            if (name === "escape") {
                editor.close()
                return
            }
            if (editor.editTarget === "chord") {
                // Every other special key names itself as the chord's
                // action key, which is how Ctrl+Enter or Alt+Left get set
                // without a picker listing every sendable key.
                editor.chordKey = name
                return
            }
            var f = editor.editTarget === "text" ? textField : labelField
            var pos = f.cursorPosition
            var len = f.length
            if (editor.chordFn && editor.chordFn(f, name)) return
            if (name === "backspace") {
                if (f.selectedText) f.remove(f.selectionStart, f.selectionEnd)
                else if (pos > 0) f.remove(pos - 1, pos)
            } else if (name === "delete") {
                if (f.selectedText) f.remove(f.selectionStart, f.selectionEnd)
                else if (pos < len) f.remove(pos, pos + 1)
            } else if (name === "left") {
                f.cursorPosition = Math.max(0, pos - 1)
            } else if (name === "right") {
                f.cursorPosition = Math.min(len, pos + 1)
            } else if (name === "home") {
                f.cursorPosition = 0
            } else if (name === "end") {
                f.cursorPosition = len
            } else if (name === "space") {
                if (f.selectedText) f.remove(f.selectionStart, f.selectionEnd)
                f.insert(f.cursorPosition, " ")
            } else if (name === "tab") {
                // The only key-driven way to change field: this window
                // cannot hold OS focus, so without it the other box is
                // reachable only by landing a click on it.
                editor.editTarget = editor.editTarget === "label" && editor._has("text")
                                    ? "text" : "label"
            } else if (name === "return" || name === "enter") {
                editor._save()
            }
        }
    }

    contentItem: ColumnLayout {
        id: editorLayout
        spacing: 8

        // --- Row 1: which key, what kind of action, close -------------
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Text {
                text: editor.keyId.toUpperCase()
                color: editor.inkColor
                font.pixelSize: 15
                font.weight: Font.Bold
                textFormat: Text.PlainText
            }

            Text {
                visible: editor.isUnbound
                text: "free to reassign"
                color: Qt.rgba(editor.inkColor.r, editor.inkColor.g, editor.inkColor.b, 0.55)
                font.pixelSize: 10
                textFormat: Text.PlainText
            }

            Item { Layout.fillWidth: true }

            // The action-type picker is built from the bridge's registry,
            // never from a list here, so a new type in
            // key_actions.ACTION_TYPES appears with no QML edit.
            Repeater {
                model: editor.actionTypes

                Rectangle {
                    width: typeLabel.implicitWidth + 16
                    height: 24
                    radius: 12
                    color: editor.typeId === modelData.id ? editor.accentColor : editor.fieldColor
                    border.color: editor.typeId === modelData.id ? editor.accentColor : editor.borderColor
                    border.width: 1

                    Text {
                        id: typeLabel
                        anchors.centerIn: parent
                        text: modelData.label
                        font.pixelSize: 11
                        textFormat: Text.PlainText
                        color: editor.typeId === modelData.id && editor.inkOnFn
                               ? editor.inkOnFn(editor.accentColor) : editor.inkColor
                    }

                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            editor.typeId = modelData.id
                            editor.editTarget = "label"
                        }
                    }
                }
            }

            Rectangle {
                width: 24; height: 24; radius: 6
                color: closeMa.containsMouse ? editor.fieldColor : "transparent"
                border.color: editor.borderColor
                border.width: 1

                Text {
                    anchors.centerIn: parent
                    text: "✕"
                    font.pixelSize: 12
                    color: editor.inkColor
                    textFormat: Text.PlainText
                }

                MouseArea {
                    id: closeMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: editor.close()
                }
            }
        }

        // --- Row 2: keycap label, and the type's own field -------------
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Text {
                text: "Label"
                color: editor.inkColor
                font.pixelSize: 11
                textFormat: Text.PlainText
            }

            TextField {
                id: labelField
                objectName: "keyActionLabelField"
                Layout.preferredWidth: 110
                Layout.preferredHeight: 28
                text: editor.labelText
                color: editor.inkColor
                font.pixelSize: 13
                maximumLength: 12
                leftPadding: 8
                rightPadding: 8
                selectByMouse: true
                verticalAlignment: Text.AlignVCenter
                onTextChanged: editor.labelText = text

                background: Rectangle {
                    color: editor.fieldColor
                    radius: 6
                    border.width: 1
                    border.color: editor.editTarget === "label"
                                  ? editor.accentColor : editor.borderColor
                }

                // `mouse.accepted = false` is load-bearing: a MouseArea's
                // whole job is to consume the press, so without this the
                // caret placement, double-click-for-a-word and drag-select
                // this field advertises are all dead behind the overlay
                // that records which box the OSK types into. Any future
                // overlay on an input has to do the same.
                MouseArea {
                    anchors.fill: parent
                    onPressed: function(mouse) {
                        editor.editTarget = "label"
                        mouse.accepted = false
                    }
                }
            }

            // Hotkey: modifier chips plus a capture slot for the action key.
            Repeater {
                model: editor._has("chord") ? ["ctrl", "alt", "shift", "win"] : []

                Rectangle {
                    width: 44
                    height: 28
                    radius: 6
                    color: editor.chordMods.indexOf(modelData) >= 0
                           ? editor.accentColor : editor.fieldColor
                    border.color: editor.chordMods.indexOf(modelData) >= 0
                                  ? editor.accentColor : editor.borderColor
                    border.width: 1

                    Text {
                        anchors.centerIn: parent
                        text: editor._modLabel(modelData)
                        font.pixelSize: 10
                        textFormat: Text.PlainText
                        color: editor.chordMods.indexOf(modelData) >= 0 && editor.inkOnFn
                               ? editor.inkOnFn(editor.accentColor) : editor.inkColor
                    }

                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: editor._toggleMod(modelData)
                    }
                }
            }

            Rectangle {
                visible: editor._has("chord")
                Layout.preferredWidth: 66
                Layout.preferredHeight: 28
                radius: 6
                color: editor.fieldColor
                border.width: 1
                border.color: editor.editTarget === "chord"
                              ? editor.accentColor : editor.borderColor

                Text {
                    anchors.centerIn: parent
                    text: editor.chordKey
                          ? (editor.chordKey.length === 1 ? editor.chordKey.toUpperCase()
                                                          : editor.chordKey)
                          : "Key"
                    font.pixelSize: 12
                    textFormat: Text.PlainText
                    color: editor.chordKey
                           ? editor.inkColor
                           : Qt.rgba(editor.inkColor.r, editor.inkColor.g,
                                     editor.inkColor.b, 0.5)
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: editor.editTarget = "chord"
                }
            }

            // Type text: one field, the phrase this key inserts.
            TextField {
                id: textField
                objectName: "keyActionTextField"
                visible: editor._has("text")
                Layout.fillWidth: true
                Layout.preferredHeight: 28
                text: editor.textValue
                color: editor.inkColor
                font.pixelSize: 13
                maximumLength: 500
                leftPadding: 8
                rightPadding: 8
                selectByMouse: true
                verticalAlignment: Text.AlignVCenter
                placeholderText: "Text this key types"
                onTextChanged: editor.textValue = text

                background: Rectangle {
                    color: editor.fieldColor
                    radius: 6
                    border.width: 1
                    border.color: editor.editTarget === "text"
                                  ? editor.accentColor : editor.borderColor
                }

                MouseArea {
                    anchors.fill: parent
                    onPressed: function(mouse) {
                        editor.editTarget = "text"
                        mouse.accepted = false
                    }
                }
            }

            Item { Layout.fillWidth: editor._has("chord") }
        }

        // --- Row 3: what it will do, and the two commits ---------------
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Text {
                Layout.fillWidth: true
                elide: Text.ElideRight
                textFormat: Text.PlainText
                font.pixelSize: 11
                color: Qt.rgba(editor.inkColor.r, editor.inkColor.g, editor.inkColor.b, 0.7)
                text: editor._has("chord")
                      ? editor._chordSummary()
                      : editor._has("text")
                        ? (editor.textValue ? "Types this text in one click"
                                            : "Enter the text this key types")
                        : "Sends " + editor.keyId.toUpperCase()
                          + " itself, to bind inside another app"
            }

            Rectangle {
                width: 66; height: 28; radius: 6
                color: resetMa.containsMouse ? editor.fieldColor : "transparent"
                border.color: editor.borderColor
                border.width: 1

                Text {
                    anchors.centerIn: parent
                    text: "Reset"
                    font.pixelSize: 11
                    color: editor.inkColor
                    textFormat: Text.PlainText
                }

                MouseArea {
                    id: resetMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: editor._reset()
                }
            }

            Rectangle {
                objectName: "keyActionSaveButton"
                width: 66; height: 28; radius: 6
                color: editor.accentColor
                border.color: editor.accentColor
                border.width: 1

                Text {
                    anchors.centerIn: parent
                    text: "Save"
                    font.pixelSize: 11
                    font.weight: Font.Bold
                    textFormat: Text.PlainText
                    color: editor.inkOnFn ? editor.inkOnFn(editor.accentColor) : "#fff"
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: editor._save()
                }
            }
        }
    }
}
