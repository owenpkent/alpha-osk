import QtQuick 2.15
import QtQuick.Layouts 1.15

Item {
    id: section
    
    property string title: "Section"
    default property alias content: contentArea.data
    
    implicitWidth: parent ? parent.width : 200
    implicitHeight: sectionColumn.implicitHeight
    
    ColumnLayout {
        id: sectionColumn
        anchors.fill: parent
        spacing: 8

        Text {
            text: section.title
            color: "#aaa"
            font.pixelSize: 12
            font.bold: true
            Layout.topMargin: 4
            Layout.leftMargin: 2
        }
        
        ColumnLayout {
            id: contentArea
            Layout.fillWidth: true
            spacing: 6
        }
    }
}
