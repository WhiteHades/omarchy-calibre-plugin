import QtQuick
import qs.Commons
import qs.Ui

BorderSurface {
  id: root

  property var jobs: []
  property color foreground: Color.foreground
  property color urgent: Color.urgent
  property string fontFamily: Style.font.family

  signal cancelRequested(string requestId)
  signal forgetRequested(string requestId)
  signal canceled()

  color: Color.background
  borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Style.normalBorderWidth)
  radius: Style.cornerRadius
  focus: visible

  onVisibleChanged: if (visible) Qt.callLater(function() { root.forceActiveFocus() })
  Keys.onEscapePressed: root.canceled()

  function stateLabel(job) {
    if (job.state === "running") return job.message || "Working"
    if (job.state === "succeeded") return "Complete"
    if (job.state === "cancelled") return "Cancelled"
    if (job.state === "failed") return job.error && job.error.message ? job.error.message : "Failed"
    return String(job.state || "Queued")
  }

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
          text: "Calibre jobs"
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.title
          font.bold: true
          elide: Text.ElideRight
        }

        Text {
          textFormat: Text.PlainText
          width: parent.width
          text: "Track active work or clear completed results."
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
        onClicked: root.canceled()
      }
    }

    PanelSeparator { width: parent.width }

    Flickable {
      width: parent.width
      height: Math.max(0, parent.height - y)
      contentWidth: width
      contentHeight: jobColumn.implicitHeight
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      flickableDirection: Flickable.VerticalFlick

      Column {
        id: jobColumn
        width: parent.width
        spacing: Style.space(6)

        Repeater {
          model: root.jobs

          BorderSurface {
            id: jobRow
            required property var modelData
            width: jobColumn.width
            implicitHeight: jobContent.implicitHeight + Style.space(12)
            color: "transparent"
            borderSpec: Border.controlSpec("normal", root.foreground, Color.accent)
            radius: Style.cornerRadius

            Column {
              id: jobContent
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.margins: Style.space(6)
              spacing: Style.space(6)

              Row {
                width: parent.width
                spacing: Style.space(8)

                Column {
                  width: Math.max(0, parent.width - actionButton.width - parent.spacing)
                  spacing: Style.space(2)

                  Text {
                    textFormat: Text.PlainText
                    width: parent.width
                    text: String(jobRow.modelData.label || "Calibre operation")
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    font.bold: true
                    elide: Text.ElideRight
                  }

                  Text {
                    textFormat: Text.PlainText
                    width: parent.width
                    text: root.stateLabel(jobRow.modelData)
                    color: jobRow.modelData.state === "failed" ? root.urgent : Qt.darker(root.foreground, 1.45)
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    elide: Text.ElideRight
                  }
                }

                Button {
                  id: actionButton
                  text: jobRow.modelData.state === "running" ? "Cancel" : "Clear"
                  foreground: jobRow.modelData.state === "failed" ? root.urgent : root.foreground
                  fontFamily: root.fontFamily
                  onClicked: {
                    if (jobRow.modelData.state === "running") root.cancelRequested(jobRow.modelData.id)
                    else root.forgetRequested(jobRow.modelData.id)
                  }
                }
              }

              BorderSurface {
                visible: jobRow.modelData.state === "running"
                width: parent.width
                height: Style.space(4)
                color: Qt.darker(root.foreground, 2.4)
                borderSpec: Border.flat("transparent", 0)
                radius: Style.cornerRadius

                Rectangle {
                  width: parent.width * Math.max(0.03, Math.min(1, Number(jobRow.modelData.fraction || 0)))
                  height: parent.height
                  color: Color.accent
                  radius: parent.radius
                }
              }
            }
          }
        }

        Text {
          textFormat: Text.PlainText
          visible: !(root.jobs instanceof Array) || root.jobs.length === 0
          width: parent.width
          text: "No Calibre jobs in this session."
          color: Qt.darker(root.foreground, 1.45)
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          horizontalAlignment: Text.AlignHCenter
        }
      }
    }
  }
}
