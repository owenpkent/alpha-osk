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
        spacing: 12

        // WPM — large hero stat
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            Layout.alignment: Qt.AlignHCenter

            Text {
                text: (dashboard.stats.wpm || 0).toFixed(1)
                font.pixelSize: 36
                font.weight: Font.Bold
                color: "#4a9eff"
                Layout.alignment: Qt.AlignHCenter
            }
            Text {
                text: "words per minute"
                font.pixelSize: 11
                color: "#888"
                Layout.alignment: Qt.AlignHCenter
            }
        }

        // Stats grid
        GridLayout {
            Layout.fillWidth: true
            columns: 2
            rowSpacing: 6
            columnSpacing: 12

            // Prediction hit rate
            StatBox {
                label: "Prediction Use"
                value: (dashboard.stats.predictionHitRate || 0).toFixed(0) + "%"
                detail: (dashboard.stats.predictionHits || 0) + " used"
            }

            // Backspace rate
            StatBox {
                label: "Correction Rate"
                value: (dashboard.stats.backspaceRate || 0).toFixed(0) + "%"
                detail: (dashboard.stats.totalKeystrokes || 0) + " keys"
            }

            // Total words
            StatBox {
                label: "Words Typed"
                value: String(dashboard.stats.totalWords || 0)
                detail: (dashboard.stats.sessionMinutes || 0).toFixed(0) + " min session"
            }

            // Avg prediction rank
            StatBox {
                label: "Avg Prediction Rank"
                value: dashboard.stats.predictionHits > 0
                       ? "#" + (dashboard.stats.avgPredictionRank || 0).toFixed(1) : "--"
                detail: "position selected"
            }
        }

        // Top words
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4
            visible: (dashboard.stats.topWords || []).length > 0

            Text {
                text: "Top Words"
                font.pixelSize: 11
                font.weight: Font.DemiBold
                color: "#aaa"
            }

            Repeater {
                model: dashboard.stats.topWords || []

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6

                    Rectangle {
                        width: 4
                        height: 14
                        radius: 2
                        color: "#4a9eff"
                        opacity: 1.0 - (index * 0.08)
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
                        color: "#888"
                    }
                }
            }
        }
    }

    // Stat box helper component
    component StatBox: ColumnLayout {
        property string label: ""
        property string value: ""
        property string detail: ""

        Layout.fillWidth: true
        spacing: 1

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 48
            radius: 6
            color: "#2a2a2a"

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 6
                spacing: 1

                Text {
                    text: parent.parent.parent.value
                    font.pixelSize: 18
                    font.weight: Font.Bold
                    color: "#e0e0e0"
                }
                Text {
                    text: parent.parent.parent.label
                    font.pixelSize: 10
                    color: "#888"
                }
            }
        }

        Text {
            text: detail
            font.pixelSize: 9
            color: "#666"
            Layout.leftMargin: 2
        }
    }
}
