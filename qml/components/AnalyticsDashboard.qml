import QtQuick 2.15
import QtQuick.Layouts 1.15
import QtQuick.Controls 2.15

Item {
    id: dashboard

    property var stats: ({})

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
        anchors.fill: parent
        spacing: 10

        // ===== ALL-TIME HERO: Keystrokes Saved =====
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: heroCol.implicitHeight + 20
            radius: 8
            color: "#1a2a1a"
            border.color: "#2a4a2a"

            ColumnLayout {
                id: heroCol
                anchors.centerIn: parent
                spacing: 2

                Text {
                    text: formatNumber(dashboard.stats.alltimeKeystrokesSaved || 0)
                    font.pixelSize: 34
                    font.weight: Font.Bold
                    color: "#66dd88"
                    Layout.alignment: Qt.AlignHCenter
                }
                Text {
                    text: "keystrokes saved"
                    font.pixelSize: 11
                    color: "#88aa88"
                    Layout.alignment: Qt.AlignHCenter
                }
            }
        }

        // ===== All-time stats row =====
        RowLayout {
            Layout.fillWidth: true
            spacing: 6

            StatPill {
                label: "words"
                value: formatNumber(dashboard.stats.alltimeWords || 0)
            }
            StatPill {
                label: "sessions"
                value: String(dashboard.stats.alltimeSessions || 0)
            }
            StatPill {
                label: "hours"
                value: ((dashboard.stats.alltimeMinutes || 0) / 60).toFixed(1)
            }
        }

        // ===== PREDICTION QUALITY =====
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 32
            radius: 6
            color: "#252525"
            visible: (dashboard.stats.qualityScore || 0) > 0

            RowLayout {
                anchors.fill: parent
                anchors.margins: 6
                spacing: 8

                Text {
                    text: "Prediction Quality"
                    font.pixelSize: 11
                    color: "#888"
                }

                // Progress bar
                Rectangle {
                    Layout.fillWidth: true
                    height: 8
                    radius: 4
                    color: "#1a1a1a"

                    Rectangle {
                        width: parent.width * Math.min(1, (dashboard.stats.qualityScore || 0) / 100)
                        height: parent.height
                        radius: parent.radius
                        color: {
                            var q = dashboard.stats.qualityScore || 0
                            if (q >= 70) return "#66dd88"
                            if (q >= 40) return "#ddcc66"
                            return "#dd6666"
                        }
                        Behavior on width { NumberAnimation { duration: 300 } }
                    }
                }

                Text {
                    text: (dashboard.stats.qualityScore || 0) + "/100"
                    font.pixelSize: 12
                    font.weight: Font.Bold
                    color: {
                        var q = dashboard.stats.qualityScore || 0
                        if (q >= 70) return "#66dd88"
                        if (q >= 40) return "#ddcc66"
                        return "#dd6666"
                    }
                    Layout.preferredWidth: 44
                    horizontalAlignment: Text.AlignRight
                }
            }
        }

        // Divider
        Rectangle {
            Layout.fillWidth: true
            height: 1
            color: "#333"
            Layout.topMargin: 2
            Layout.bottomMargin: 2
        }

        // ===== THIS SESSION =====
        Text {
            text: "This Session"
            font.pixelSize: 11
            font.weight: Font.DemiBold
            color: "#888"
        }

        // Session stats grid
        GridLayout {
            Layout.fillWidth: true
            columns: 2
            rowSpacing: 6
            columnSpacing: 6

            StatBox {
                label: "Speed"
                value: (dashboard.stats.wpm || 0).toFixed(1)
                unit: "wpm"
                accent: "#4a9eff"
            }

            StatBox {
                label: "Saved"
                value: String(dashboard.stats.keystrokesSaved || 0)
                unit: (dashboard.stats.savingsPercent || 0).toFixed(0) + "% of typing"
                accent: "#66dd88"
            }

            StatBox {
                label: "Predictions Used"
                value: (dashboard.stats.predictionHitRate || 0).toFixed(0) + "%"
                unit: (dashboard.stats.predictionHits || 0) + " of " + (dashboard.stats.totalWords || 0) + " words"
                accent: "#bb88ff"
            }

            StatBox {
                label: "Corrections"
                value: (dashboard.stats.backspaceRate || 0).toFixed(0) + "%"
                unit: "backspace rate"
                accent: dashboard.stats.backspaceRate > 20 ? "#ffaa66" : "#888"
            }
        }

        // WPM sparkline
        Item {
            Layout.fillWidth: true
            implicitHeight: 40
            visible: (dashboard.stats.wpmSamples || []).length > 1

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

        // Top words
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 3
            visible: (dashboard.stats.topWords || []).length > 0

            Text {
                text: "Top Words"
                font.pixelSize: 11
                font.weight: Font.DemiBold
                color: "#888"
            }

            Repeater {
                model: dashboard.stats.topWords || []

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6

                    Rectangle {
                        width: Math.max(4, Math.min(60, modelData.count * 3))
                        height: 12
                        radius: 2
                        color: "#4a9eff"
                        opacity: 1.0 - (index * 0.15)
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

    // All-time stat pill (compact, inline)
    component StatPill: Rectangle {
        property string label: ""
        property string value: ""

        Layout.fillWidth: true
        implicitHeight: 36
        radius: 6
        color: "#252525"

        ColumnLayout {
            anchors.centerIn: parent
            spacing: 0

            Text {
                text: parent.parent.value
                font.pixelSize: 14
                font.weight: Font.Bold
                color: "#ddd"
                Layout.alignment: Qt.AlignHCenter
            }
            Text {
                text: parent.parent.label
                font.pixelSize: 9
                color: "#777"
                Layout.alignment: Qt.AlignHCenter
            }
        }
    }

    // Session stat box
    component StatBox: ColumnLayout {
        property string label: ""
        property string value: ""
        property string unit: ""
        property string accent: "#e0e0e0"

        Layout.fillWidth: true
        spacing: 0

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 50
            radius: 6
            color: "#2a2a2a"

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 6
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
                    font.pixelSize: 9
                    color: "#666"
                    visible: parent.parent.parent.unit !== ""
                }
            }
        }
    }
}
