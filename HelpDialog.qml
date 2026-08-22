import QtQuick
import QtQuick.Controls as QQC
import qs.Commons
import qs.Ui

BorderSurface {
  id: root

  property string calibreVersion: ""
  property string calibreStatus: ""
  property string libraryName: ""
  property string libraryPath: ""
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family

  signal retryRequested()
  signal canceled()

  color: Color.background
  borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Style.normalBorderWidth)
  radius: Style.cornerRadius
  focus: visible

  Keys.onEscapePressed: root.canceled()
  onVisibleChanged: if (visible) Qt.callLater(function() { root.forceActiveFocus() })

  readonly property var shortcuts: [
    { label: "Search", key: "/" },
    { label: "Commands", key: "P or :" },
    { label: "Move", key: "Arrow keys" },
    { label: "Run selected action", key: "Enter" },
    { label: "Open book", key: "O" },
    { label: "Edit metadata", key: "E" },
    { label: "Convert", key: "C" },
    { label: "Send to reader", key: "D" },
    { label: "Export", key: "S" },
    { label: "Manage formats", key: "F" },
    { label: "Refresh", key: "R" },
    { label: "Close", key: "Esc" }
  ]

  readonly property var diagnostics: [
    { label: "Plugin", value: "Calibre 0.1.0" },
    { label: "Calibre", value: calibreVersion || "Not detected" },
    { label: "Status", value: calibreStatus || "Unknown" },
    { label: "Library", value: libraryName || "None selected" },
    { label: "Path", value: libraryPath || "—" }
  ]

  Column {
    anchors.fill: parent
    anchors.margins: Style.space(18)
    spacing: Style.space(12)

    Row {
      width: parent.width
      spacing: Style.space(12)

      CalibreIcon {
        width: Style.space(32)
        height: width
        iconSize: width
        anchors.verticalCenter: parent.verticalCenter
      }

      Column {
        width: Math.max(0, parent.width - Style.space(44) - closeButton.width - parent.spacing)
        spacing: Style.space(2)

        Text {
          textFormat: Text.PlainText
          width: parent.width
          text: "Calibre help"
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.title
          font.bold: true
          elide: Text.ElideRight
        }

        Text {
          textFormat: Text.PlainText
          width: parent.width
          text: "Keyboard shortcuts and local runtime status"
          color: Qt.darker(root.foreground, 1.45)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      PanelActionButton {
        id: closeButton
        iconText: "󰅖"
        tooltipText: "Close"
        foreground: root.foreground
        fontFamily: root.fontFamily
        focusable: true
        onClicked: root.canceled()
      }
    }

    PanelSeparator { width: parent.width }

    Flickable {
      width: parent.width
      height: Math.max(0, parent.height - y - footer.height - parent.spacing)
      contentWidth: width
      contentHeight: content.implicitHeight
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      flickableDirection: Flickable.VerticalFlick

      QQC.ScrollBar.vertical: QQC.ScrollBar { policy: QQC.ScrollBar.AsNeeded }

      Column {
        id: content
        width: parent.width
        spacing: Style.space(10)

        PanelSectionHeader {
          text: "KEYBOARD"
          foreground: root.foreground
          fontFamily: root.fontFamily
        }

        Grid {
          width: parent.width
          columns: 2
          columnSpacing: Style.space(16)
          rowSpacing: Style.space(5)

          Repeater {
            model: root.shortcuts

            Row {
              required property var modelData
              width: Math.floor((content.width - Style.space(16)) / 2)
              spacing: Style.space(8)

              Text {
                textFormat: Text.PlainText
                width: Math.max(0, parent.width - shortcutKey.width - parent.spacing)
                text: modelData.label
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                elide: Text.ElideRight
              }

              Text {
                textFormat: Text.PlainText
                id: shortcutKey
                text: modelData.key
                color: Color.accent
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
              }
            }
          }
        }

        PanelSeparator { width: parent.width }

        PanelSectionHeader {
          text: "DIAGNOSTICS"
          foreground: root.foreground
          fontFamily: root.fontFamily
        }

        Column {
          width: parent.width
          spacing: Style.space(5)

          Repeater {
            model: root.diagnostics

            Row {
              required property var modelData
              width: parent.width
              spacing: Style.space(12)

              Text {
                textFormat: Text.PlainText
                width: Style.space(80)
                text: modelData.label.toUpperCase()
                color: Qt.darker(root.foreground, 1.45)
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
                elide: Text.ElideRight
              }

              Text {
                textFormat: Text.PlainText
                width: Math.max(0, parent.width - x)
                text: modelData.value
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                elide: Text.ElideMiddle
              }
            }
          }
        }
      }
    }

    Row {
      id: footer
      width: parent.width
      spacing: Style.space(8)

      Item { width: Math.max(0, parent.width - retryButton.width - closeFooterButton.width - parent.spacing * 2); height: 1 }

      Button {
        id: retryButton
        text: "Refresh status"
        foreground: root.foreground
        fontFamily: root.fontFamily
        focusable: true
        onClicked: root.retryRequested()
      }

      Button {
        id: closeFooterButton
        text: "Close"
        bordered: true
        foreground: root.foreground
        fontFamily: root.fontFamily
        focusable: true
        onClicked: root.canceled()
      }
    }
  }
}
