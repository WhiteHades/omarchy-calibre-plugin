import QtQuick
import QtQuick.Effects
import Quickshell
import qs.Commons

Item {
  id: root

  property real iconSize: 24
  property color color: Color.foreground

  width: iconSize
  height: iconSize
  implicitWidth: iconSize
  implicitHeight: iconSize

  Image {
    id: sourceImage
    anchors.fill: parent
    source: Qt.resolvedUrl("assets/calibre.svg")
    sourceSize.width: Math.max(1, Math.round(width * Screen.devicePixelRatio))
    sourceSize.height: Math.max(1, Math.round(height * Screen.devicePixelRatio))
    fillMode: Image.PreserveAspectFit
    asynchronous: true
    cache: true
    mipmap: true
    visible: false
    layer.enabled: true
  }

  Rectangle {
    anchors.fill: parent
    color: root.color
    layer.enabled: true
    layer.smooth: true
    layer.effect: MultiEffect {
      maskEnabled: true
      maskSource: sourceImage
      maskThresholdMin: 0.12
      maskSpreadAtMin: 0.25
    }
  }
}
