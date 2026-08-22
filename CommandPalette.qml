import QtQuick
import qs.Commons
import qs.Ui
import "Model.js" as Model

BorderSurface {
  id: root

  property var commands: []
  property var filteredCommands: []
  property int selectedIndex: 0
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family

  signal commandRequested(string commandId)
  signal canceled()

  color: Color.background
  borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Style.normalBorderWidth)
  radius: Style.cornerRadius

  function filterCommands() {
    var values = []
    var source = commands instanceof Array ? commands : []
    for (var i = 0; i < source.length; i++) {
      if (Model.commandMatches(source[i], filterField.text)) values.push(source[i])
    }
    filteredCommands = values
    selectedIndex = Math.max(0, Math.min(selectedIndex, values.length - 1))
  }

  function executeSelected() {
    if (selectedIndex < 0 || selectedIndex >= filteredCommands.length) return
    commandRequested(String(filteredCommands[selectedIndex].id || ""))
  }

  onCommandsChanged: filterCommands()
  onVisibleChanged: if (visible) {
    filterField.text = ""
    selectedIndex = 0
    filterCommands()
    Qt.callLater(function() { filterField.forceActiveFocus() })
  }
  Component.onCompleted: filterCommands()

  Column {
    anchors.fill: parent
    anchors.margins: Style.space(18)
    spacing: Style.space(10)

    Row {
      width: parent.width
      spacing: Style.space(12)

      CalibreIcon {
        width: Style.space(34)
        height: width
        iconSize: width
        anchors.verticalCenter: parent.verticalCenter
      }

      Column {
        width: Math.max(0, parent.width - Style.space(46))
        spacing: Style.space(2)

        Text {
          width: parent.width
          text: "Calibre commands"
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.title
          font.bold: true
          elide: Text.ElideRight
        }

        Text {
          width: parent.width
          text: "Type an action, then press Enter."
          color: Qt.darker(root.foreground, 1.45)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }
    }

    TextField {
      id: filterField
      width: parent.width
      placeholderText: "Find a command"
      foreground: root.foreground
      onTextChanged: root.filterCommands()
      Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Escape) {
          root.canceled()
          event.accepted = true
        } else if (event.key === Qt.Key_Down) {
          root.selectedIndex = Math.min(root.filteredCommands.length - 1, root.selectedIndex + 1)
          event.accepted = true
        } else if (event.key === Qt.Key_Up) {
          root.selectedIndex = Math.max(0, root.selectedIndex - 1)
          event.accepted = true
        } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
          root.executeSelected()
          event.accepted = true
        }
      }
    }

    PanelSeparator { width: parent.width }

    ListView {
      id: commandList
      width: parent.width
      height: Math.max(0, parent.height - y - helpText.height - parent.spacing)
      model: root.filteredCommands
      currentIndex: root.selectedIndex
      spacing: Style.space(3)
      clip: true
      boundsBehavior: Flickable.StopAtBounds

      delegate: CursorSurface {
        id: commandRow
        required property var modelData
        required property int index
        width: commandList.width
        height: Style.space(42)
        current: index === root.selectedIndex
        hasCursor: current
        foreground: root.foreground

        Row {
          anchors.fill: parent
          anchors.margins: Style.space(7)
          spacing: Style.space(8)

          Text {
            width: Math.max(0, parent.width - shortcutText.width - parent.spacing)
            text: String(commandRow.modelData.label || "")
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            elide: Text.ElideRight
          }

          Text {
            id: shortcutText
            text: String(commandRow.modelData.key || "")
            color: Qt.darker(root.foreground, 1.4)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }

        MouseArea {
          anchors.fill: parent
          hoverEnabled: true
          cursorShape: Qt.PointingHandCursor
          onEntered: root.selectedIndex = commandRow.index
          onClicked: root.executeSelected()
        }
      }

      Text {
        visible: root.filteredCommands.length === 0
        anchors.centerIn: parent
        text: "No matching commands."
        color: Qt.darker(root.foreground, 1.45)
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
      }
    }

    Text {
      id: helpText
      width: parent.width
      text: "↑↓ select  ·  Enter run  ·  Esc close"
      color: Qt.darker(root.foreground, 1.55)
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      horizontalAlignment: Text.AlignHCenter
    }
  }
}
