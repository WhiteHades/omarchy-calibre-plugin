import QtQuick
import qs.Commons
import qs.Ui
import "Model.js" as Model

BorderSurface {
  id: root

  property var book: null
  property color foreground: Color.foreground
  property color urgent: Color.urgent
  property string fontFamily: Style.font.family

  signal openRequested(var format)
  signal addRequested()
  signal removeRequested(var format)
  signal canceled()

  color: Color.background
  borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Style.normalBorderWidth)
  radius: Style.cornerRadius
  focus: visible

  onVisibleChanged: if (visible) Qt.callLater(function() { root.forceActiveFocus() })
  Keys.onEscapePressed: root.canceled()

  Column {
    anchors.fill: parent
    anchors.margins: Style.space(18)
    spacing: Style.space(12)

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
        width: Math.max(0, parent.width - Style.space(46) - closeButton.width - parent.spacing)
        spacing: Style.space(2)

        Text {
          textFormat: Text.PlainText
          width: parent.width
          text: "Manage formats"
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.title
          font.bold: true
          elide: Text.ElideRight
        }

        Text {
          textFormat: Text.PlainText
          width: parent.width
          text: root.book ? root.book.title : ""
          color: Qt.darker(root.foreground, 1.45)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      CalibreActionButton {
        id: closeButton
        iconText: "󰅖"
        tooltipText: "Close"
        foreground: root.foreground
        fontFamily: root.fontFamily
        onClicked: root.canceled()
      }
    }

    PanelSeparator { width: parent.width }

    Text {
      textFormat: Text.PlainText
      width: parent.width
      text: "Open a format with its default application, attach another file, or remove a format from this record."
      color: Qt.darker(root.foreground, 1.45)
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.WordWrap
    }

    Flickable {
      width: parent.width
      height: Math.max(0, parent.height - y - addButton.height - parent.spacing)
      contentWidth: width
      contentHeight: formatColumn.implicitHeight
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      flickableDirection: Flickable.VerticalFlick

      Column {
        id: formatColumn
        width: parent.width
        spacing: Style.space(5)

        Repeater {
          model: root.book && root.book.formats instanceof Array ? root.book.formats : []

          BorderSurface {
            id: formatRow
            required property var modelData
            width: formatColumn.width
            implicitHeight: formatContent.implicitHeight + Style.space(12)
            color: "transparent"
            borderSpec: Border.controlSpec("normal", root.foreground, Color.accent)
            radius: Style.cornerRadius

            Row {
              id: formatContent
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.margins: Style.space(6)
              spacing: Style.space(8)

              Column {
                width: Math.max(0, parent.width - openButton.width - removeButton.width - parent.spacing * 2)
                spacing: Style.space(2)

                Text {
                  textFormat: Text.PlainText
                  width: parent.width
                  text: String(formatRow.modelData.name || "").toUpperCase()
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                  elide: Text.ElideRight
                }

                Text {
                  textFormat: Text.PlainText
                  width: parent.width
                  text: Model.formatBytes(formatRow.modelData.size)
                  color: Qt.darker(root.foreground, 1.5)
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }
              }

              CalibreButton {
                id: openButton
                text: "Open"
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.openRequested(formatRow.modelData)
              }

              CalibreButton {
                id: removeButton
                text: "Remove"
                foreground: root.urgent
                fontFamily: root.fontFamily
                onClicked: root.removeRequested(formatRow.modelData)
              }
            }
          }
        }

        Text {
          textFormat: Text.PlainText
          visible: !root.book || !(root.book.formats instanceof Array) || root.book.formats.length === 0
          width: parent.width
          text: "This record has no attached book files."
          color: Qt.darker(root.foreground, 1.45)
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          horizontalAlignment: Text.AlignHCenter
        }
      }
    }

    CalibreButton {
      id: addButton
      width: parent.width
      text: "Add or replace a format"
      iconText: "+"
      bordered: true
      foreground: root.foreground
      fontFamily: root.fontFamily
      onClicked: root.addRequested()
    }
  }
}
