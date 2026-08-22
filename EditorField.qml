import QtQuick
import qs.Commons
import qs.Ui

Column {
  id: root

  property string label: ""
  property real fieldWidth: Style.space(200)
  property color foreground: Color.foreground
  property alias text: field.text

  width: fieldWidth
  spacing: Style.space(3)

  Text {
    width: parent.width
    text: root.label.toUpperCase()
    color: Qt.darker(root.foreground, 1.4)
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
    font.bold: true
    elide: Text.ElideRight
  }

  TextField {
    id: field
    width: parent.width
    foreground: root.foreground
  }
}
