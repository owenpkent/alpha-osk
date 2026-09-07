import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Window 2.15

// The participant-facing surface for the research study (see
// src/study_bridge.py and docs/research/STUDY_PROTOCOL.md). A separate
// top-level Window, not a Popup, mirroring qml/components/SnippetsWindow.qml
// exactly: frameless, stays-on-top, never accepts OS focus, so it can float
// anywhere on the desktop and never steals focus from the target app.
//
// The one thing that makes this window different from every other floating
// window here: it deliberately never calls keyboard.setEditMode(). Every
// other editable surface (the snippets editor, the key-action editor, the
// prediction-edit popup) routes OSK keystrokes through the edit-mode
// intercept because their TextFields need real typed input and this window
// can never hold OS focus, so that is the only way characters can land in
// a QML control here. The study trial view has the opposite requirement:
// the participant has to type through the ORDINARY keystroke path, with the
// prediction engine running exactly as it does outside the study, because
// that is the thing being measured. StudyBridge.beginTrial() achieves this
// by swapping the bridge's synthesiser for a recorder
// (KeyboardBridge.begin_study_capture) -- predictions still run, the pill
// bar still shows (or doesn't, per the condition), but the keystrokes land
// in the recorder instead of the desktop. `study.transcript` mirrors what
// was captured. Given that, every piece of participant input in THIS window
// that is not a trial has to be answerable with a mouse click alone: there
// is no code path by which a typed character could ever reach a plain
// TextField here. That is why the enrolment number and the NASA-TLX scales
// below are +/- steppers rather than text fields or draggable sliders, and
// why the background questions are tap-to-choose rather than free text.
Window {
    id: studyWindow

    // Bound by Main.qml; this component cannot see `root` or `appSettings`.
    required property bool selfRoundedCorners
    required property var clampedWindowPos
    required property var inkOn
    required property var luminance
    required property color themeAccent
    required property color themeBackground
    required property color themeBorder
    required property color themeKeyColor
    required property color themeKeyPressed
    required property color themeTextColor
    required property real keyboardWidth
    required property real keyboardX
    required property real keyboardY
    required property var settings

    // Failures only -- this window's own views already show success (the
    // review screen stays open on "done" rather than closing), so there is
    // nothing here shaped like Snippets' `copied` / `saved`. See the toast
    // note below onProblem is wired in Main.qml.
    signal problem(string message)

    objectName: "studyWindow"
    title: "Alpha-OSK Research Study"
    width: 480
    minimumWidth: 420
    minimumHeight: 300
    height: Math.max(minimumHeight, studyContent.implicitHeight + 28)
    color: "transparent"
    flags: Qt.Window | Qt.FramelessWindowHint
           | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus

    // Which of the eight participant-facing views is showing, plus the
    // "stopConfirm" sub-state the trial view's Stop button drops into.
    // Exactly one is ever visible -- see the `visible:` binding on each
    // view's outer ColumnLayout below, all gated on this one property.
    property string currentView: "consent"

    // Local, session-lifetime flow state the bridge has no slot for: it
    // remembers consent and progress across a restart, but "has this
    // window already asked the background questions this run" is a
    // property of the UI's own flow, not of the study, so it lives here.
    property bool bgDone: false

    // Mirrors of the bridge's own step / summary maps, refreshed by
    // syncView() so the delegates below can bind to plain properties
    // rather than re-querying the bridge inside every Text binding.
    property var step: ({kind: "none"})
    property var review: ({})
    property string savedPath: ""
    property string startError: ""

    property bool _positioned: false

    readonly property color surface: themeKeyColor
    readonly property color surfaceHi: themeKeyPressed
    readonly property color txt: themeTextColor
    readonly property color muted: Qt.rgba(txt.r, txt.g, txt.b, 0.58)
    readonly property color faint: Qt.rgba(txt.r, txt.g, txt.b, 0.42)
    readonly property bool lightTheme: luminance(themeBackground) > 0.5
    // Same per-theme pick as SnippetsWindow's `danger`: a dark red is
    // illegible on Typewriter's cream, a bright one glares on Spaceship.
    readonly property color danger: lightTheme ? "#a3271c" : "#ef8b80"
    readonly property color accentSoft:
        Qt.rgba(themeAccent.r, themeAccent.g, themeAccent.b, 0.22)

    // ---- Consent-view state (mouse-only: see the file header) ----
    property int enrolNumber: -1  // -1 == "not given", passed through unchanged

    // ---- Background-view state ----
    property string bgPointer: ""
    property string bgExperience: ""
    property var bgNotes: []

    // ---- Workload-view state ----
    readonly property var workloadScales: [
        {key: "mental", label: qsTr("Mental demand")},
        {key: "physical", label: qsTr("Physical demand")},
        {key: "temporal", label: qsTr("Temporal demand")},
        {key: "performance", label: qsTr("Performance")},
        {key: "effort", label: qsTr("Effort")},
        {key: "frustration", label: qsTr("Frustration")}
    ]
    property var workloadValues: ({})

    function _resetWorkload() {
        var v = {}
        for (var i = 0; i < workloadScales.length; i++)
            v[workloadScales[i].key] = 10
        workloadValues = v
    }

    // Reassigning the whole map (rather than mutating the existing one in
    // place) is what makes the bar-fill and the number both re-evaluate --
    // QML property bindings only notice a *new* object, not a changed key
    // on the one they already hold.
    function _setWorkloadValue(key, value) {
        var v = {}
        for (var k in workloadValues) v[k] = workloadValues[k]
        v[key] = value
        workloadValues = v
    }

    // The one function every action below calls afterward. Reads consent,
    // the bridge's current step, and (once the session is over) the review
    // summary, and derives which view has to be showing. Driven imperatively
    // rather than through bound Properties on the bridge: `hasConsented()`
    // and `currentStep()` are plain Slots with no NOTIFY signal, so nothing
    // would re-evaluate a binding built on them. `stepChanged` /
    // `sessionComplete` cover every change that happens *inside* the bridge
    // (submitting a trial, finishing workload, a break ending); the actions
    // below that the bridge has no signal for (recording consent, saving the
    // background answers) call this directly.
    function syncView() {
        if (!study.hasConsented()) {
            currentView = "consent"
            return
        }
        var s = study.currentStep()
        step = s
        if (s.kind === "none") {
            currentView = bgDone ? "instructions" : "background"
        } else if (s.kind === "practice" || s.kind === "trial") {
            currentView = "trial"
            // Enter capture exactly once per step: guarded on not already
            // capturing, which is true both the first time a step is seen
            // and again after a Stop -> Resume, and false on every
            // stepChanged re-entry this function would otherwise re-fire on.
            if (!study.capturing)
                study.beginTrial()
        } else if (s.kind === "workload") {
            _resetWorkload()
            currentView = "workload"
        } else if (s.kind === "break") {
            currentView = "break"
        } else if (s.kind === "done") {
            review = study.reviewSummary()
            currentView = "review"
        } else {
            currentView = "consent"
        }
    }

    function openStudy() {
        if (!_positioned) {
            if (settings.savedStudyX > -1000000 && settings.savedStudyY > -1000000) {
                var pos = clampedWindowPos(settings.savedStudyX, settings.savedStudyY,
                                            studyWindow.width, studyWindow.height)
                studyWindow.x = pos.x
                studyWindow.y = pos.y
            } else {
                studyWindow.x = keyboardX + (keyboardWidth - studyWindow.width) / 2
                studyWindow.y = Math.max(0, keyboardY - studyWindow.height - 8)
            }
            _positioned = true
        }
        syncView()
        studyWindow.show()
        studyWindow.raise()
    }

    // The universal way out (see the file header on the header ✕ below).
    // Ends any in-flight capture cleanly rather than leaving the bridge
    // believing a trial is still being typed once the window is gone.
    function endForNow() {
        study.abandonTrial()
        studyWindow.hide()
    }

    onVisibleChanged: {
        if (!visible) study.abandonTrial()
    }

    Connections {
        target: study
        enabled: studyWindow.visible
        function onStepChanged() { studyWindow.syncView() }
        function onSessionComplete() { studyWindow.syncView() }
    }

    // ---- Consent ----
    function agreeToConsent() {
        study.recordConsent("", enrolNumber)
        syncView()
    }

    // ---- Background ----
    function submitBackground() {
        study.setBackground({
            "pointer_device": bgPointer,
            "osk_experience": bgExperience,
            "notes": bgNotes.join(", ")
        })
        bgDone = true
        syncView()
    }

    // ---- Instructions ----
    function beginSession() {
        startError = ""
        if (!study.startSession()) {
            startError = qsTr("Could not start the session. Ask the researcher to check your consent record and try again.")
            return
        }
        syncView()
    }

    // ---- Trial ----
    function submitPhrase() {
        if (!study.submitTrial()) {
            studyWindow.problem(qsTr("Could not record that trial"))
            // The step did not advance, so the same phrase is still current;
            // capture already ended inside submitTrial(), so resume it.
            study.beginTrial()
            return
        }
        // submitTrial() emits stepChanged on success, which drives syncView()
        // via the Connections above; nothing further to do here.
    }

    function requestStop() {
        study.abandonTrial()
        currentView = "stopConfirm"
    }

    function resumeFromStop() {
        syncView()
    }

    // ---- Workload ----
    function submitWorkload() {
        if (!study.recordWorkload(step.condition, workloadValues))
            studyWindow.problem(qsTr("Could not record your answers"))
    }

    // ---- Review ----
    property bool confirmingWithdraw: false

    function saveResults() {
        var path = study.suggestedBundlePath()
        if (study.saveBundle(path)) {
            savedPath = path
            currentView = "done"
        } else {
            studyWindow.problem(qsTr("Could not save your results"))
        }
    }

    function reallyWithdraw() {
        confirmingWithdraw = false
        study.withdraw()
        // withdraw() emits stepChanged; hasConsented() is now false, so
        // syncView() (driven by the Connections above) lands back on the
        // consent view -- there is nothing left to show a summary of.
    }

    // Window background (rounded card) -- see "Who rounds the window
    // corners" in CLAUDE.md for why the radius must be conditional rather
    // than a literal on a transparent, layered window.
    Rectangle {
        anchors.fill: parent
        color: themeBackground
        border.color: themeAccent
        border.width: 1
        radius: studyWindow.selfRoundedCorners ? 8 : 0
    }

    ColumnLayout {
        id: studyContent
        anchors.fill: parent
        anchors.margins: 14
        spacing: 10

        // ---- Header: drag handle + title + close ----
        RowLayout {
            Layout.fillWidth: true
            spacing: 6

            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 28

                Row {
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 6
                    Row {
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 3
                        Repeater {
                            model: 4
                            Rectangle {
                                width: 3; height: 3; radius: 1.5
                                color: studyWindow.faint
                            }
                        }
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("Research Study")
                        color: studyWindow.txt
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                    }
                }

                // Manual x/y drag, never startSystemMove() -- same reason
                // as every other floating window here: WindowDoesNotAcceptFocus
                // makes a WM-driven system move unreliable.
                MouseArea {
                    id: studyDragArea
                    anchors.fill: parent
                    cursorShape: Qt.SizeAllCursor
                    property real startMx
                    property real startMy
                    property real startX
                    property real startY
                    onPressed: function(mouse) {
                        var g = mapToGlobal(mouse.x, mouse.y)
                        startMx = g.x; startMy = g.y
                        startX = studyWindow.x; startY = studyWindow.y
                    }
                    onPositionChanged: function(mouse) {
                        if (!pressed) return
                        var g = mapToGlobal(mouse.x, mouse.y)
                        studyWindow.x = startX + (g.x - startMx)
                        studyWindow.y = startY + (g.y - startMy)
                    }
                    onReleased: {
                        settings.savedStudyX = Math.round(studyWindow.x)
                        settings.savedStudyY = Math.round(studyWindow.y)
                    }
                }
            }

            // Reachable from every view: closing the window is always
            // safe, since a trial in flight is cleanly abandoned first
            // (see endForNow() / onVisibleChanged above) and everything
            // else the participant has answered so far is already
            // persisted by the bridge.
            Rectangle {
                Layout.preferredWidth: 32
                Layout.preferredHeight: 32
                radius: 4
                color: studyCloseMa.containsMouse
                       ? Qt.rgba(studyWindow.danger.r, studyWindow.danger.g,
                                 studyWindow.danger.b, 0.22)
                       : "transparent"
                StrokeIcon {
                    anchors.centerIn: parent
                    width: 14; height: 14
                    strokeWidth: 2.4
                    ink: studyCloseMa.containsMouse
                         ? studyWindow.danger : studyWindow.muted
                    paths: ["M5 5 L19 19", "M19 5 L5 19"]
                }
                MouseArea {
                    id: studyCloseMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: studyWindow.endForNow()
                }
            }
        }

        // ================= Consent =================
        ColumnLayout {
            objectName: "studyConsentView"
            Layout.fillWidth: true
            spacing: 8
            visible: studyWindow.currentView === "consent"

            Text {
                Layout.fillWidth: true
                text: qsTr("Before anything is recorded")
                color: studyWindow.txt
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }

            Text {
                Layout.fillWidth: true
                text: qsTr("There has been NO independent ethics review of this study. It is run by the developer of Alpha-OSK. Taking part is entirely optional, nothing is recorded until you press “I agree” below, and you can withdraw and delete everything at any point, including after finishing.")
                color: studyWindow.danger
                font.pixelSize: 11
                wrapMode: Text.WordWrap
            }

            Flickable {
                Layout.fillWidth: true
                Layout.preferredHeight: 170
                clip: true
                contentWidth: width
                contentHeight: consentSummaryText.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar {}

                Text {
                    id: consentSummaryText
                    width: parent.width
                    textFormat: Text.PlainText
                    wrapMode: Text.WordWrap
                    color: studyWindow.muted
                    font.pixelSize: 11
                    text: qsTr("You would copy about 25 minutes of short phrases, once with word suggestions on and once off, at home and in your own time. There is a practice phrase each time that does not count, and six short questions after each round. You can take a break whenever you like. Only the phrases you type, timing and keystrokes are recorded, never your name, and never anything you type outside the study. Nothing leaves this computer until you press “Save my results” at the very end, and you decide then whether to keep or delete it.")
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 40
                radius: 6
                color: consentLinkMa.containsMouse ? studyWindow.surfaceHi : studyWindow.surface
                border.width: 1
                border.color: studyWindow.themeBorder
                Text {
                    anchors.centerIn: parent
                    text: qsTr("Read the full consent form")
                    color: studyWindow.themeAccent
                    font.pixelSize: 12
                }
                MouseArea {
                    id: consentLinkMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: Qt.openUrlExternally(
                        "https://github.com/owenpkent/alpha-osk/blob/main/docs/research/STUDY_CONSENT.md")
                }
            }

            // Enrolment number: mouse-only stepper, never a text field (see
            // the file header). -1 reads as "not given" and is passed
            // through unchanged; the bridge draws a random one in that case.
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Text {
                    Layout.fillWidth: true
                    text: qsTr("Enrolment number (optional, the researcher gives you this)")
                    color: studyWindow.muted
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                }
                Rectangle {
                    Layout.preferredWidth: 44; Layout.preferredHeight: 44
                    radius: 6
                    color: enrolMinusMa.containsMouse ? studyWindow.surfaceHi : studyWindow.surface
                    border.width: 1; border.color: studyWindow.themeBorder
                    Text { anchors.centerIn: parent; text: "−"; font.pixelSize: 18; color: studyWindow.txt }
                    MouseArea {
                        id: enrolMinusMa
                        anchors.fill: parent; hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: studyWindow.enrolNumber = Math.max(-1, studyWindow.enrolNumber - 1)
                    }
                }
                Text {
                    Layout.preferredWidth: 60
                    horizontalAlignment: Text.AlignHCenter
                    text: studyWindow.enrolNumber < 0 ? qsTr("none") : String(studyWindow.enrolNumber)
                    color: studyWindow.txt
                    font.pixelSize: 13
                }
                Rectangle {
                    Layout.preferredWidth: 44; Layout.preferredHeight: 44
                    radius: 6
                    color: enrolPlusMa.containsMouse ? studyWindow.surfaceHi : studyWindow.surface
                    border.width: 1; border.color: studyWindow.themeBorder
                    Text { anchors.centerIn: parent; text: "+"; font.pixelSize: 18; color: studyWindow.txt }
                    MouseArea {
                        id: enrolPlusMa
                        anchors.fill: parent; hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: studyWindow.enrolNumber = Math.max(0, studyWindow.enrolNumber) + 1
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 46
                    radius: 6
                    color: notNowMa.containsMouse ? studyWindow.surfaceHi : studyWindow.surface
                    border.width: 1; border.color: studyWindow.themeBorder
                    Text { anchors.centerIn: parent; text: qsTr("Not now"); color: studyWindow.txt; font.pixelSize: 13 }
                    MouseArea {
                        id: notNowMa
                        anchors.fill: parent; hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: studyWindow.hide()
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 46
                    radius: 6
                    color: agreeMa.containsMouse ? Qt.lighter(studyWindow.themeAccent, 1.15) : studyWindow.themeAccent
                    border.width: 1; border.color: studyWindow.themeAccent
                    Text {
                        anchors.centerIn: parent
                        text: qsTr("I agree, begin")
                        color: studyWindow.inkOn(studyWindow.themeAccent)
                        font.pixelSize: 13; font.weight: Font.Bold
                    }
                    MouseArea {
                        id: agreeMa
                        anchors.fill: parent; hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: studyWindow.agreeToConsent()
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                text: qsTr("Consent form version %1").arg(study.consentVersion())
                color: studyWindow.faint
                font.pixelSize: 9
            }
        }

        // ================= Background =================
        ColumnLayout {
            objectName: "studyBackgroundView"
            Layout.fillWidth: true
            spacing: 10
            visible: studyWindow.currentView === "background"

            Text {
                Layout.fillWidth: true
                text: qsTr("A few quick questions")
                color: studyWindow.txt
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }

            Text {
                Layout.fillWidth: true
                text: qsTr("What do you point with, most of the time?")
                color: studyWindow.muted
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
            Flow {
                Layout.fillWidth: true
                spacing: 6
                Repeater {
                    model: [qsTr("Mouse"), qsTr("Trackball"), qsTr("Touchpad"),
                            qsTr("Head or eye tracker"), qsTr("Switch / single button"),
                            qsTr("Other")]
                    delegate: Rectangle {
                        readonly property bool chosen: studyWindow.bgPointer === modelData
                        height: 40
                        width: chipLabel.implicitWidth + 24
                        radius: 20
                        color: chosen ? studyWindow.accentSoft : studyWindow.surface
                        border.width: chosen ? 2 : 1
                        border.color: chosen ? studyWindow.themeAccent : studyWindow.themeBorder
                        Text {
                            id: chipLabel
                            anchors.centerIn: parent
                            text: modelData
                            color: studyWindow.txt
                            font.pixelSize: 12
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: studyWindow.bgPointer = modelData
                        }
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                Layout.topMargin: 4
                text: qsTr("How long have you used an on-screen keyboard like this one?")
                color: studyWindow.muted
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
            Flow {
                Layout.fillWidth: true
                spacing: 6
                Repeater {
                    model: [qsTr("Never before today"), qsTr("A few times"),
                            qsTr("Regularly, less than a year"), qsTr("A year or more")]
                    delegate: Rectangle {
                        readonly property bool chosen: studyWindow.bgExperience === modelData
                        height: 40
                        width: expLabel.implicitWidth + 24
                        radius: 20
                        color: chosen ? studyWindow.accentSoft : studyWindow.surface
                        border.width: chosen ? 2 : 1
                        border.color: chosen ? studyWindow.themeAccent : studyWindow.themeBorder
                        Text {
                            id: expLabel
                            anchors.centerIn: parent
                            text: modelData
                            color: studyWindow.txt
                            font.pixelSize: 12
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: studyWindow.bgExperience = modelData
                        }
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                Layout.topMargin: 4
                text: qsTr("Anything else worth knowing? (optional, tap any that apply)")
                color: studyWindow.muted
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
            Flow {
                Layout.fillWidth: true
                spacing: 6
                Repeater {
                    model: [qsTr("My aim varies through the day"), qsTr("I tire quickly"),
                            qsTr("This is my main way of typing")]
                    delegate: Rectangle {
                        readonly property bool chosen: studyWindow.bgNotes.indexOf(modelData) >= 0
                        height: 40
                        width: noteLabel.implicitWidth + 24
                        radius: 20
                        color: chosen ? studyWindow.accentSoft : studyWindow.surface
                        border.width: chosen ? 2 : 1
                        border.color: chosen ? studyWindow.themeAccent : studyWindow.themeBorder
                        Text {
                            id: noteLabel
                            anchors.centerIn: parent
                            text: modelData
                            color: studyWindow.txt
                            font.pixelSize: 12
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                var list = studyWindow.bgNotes.slice()
                                var at = list.indexOf(modelData)
                                if (at >= 0) list.splice(at, 1)
                                else list.push(modelData)
                                studyWindow.bgNotes = list
                            }
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 46
                Layout.topMargin: 6
                radius: 6
                readonly property bool ready: studyWindow.bgPointer !== "" && studyWindow.bgExperience !== ""
                enabled: ready
                opacity: ready ? 1.0 : 0.45
                color: bgContinueMa.containsMouse ? Qt.lighter(studyWindow.themeAccent, 1.15) : studyWindow.themeAccent
                border.width: 1; border.color: studyWindow.themeAccent
                Text {
                    anchors.centerIn: parent
                    text: qsTr("Continue")
                    color: studyWindow.inkOn(studyWindow.themeAccent)
                    font.pixelSize: 13; font.weight: Font.Bold
                }
                MouseArea {
                    id: bgContinueMa
                    anchors.fill: parent; hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: studyWindow.submitBackground()
                }
            }
        }

        // ================= Instructions =================
        ColumnLayout {
            objectName: "studyInstructionsView"
            Layout.fillWidth: true
            spacing: 10
            visible: studyWindow.currentView === "instructions"

            Text {
                Layout.fillWidth: true
                text: qsTr("What happens next")
                color: studyWindow.txt
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: studyWindow.muted
                font.pixelSize: 12
                text: qsTr("You will type a handful of short phrases with word suggestions ON, then the same number with them OFF (or the other order, it is decided for you). Each round starts with one practice phrase that does not count, then the phrases that do, then six short questions about how it felt. You can stop at any point and pick up again later, and there is no timer on the break in between.")
            }
            Text {
                visible: studyWindow.startError !== ""
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: studyWindow.startError
                color: studyWindow.danger
                font.pixelSize: 11
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 48
                radius: 6
                color: startMa.containsMouse ? Qt.lighter(studyWindow.themeAccent, 1.15) : studyWindow.themeAccent
                border.width: 1; border.color: studyWindow.themeAccent
                Text {
                    anchors.centerIn: parent
                    text: qsTr("Start")
                    color: studyWindow.inkOn(studyWindow.themeAccent)
                    font.pixelSize: 14; font.weight: Font.Bold
                }
                MouseArea {
                    id: startMa
                    anchors.fill: parent; hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: studyWindow.beginSession()
                }
            }
        }

        // ================= Trial (practice or scored) =================
        ColumnLayout {
            objectName: "studyTrialView"
            Layout.fillWidth: true
            spacing: 8
            visible: studyWindow.currentView === "trial"

            Text {
                visible: studyWindow.step.kind === "practice"
                Layout.fillWidth: true
                text: qsTr("Practice: this one does not count")
                color: studyWindow.themeAccent
                font.pixelSize: 11
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: qsTr("Block %1 of %2: %3")
                      .arg((studyWindow.step.blockIndex || 0) + 1)
                      .arg(studyWindow.step.blockCount || 1)
                      .arg(studyWindow.step.conditionLabel || "")
                color: studyWindow.faint
                font.pixelSize: 10
            }
            Text {
                visible: studyWindow.step.kind === "trial"
                Layout.fillWidth: true
                text: qsTr("Phrase %1 of %2")
                      .arg((studyWindow.step.scoredDone || 0) + 1)
                      .arg(studyWindow.step.scoredTotal || 0)
                color: studyWindow.faint
                font.pixelSize: 10
            }

            // The presented phrase, large and readable at the window's
            // minimum size -- this is the one thing the participant has to
            // be able to read without straining, at any window size.
            Text {
                objectName: "studyPhraseText"
                Layout.fillWidth: true
                text: studyWindow.step.phrase || ""
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                color: studyWindow.txt
                font.pixelSize: 20
                font.weight: Font.DemiBold
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 64
                radius: 6
                color: studyWindow.surface
                border.width: 1
                border.color: studyWindow.themeBorder
                Text {
                    objectName: "studyTranscriptText"
                    anchors.fill: parent
                    anchors.margins: 10
                    verticalAlignment: Text.AlignTop
                    wrapMode: Text.WordWrap
                    textFormat: Text.PlainText
                    color: studyWindow.txt
                    font.pixelSize: 14
                    text: study.transcript
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Rectangle {
                    Layout.preferredWidth: 100
                    Layout.preferredHeight: 48
                    radius: 6
                    color: stopMa.containsMouse
                           ? Qt.rgba(studyWindow.danger.r, studyWindow.danger.g, studyWindow.danger.b, 0.22)
                           : studyWindow.surface
                    border.width: 1; border.color: studyWindow.themeBorder
                    Text { anchors.centerIn: parent; text: qsTr("Stop"); color: studyWindow.danger; font.pixelSize: 13 }
                    MouseArea {
                        id: stopMa
                        anchors.fill: parent; hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: studyWindow.requestStop()
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 48
                    radius: 6
                    color: doneMa.containsMouse ? Qt.lighter(studyWindow.themeAccent, 1.15) : studyWindow.themeAccent
                    border.width: 1; border.color: studyWindow.themeAccent
                    Text {
                        anchors.centerIn: parent
                        text: qsTr("Done")
                        color: studyWindow.inkOn(studyWindow.themeAccent)
                        font.pixelSize: 15; font.weight: Font.Bold
                    }
                    MouseArea {
                        id: doneMa
                        anchors.fill: parent; hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: studyWindow.submitPhrase()
                    }
                }
            }
        }

        // ================= Stop confirmation =================
        ColumnLayout {
            objectName: "studyStopConfirmView"
            Layout.fillWidth: true
            spacing: 10
            visible: studyWindow.currentView === "stopConfirm"

            Text {
                Layout.fillWidth: true
                text: qsTr("Paused")
                color: studyWindow.txt
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: qsTr("Nothing from this phrase was kept. Resume whenever you are ready, or come back another time. Your place is saved.")
                color: studyWindow.muted
                font.pixelSize: 12
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 46
                    radius: 6
                    color: exitNowMa.containsMouse ? studyWindow.surfaceHi : studyWindow.surface
                    border.width: 1; border.color: studyWindow.themeBorder
                    Text { anchors.centerIn: parent; text: qsTr("Exit for now"); color: studyWindow.txt; font.pixelSize: 13 }
                    MouseArea {
                        id: exitNowMa
                        anchors.fill: parent; hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: studyWindow.hide()
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 46
                    radius: 6
                    color: resumeMa.containsMouse ? Qt.lighter(studyWindow.themeAccent, 1.15) : studyWindow.themeAccent
                    border.width: 1; border.color: studyWindow.themeAccent
                    Text {
                        anchors.centerIn: parent
                        text: qsTr("Resume")
                        color: studyWindow.inkOn(studyWindow.themeAccent)
                        font.pixelSize: 13; font.weight: Font.Bold
                    }
                    MouseArea {
                        id: resumeMa
                        anchors.fill: parent; hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: studyWindow.resumeFromStop()
                    }
                }
            }
        }

        // ================= Workload (NASA-TLX, raw/unweighted) =================
        ColumnLayout {
            objectName: "studyWorkloadView"
            Layout.fillWidth: true
            spacing: 6
            visible: studyWindow.currentView === "workload"

            Text {
                Layout.fillWidth: true
                text: qsTr("How did that feel?")
                color: studyWindow.txt
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: qsTr("Six quick ratings, 0 (very low) to 20 (very high).")
                color: studyWindow.muted
                font.pixelSize: 11
            }

            Repeater {
                model: studyWindow.workloadScales
                delegate: RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Text {
                        Layout.preferredWidth: 110
                        text: modelData.label
                        color: studyWindow.txt
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                    }
                    Rectangle {
                        Layout.preferredWidth: 38; Layout.preferredHeight: 38
                        radius: 6
                        color: wlMinusMa.containsMouse ? studyWindow.surfaceHi : studyWindow.surface
                        border.width: 1; border.color: studyWindow.themeBorder
                        Text { anchors.centerIn: parent; text: "−"; font.pixelSize: 16; color: studyWindow.txt }
                        MouseArea {
                            id: wlMinusMa
                            anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: studyWindow._setWorkloadValue(
                                modelData.key,
                                Math.max(0, studyWindow.workloadValues[modelData.key] - 1))
                        }
                    }
                    // The filled bar gives the "slider" reading at a glance
                    // while staying entirely click-driven -- see the file
                    // header on why an actual draggable Slider is not used.
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 16
                        radius: 8
                        color: studyWindow.surface
                        border.width: 1; border.color: studyWindow.themeBorder
                        Rectangle {
                            anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter
                            height: parent.height - 4
                            radius: 6
                            color: studyWindow.themeAccent
                            width: Math.max(6, parent.width * (studyWindow.workloadValues[modelData.key] || 0) / 20)
                        }
                    }
                    Text {
                        Layout.preferredWidth: 24
                        horizontalAlignment: Text.AlignHCenter
                        text: String(studyWindow.workloadValues[modelData.key] !== undefined
                                     ? studyWindow.workloadValues[modelData.key] : 10)
                        color: studyWindow.txt
                        font.pixelSize: 12
                    }
                    Rectangle {
                        Layout.preferredWidth: 38; Layout.preferredHeight: 38
                        radius: 6
                        color: wlPlusMa.containsMouse ? studyWindow.surfaceHi : studyWindow.surface
                        border.width: 1; border.color: studyWindow.themeBorder
                        Text { anchors.centerIn: parent; text: "+"; font.pixelSize: 16; color: studyWindow.txt }
                        MouseArea {
                            id: wlPlusMa
                            anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: studyWindow._setWorkloadValue(
                                modelData.key,
                                Math.min(20, studyWindow.workloadValues[modelData.key] + 1))
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 46
                Layout.topMargin: 4
                radius: 6
                color: wlContinueMa.containsMouse ? Qt.lighter(studyWindow.themeAccent, 1.15) : studyWindow.themeAccent
                border.width: 1; border.color: studyWindow.themeAccent
                Text {
                    anchors.centerIn: parent
                    text: qsTr("Continue")
                    color: studyWindow.inkOn(studyWindow.themeAccent)
                    font.pixelSize: 13; font.weight: Font.Bold
                }
                MouseArea {
                    id: wlContinueMa
                    anchors.fill: parent; hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: studyWindow.submitWorkload()
                }
            }
        }

        // ================= Break =================
        ColumnLayout {
            objectName: "studyBreakView"
            Layout.fillWidth: true
            spacing: 10
            visible: studyWindow.currentView === "break"

            Text {
                Layout.fillWidth: true
                text: qsTr("Take a break")
                color: studyWindow.txt
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: qsTr("Take as long as you like. There is no timer, and nothing happens until you tap Continue.")
                color: studyWindow.muted
                font.pixelSize: 12
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 46
                radius: 6
                color: breakContinueMa.containsMouse ? Qt.lighter(studyWindow.themeAccent, 1.15) : studyWindow.themeAccent
                border.width: 1; border.color: studyWindow.themeAccent
                Text {
                    anchors.centerIn: parent
                    text: qsTr("Continue")
                    color: studyWindow.inkOn(studyWindow.themeAccent)
                    font.pixelSize: 13; font.weight: Font.Bold
                }
                MouseArea {
                    id: breakContinueMa
                    anchors.fill: parent; hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: study.skipStep()
                }
            }
        }

        // ================= Review =================
        ColumnLayout {
            objectName: "studyReviewView"
            Layout.fillWidth: true
            spacing: 8
            visible: studyWindow.currentView === "review"

            Text {
                Layout.fillWidth: true
                text: qsTr("Your results")
                color: studyWindow.txt
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: qsTr("Nothing has been sent anywhere yet. Saving writes a file on this computer that you can open and read yourself before deciding what to do with it.")
                color: studyWindow.muted
                font.pixelSize: 11
            }

            Text {
                Layout.fillWidth: true
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                color: studyWindow.txt
                font.pixelSize: 13
                text: qsTr("Your keystroke savings: %1%")
                      .arg((studyWindow.review.realised_savings || 0).toFixed(1))
            }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: studyWindow.muted
                font.pixelSize: 12
                text: qsTr("Offline benchmark: %1%   •   shortfall: %2 points")
                      .arg((studyWindow.review.benchmark_savings || 0).toFixed(1))
                      .arg((studyWindow.review.savings_shortfall || 0).toFixed(1))
            }
            Text {
                visible: studyWindow.review.entry_rate_difference_wpm !== undefined
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: studyWindow.muted
                font.pixelSize: 12
                text: qsTr("Typing speed with suggestions on was %1 wpm %2 than off")
                      .arg(Math.abs(studyWindow.review.entry_rate_difference_wpm || 0).toFixed(1))
                      .arg((studyWindow.review.entry_rate_difference_wpm || 0) >= 0
                           ? qsTr("faster") : qsTr("slower"))
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 6
                spacing: 8
                visible: !studyWindow.confirmingWithdraw

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 46
                    radius: 6
                    color: withdrawMa.containsMouse
                           ? Qt.rgba(studyWindow.danger.r, studyWindow.danger.g, studyWindow.danger.b, 0.22)
                           : studyWindow.surface
                    border.width: 1; border.color: studyWindow.themeBorder
                    Text { anchors.centerIn: parent; text: qsTr("Delete everything instead"); color: studyWindow.danger; font.pixelSize: 12 }
                    MouseArea {
                        id: withdrawMa
                        anchors.fill: parent; hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: studyWindow.confirmingWithdraw = true
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 46
                    radius: 6
                    color: saveMa.containsMouse ? Qt.lighter(studyWindow.themeAccent, 1.15) : studyWindow.themeAccent
                    border.width: 1; border.color: studyWindow.themeAccent
                    Text {
                        anchors.centerIn: parent
                        text: qsTr("Save my results")
                        color: studyWindow.inkOn(studyWindow.themeAccent)
                        font.pixelSize: 13; font.weight: Font.Bold
                    }
                    MouseArea {
                        id: saveMa
                        anchors.fill: parent; hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: studyWindow.saveResults()
                    }
                }
            }

            // Delete always confirms, same rule as Snippets: there is no
            // undo, and this population uses an imprecise pointer.
            ColumnLayout {
                Layout.fillWidth: true
                Layout.topMargin: 6
                spacing: 8
                visible: studyWindow.confirmingWithdraw
                Text {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: qsTr("Delete everything from this study? This cannot be undone.")
                    color: studyWindow.danger
                    font.pixelSize: 12
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 46
                        radius: 6
                        color: keepMa.containsMouse ? studyWindow.surfaceHi : studyWindow.surface
                        border.width: 1; border.color: studyWindow.themeBorder
                        Text { anchors.centerIn: parent; text: qsTr("Keep"); color: studyWindow.txt; font.pixelSize: 13 }
                        MouseArea {
                            id: keepMa
                            anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: studyWindow.confirmingWithdraw = false
                        }
                    }
                    Rectangle {
                        Layout.preferredWidth: 130
                        Layout.preferredHeight: 46
                        radius: 6
                        color: reallyWithdrawMa.containsMouse ? Qt.lighter(studyWindow.danger, 1.12) : studyWindow.danger
                        border.width: 1; border.color: studyWindow.danger
                        Text {
                            anchors.centerIn: parent
                            text: qsTr("Delete")
                            color: studyWindow.inkOn(studyWindow.danger)
                            font.pixelSize: 13; font.weight: Font.Bold
                        }
                        MouseArea {
                            id: reallyWithdrawMa
                            anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: studyWindow.reallyWithdraw()
                        }
                    }
                }
            }
        }

        // ================= Done =================
        ColumnLayout {
            objectName: "studyDoneView"
            Layout.fillWidth: true
            spacing: 8
            visible: studyWindow.currentView === "done"

            Text {
                Layout.fillWidth: true
                text: qsTr("Thank you")
                color: studyWindow.txt
                font.pixelSize: 16
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: qsTr("Your results were saved to:")
                color: studyWindow.muted
                font.pixelSize: 12
            }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WrapAnywhere
                textFormat: Text.PlainText
                text: studyWindow.savedPath
                color: studyWindow.txt
                font.pixelSize: 11
            }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: qsTr("Send that file to the researcher whenever suits you. You can close this window now.")
                color: studyWindow.muted
                font.pixelSize: 11
            }
        }
    }
}
