import QtQuick

Item {
  id: root

  property real iconSize: 24

  implicitWidth: iconSize
  implicitHeight: iconSize

  Image {
    anchors.fill: parent
    source: Qt.resolvedUrl("assets/calibre.svg")
    sourceSize.width: Math.max(1, width * 2)
    sourceSize.height: Math.max(1, height * 2)
    fillMode: Image.PreserveAspectFit
    asynchronous: true
    cache: true
    mipmap: true
  }
}
