import QtQuick

Item {
  id: root

  required property Item anchorItem
  required property QtObject bar
  property var owner: null
  property bool open: false
  property bool centerOnBar: false
  property Item focusTarget: null
  property int contentWidth: 1
  property int contentHeight: 1
  readonly property bool backingWindowVisible: visible

  default property alias contentItem: contentHolder.children

  visible: open
  width: contentWidth
  height: contentHeight

  function fittedContentWidth(width, cap) {
    var desired = Math.max(1, Number(width) || 1)
    return Math.round(cap && Number(cap) > 0 ? Math.min(desired, Number(cap)) : desired)
  }

  function fittedContentHeight(height, cap) {
    var desired = Math.max(1, Number(height) || 1)
    return Math.round(cap && Number(cap) > 0 ? Math.min(desired, Number(cap)) : desired)
  }

  Item {
    id: contentHolder
    anchors.fill: parent
  }
}
