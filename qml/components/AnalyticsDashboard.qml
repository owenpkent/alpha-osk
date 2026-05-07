import QtQuick 2.15
import QtQuick.Layouts 1.15
import QtQuick.Controls 2.15

Item {
    id: dashboard

    property var stats: ({})

    // Lifetime is the default view — typing patterns, savings, and
    // quality only become meaningful over many sessions.  Session view
    // is still useful for "how am I doing right now."
    property bool showLifetime: true

    // Size to content rather than filling the parent.  When the
    // parent gave us a fixed implicitHeight (e.g. 460 px) the
    // ColumnLayout's `anchors.fill: parent` stretched to fit and the
    // inner items packed at the top, leaving a tall empty band below
    // the tile grid (especially in This Session view, where the
    // sparkline / top words sections collapse to zero height).
    implicitHeight: contentColumn.implicitHeight

    // Poll analytics every 2 seconds while visible
    Timer {
        running: dashboard.visible
        interval: 2000
        repeat: true
        triggeredOnStart: true
        onTriggered: {
            if (keyboard) dashboard.stats = keyboard.getAnalytics()
        }
    }

    ColumnLayout {
        id: contentColumn
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: 10

        // ===== Session / Lifetime toggle =====
        // (was preceded by a separate hero card, all-time pill row,
        // and a divider; collapsed into the single tile grid below
        // because the visual layering read as 4 disconnected sections.)
        RowLayout {
            Layout.fillWidth: true
            spacing: 4

            ScopeTab {
                label: "Lifetime"
                selected: dashboard.showLifetime
                onClicked: dashboard.showLifetime = true
            }
            ScopeTab {
                label: "This Session"
                selected: !dashboard.showLifetime
                onClicked: dashboard.showLifetime = false
            }
        }

        // Stats grid — bound to the active scope
        GridLayout {
            Layout.fillWidth: true
            columns: 2
            rowSpacing: 6
            columnSpacing: 6

            // Keystrokes Saved: the headline savings number, promoted
            // from the previous hero card into a tile when the
            // dashboard collapsed to a single section.  Replaces the
            // prior "Typing Effort" (total keystrokes typed) tile
            // because the absolute-savings number tells the dashboard
            // story more directly than the absolute-effort one.
            StatBox {
                label: "Keystrokes Saved"
                value: formatNumber(dashboard.showLifetime
                                    ? (dashboard.stats.alltimeKeystrokesSaved || 0)
                                    : (dashboard.stats.keystrokesSaved || 0))
                unit: "keys you didn't have to press"
                accent: "#66dd88"
            }

            // Time Saved: keystrokes_saved x user's own pace, formatted.
            StatBox {
                label: "Time Saved"
                value: formatDuration(dashboard.showLifetime
                                      ? (dashboard.stats.alltimeTimeSavedSeconds || 0)
                                      : (dashboard.stats.timeSavedSeconds || 0))
                unit: "avoided by predictions"
                accent: "#4a9eff"
            }

            // Effort Saved: % of total keystrokes that came from
            // predictions instead of being typed.
            StatBox {
                label: "Effort Saved"
                value: (dashboard.showLifetime
                        ? (dashboard.stats.alltimeSavingsPercent || 0)
                        : (dashboard.stats.savingsPercent || 0)
                       ).toFixed(0) + "%"
                unit: "of total keystrokes"
                accent: "#bb88ff"
            }

            // Acceptance Rate: of suggestions OFFERED, what % did the
            // user click.  Distinct from Effort Saved (a keystroke
            // share): asks "when the keyboard suggested something,
            // how often was it useful enough to take".
            StatBox {
                label: "Acceptance"
                value: (dashboard.showLifetime
                        ? (dashboard.stats.alltimeAcceptanceRate || 0)
                        : (dashboard.stats.acceptanceRate || 0)
                       ).toFixed(0) + "%"
                unit: "of offered suggestions accepted"
                accent: "#ffaa66"
            }
        }

        // WPM sparkline — session only.  Lifetime aggregate has no
        // per-minute history; the hourly WPM is already in the Speed
        // tile above.
        Item {
            Layout.fillWidth: true
            implicitHeight: 40
            visible: !dashboard.showLifetime && (dashboard.stats.wpmSamples || []).length > 1

            Canvas {
                id: sparkCanvas
                anchors.fill: parent

                property var samples: dashboard.stats.wpmSamples || []

                onSamplesChanged: requestPaint()

                onPaint: {
                    var ctx = getContext("2d")
                    ctx.clearRect(0, 0, width, height)
                    if (samples.length < 2) return

                    var maxVal = Math.max.apply(null, samples) || 1
                    var minVal = Math.min.apply(null, samples)
                    var range = Math.max(1, maxVal - minVal)
                    var stepX = width / (samples.length - 1)
                    var pad = 4

                    // Draw line
                    ctx.beginPath()
                    ctx.strokeStyle = "#4a9eff"
                    ctx.lineWidth = 2
                    ctx.lineJoin = "round"
                    for (var i = 0; i < samples.length; i++) {
                        var x = i * stepX
                        var y = pad + (1 - (samples[i] - minVal) / range) * (height - 2 * pad)
                        if (i === 0) ctx.moveTo(x, y)
                        else ctx.lineTo(x, y)
                    }
                    ctx.stroke()

                    // Fill under
                    ctx.lineTo((samples.length - 1) * stepX, height)
                    ctx.lineTo(0, height)
                    ctx.closePath()
                    ctx.fillStyle = Qt.rgba(0.29, 0.61, 1.0, 0.1)
                    ctx.fill()
                }
            }

            Text {
                anchors.left: parent.left
                anchors.bottom: parent.bottom
                text: "wpm over time"
                font.pixelSize: 9
                color: "#555"
            }
        }

        // Top words — bound to the active scope
        ColumnLayout {
            id: topWordsCol
            Layout.fillWidth: true
            spacing: 3

            // The active list of {word,count}.  Bound twice (here + in
            // the Repeater's model) rather than referencing through
            // `parent` so the binding survives any future restructuring.
            property var activeTopWords: dashboard.showLifetime
                                         ? (dashboard.stats.alltimeTopWords || [])
                                         : (dashboard.stats.topWords || [])
            visible: activeTopWords.length > 0

            Text {
                text: "Top Words"
                font.pixelSize: 11
                font.weight: Font.DemiBold
                color: "#888"
            }

            Repeater {
                model: topWordsCol.activeTopWords

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6

                    // Bar scales relative to the top word's count so the
                    // ranking actually reads as a ranking.  The earlier
                    // formula (count * 3 capped at 60 px) saturated the
                    // moment a word was used 20+ times, which for "and",
                    // "the", "to" is immediately, so every bar drew the
                    // same width.  Now the #1 word always fills the
                    // 80 px allotment and the rest scale proportionally.
                    Rectangle {
                        Layout.preferredWidth: 80
                        height: 12
                        color: "transparent"

                        Rectangle {
                            height: parent.height
                            width: {
                                var top = topWordsCol.activeTopWords
                                if (!top || top.length === 0) return 0
                                var maxC = top[0].count
                                if (maxC <= 0) return 4
                                return Math.max(4, parent.width * modelData.count / maxC)
                            }
                            radius: 2
                            color: "#4a9eff"
                            opacity: 1.0 - (index * 0.15)

                            Behavior on width { NumberAnimation { duration: 250 } }
                        }
                    }

                    Text {
                        text: modelData.word
                        font.pixelSize: 11
                        color: "#ccc"
                        Layout.fillWidth: true
                    }

                    Text {
                        text: String(modelData.count)
                        font.pixelSize: 11
                        color: "#666"
                    }
                }
            }
        }
    }

    function formatNumber(n) {
        if (n >= 10000) return (n / 1000).toFixed(1) + "k"
        if (n >= 1000) return (n / 1000).toFixed(1) + "k"
        return String(n)
    }

    // Render a seconds count as the most readable unit for its size.
    // < 1 min → "Xs" (only useful in fresh sessions before the user
    // has saved a meaningful amount), then minutes, then hours with
    // one decimal once we cross an hour.
    function formatDuration(seconds) {
        if (!seconds || seconds < 1) return "0s"
        if (seconds < 60) return Math.round(seconds) + "s"
        var minutes = seconds / 60
        if (minutes < 60) return Math.round(minutes) + " min"
        var hours = minutes / 60
        return hours.toFixed(1) + " hrs"
    }

    // Toggle pill for "Lifetime" vs "This Session"
    component ScopeTab: Rectangle {
        property string label: ""
        property bool selected: false
        signal clicked()

        Layout.fillWidth: true
        implicitHeight: 26
        radius: 5
        color: selected ? "#3a3a3a" : (tabHover.containsMouse ? "#2a2a2a" : "transparent")
        border.color: selected ? "#555" : "transparent"
        border.width: 1

        Text {
            anchors.centerIn: parent
            text: parent.label
            font.pixelSize: 11
            font.weight: parent.selected ? Font.DemiBold : Font.Normal
            color: parent.selected ? "#e0e0e0" : "#888"
        }

        MouseArea {
            id: tabHover
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: parent.clicked()
        }
    }


    // Session stat box.  The Rectangle's height grows with content
    // (label + value + optional unit + margins) so subtext never
    // renders past the rounded background -- the prior fixed
    // implicitHeight of 50 px was ~10 px shorter than the three text
    // elements need, which is why "1.8k words typed" appeared to
    // float outside the colored box.
    component StatBox: ColumnLayout {
        property string label: ""
        property string value: ""
        property string unit: ""
        property string accent: "#e0e0e0"

        Layout.fillWidth: true
        spacing: 0

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: contentCol.implicitHeight + 14
            radius: 6
            color: "#2a2a2a"

            ColumnLayout {
                id: contentCol
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 7
                anchors.rightMargin: 7
                spacing: 1

                Text {
                    text: parent.parent.parent.label
                    font.pixelSize: 10
                    color: "#888"
                }
                Text {
                    text: parent.parent.parent.value
                    font.pixelSize: 18
                    font.weight: Font.Bold
                    color: parent.parent.parent.accent
                }
                Text {
                    text: parent.parent.parent.unit
                    font.pixelSize: 10
                    color: "#aaa"
                    visible: parent.parent.parent.unit !== ""
                }
            }
        }
    }
}
