import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: debugPanel
    width: 400
    height: 250
    radius: 8
    color: "#0a0a0a"
    border.color: "#4a9eff"
    border.width: 1

    property var logEntries: []
    property string currentContext: ""
    property var currentPredictions: []

    signal closeRequested()
    signal clearLog()

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 8
        spacing: 6

        // Header
        RowLayout {
            Layout.fillWidth: true
            
            Text {
                text: "🐛 Debug Console"
                color: "#4a9eff"
                font.pixelSize: 12
                font.bold: true
            }
            
            Item { Layout.fillWidth: true }
            
            Rectangle {
                width: 60
                height: 20
                radius: 3
                color: clearBtn.containsMouse ? "#333" : "#222"
                border.color: "#444"
                
                Text {
                    anchors.centerIn: parent
                    text: "Clear"
                    color: "#888"
                    font.pixelSize: 10
                }
                
                MouseArea {
                    id: clearBtn
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: debugPanel.clearLog()
                }
            }
            
            Rectangle {
                width: 20
                height: 20
                radius: 3
                color: closeDbgBtn.containsMouse ? "#333" : "transparent"
                
                Text {
                    anchors.centerIn: parent
                    text: "✕"
                    color: closeDbgBtn.containsMouse ? "#ff6666" : "#888"
                    font.pixelSize: 10
                }
                
                MouseArea {
                    id: closeDbgBtn
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: debugPanel.closeRequested()
                }
            }
        }

        // Context display
        Rectangle {
            Layout.fillWidth: true
            height: 40
            radius: 4
            color: "#151515"
            border.color: "#333"

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 6
                spacing: 2

                Text {
                    text: "Context: " + (debugPanel.currentContext || "(empty)")
                    textFormat: Text.PlainText
                    color: "#aaa"
                    font.pixelSize: 10
                    font.family: "monospace"
                    elide: Text.ElideLeft
                    Layout.fillWidth: true
                }

                Text {
                    text: "Predictions: [" + debugPanel.currentPredictions.join(", ") + "]"
                    textFormat: Text.PlainText
                    color: "#6ab4ff"
                    font.pixelSize: 10
                    font.family: "monospace"
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }
        }

        // Log entries
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: 4
            color: "#0d0d0d"
            border.color: "#222"

            ListView {
                id: logView
                anchors.fill: parent
                anchors.margins: 4
                clip: true
                model: debugPanel.logEntries
                
                delegate: Text {
                    width: logView.width
                    text: modelData
                    textFormat: Text.PlainText
                    color: {
                        if (modelData.indexOf("ERROR") >= 0) return "#ff6666"
                        if (modelData.indexOf("WARN") >= 0) return "#ffaa44"
                        if (modelData.indexOf("Import") >= 0) return "#66ff66"
                        return "#888"
                    }
                    font.pixelSize: 9
                    font.family: "monospace"
                    wrapMode: Text.WordWrap
                }
                
                // Auto-scroll to bottom
                onCountChanged: {
                    positionViewAtEnd()
                }
            }
        }

        // Quick info bar
        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Text {
                text: "Log: " + debugPanel.logEntries.length + " entries"
                color: "#666"
                font.pixelSize: 9
            }
            
            Item { Layout.fillWidth: true }
            
            Text {
                text: "Context length: " + debugPanel.currentContext.length
                color: "#666"
                font.pixelSize: 9
            }
        }
    }
}
