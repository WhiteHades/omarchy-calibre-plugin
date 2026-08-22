pragma ComponentBehavior: Bound

import QtQuick
import Quickshell
import qs.Commons
import "." as Plugin

ShellRoot {
  id: root

  QtObject {
    id: fakeShell
    property var bar: fakeBar
    function updateEntryInline(moduleName, settings) { return true }
  }

  QtObject {
    id: fakeBar
    property bool vertical: false
    property int barSize: 28
    property string position: "top"
    property string fontFamily: Style.font.family
    property color foreground: Color.foreground
    property color barForeground: Color.foreground
    property color background: Color.background
    property color urgent: Color.urgent
    property var shell: fakeShell
    property var activePopout: null
    property var clickTargets: []
    property bool centerHoverRevealSuppressed: false
    property bool foregroundAnimationEnabled: false

    function run(command) {}
    function showTooltip(owner, text) {}
    function hideTooltip(owner) {}
    function moduleWidgets(moduleName) { return [widget] }
    function requestPopout(owner) { activePopout = owner }
    function releasePopout(owner) { if (activePopout === owner) activePopout = null }
    function switchPanelFrom(owner, direction) { return false }
    function registerClickTarget(target) {}
    function unregisterClickTarget(target) {}
    function targetBelongsToWindow(target, window) { return true }
  }

  PanelWindow {
    id: barWindow
    anchors { top: true; left: true; right: true }
    implicitHeight: 36
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore

    Rectangle {
      anchors.fill: parent
      color: Color.background

      Plugin.BarWidget {
        id: widget
        width: 32
        height: 32
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.verticalCenter: parent.verticalCenter
        bar: fakeBar
        settings: ({ pageSize: 50 })
      }
    }
  }

  Timer {
    interval: 300
    running: true
    repeat: false
    onTriggered: widget.open()
  }

  Timer {
    id: safetyTimeout
    interval: 6000
    running: true
    repeat: false
    onTriggered: Qt.quit()
  }
}
