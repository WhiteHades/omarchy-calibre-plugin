import QtQuick
import qs.Commons
import qs.Ui

// The reader flow stays small on purpose: choose the book format, send it,
// then leave the reader ready to use or eject it safely. The bridge owns all
// device paths and transport details; this component only receives normalized
// state and emits user intent.
BorderSurface {
  id: root

  property var book: null
  property var preferredFormats: ["EPUB", "AZW3", "PDF", "MOBI"]
  property string selectedFormat: ""
  property string deviceState: "probing"
  property string conflictFormat: ""
  property var deviceInfo: null
  property var deviceError: null
  property var deviceCapabilities: ({})
  property real progressFraction: 0
  property string progressMessage: ""
  property color foreground: Color.foreground
  property color urgent: Color.urgent
  property string fontFamily: Style.font.family

  signal sendRequested(string format, bool replace)
  signal retryRequested()
  signal ejectRequested()
  signal cancelRequested()
  signal canceled()

  color: Color.background
  borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Style.normalBorderWidth)
  radius: Style.cornerRadius
  focus: visible

  readonly property string normalizedState: {
    var value = String(root.deviceState || "probing").toLowerCase()
    return value === "loading" ? "probing"
      : value === "connected" ? "ready"
      : value === "unavailable" || value === "unsupported" ? "error"
      : value
  }
  readonly property bool readerReady: normalizedState === "ready"
    || normalizedState === "sending" || normalizedState === "sent"
    || normalizedState === "conflict" || normalizedState === "ejecting"
  readonly property bool retryableState: normalizedState === "no-device"
    || normalizedState === "locked" || normalizedState === "error"
    || normalizedState === "ejected"
  readonly property bool sending: normalizedState === "sending"
  readonly property bool replacesConflict: normalizedState === "conflict"
    && conflictFormat !== "" && selectedFormat === conflictFormat
  readonly property bool formatAvailable: (formatOptions || []).length > 0
  readonly property bool sendAvailable: capability("send", true)
  readonly property bool ejectAvailable: capability("eject", false)
  readonly property var formatOptions: buildFormatOptions()
  readonly property string readerName: {
    var info = root.deviceInfo || ({})
    return String(info.deviceName || "ebook reader")
  }
  readonly property string errorText: {
    var error = root.deviceError || ({})
    return String(error.message || "The ebook reader could not be reached.")
  }

  function capability(name, fallback) {
    var source = root.deviceCapabilities || ({})
    var supports = source.supports || ({})
    if (supports[name] !== undefined) return supports[name] === true
    if (source[name] !== undefined) return source[name] === true
    return fallback
  }

  function formatName(value) {
    if (value && typeof value === "object")
      return String(value.name || value.format || value.value || "").toUpperCase()
    return String(value || "").toUpperCase()
  }

  function formatSize(value) {
    var size = value && typeof value === "object" ? Number(value.size || 0) : 0
    if (!isFinite(size) || size <= 0) return ""
    if (size >= 1024 * 1024) return (size / (1024 * 1024)).toFixed(1) + " MB"
    if (size >= 1024) return Math.round(size / 1024) + " KB"
    return size + " B"
  }

  function preferredList() {
    var source = root.preferredFormats
    if (source instanceof Array) return source
    return String(source || "").split(",")
  }

  function isPreferred(name) {
    var wanted = preferredList()
    for (var i = 0; i < wanted.length; i++) {
      if (formatName(wanted[i]) === name) return true
    }
    return false
  }

  function preferredRank(name) {
    var wanted = preferredList()
    for (var i = 0; i < wanted.length; i++) {
      if (formatName(wanted[i]) === name) return i
    }
    return wanted.length + 1
  }

  function buildFormatOptions() {
    var source = root.book && root.book.formats instanceof Array ? root.book.formats : []
    var values = []
    for (var i = 0; i < source.length; i++) {
      var name = formatName(source[i])
      if (!name || values.some(function(item) { return item.value === name })) continue
      var size = formatSize(source[i])
      values.push({
        value: name,
        label: name + (isPreferred(name) ? "  ·  preferred" : (size ? "  ·  " + size : "")),
        rank: preferredRank(name)
      })
    }
    values.sort(function(left, right) {
      if (left.rank !== right.rank) return left.rank - right.rank
      return left.value.localeCompare(right.value)
    })
    return values
  }

  function syncSelectedFormat() {
    var options = root.formatOptions || []
    for (var i = 0; i < options.length; i++) {
      if (options[i].value === root.selectedFormat) return
    }
    root.selectedFormat = options.length > 0 ? options[0].value : ""
  }

  function stateTitle() {
    if (normalizedState === "probing") return "Checking for a reader"
    if (normalizedState === "no-device") return "No reader connected"
    if (normalizedState === "locked") return "Reader is locked"
    if (normalizedState === "error") return "Reader unavailable"
    if (normalizedState === "conflict") return "Book already on reader"
    if (normalizedState === "sending") return "Sending to " + readerName
    if (normalizedState === "sent") return "Book sent"
    if (normalizedState === "ejecting") return "Ejecting reader"
    if (normalizedState === "ejected") return "Reader ejected"
    return "Send to reader"
  }

  function stateDescription() {
    if (normalizedState === "probing") return "Looking for a supported ebook reader."
    if (normalizedState === "no-device") return "Connect a reader, then try again."
    if (normalizedState === "locked") return "Unlock the reader, then try again."
    if (normalizedState === "error") return root.errorText
    if (normalizedState === "conflict") return "Replace the reader copy with this library version?"
    if (normalizedState === "sending") return root.progressMessage || "Calibre is sending the selected format."
    if (normalizedState === "sent") return "The book is ready on your reader."
    if (normalizedState === "ejecting") return "Calibre is preparing the reader for safe removal."
    if (normalizedState === "ejected") return "You can disconnect the reader safely."
    return "Choose a format. Calibre will handle the reader transfer."
  }

  onBookChanged: syncSelectedFormat()
  onPreferredFormatsChanged: syncSelectedFormat()
  onVisibleChanged: if (visible) Qt.callLater(function() { root.forceActiveFocus(); root.syncSelectedFormat() })
  Keys.onEscapePressed: root.sending ? root.cancelRequested() : root.canceled()

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
          text: root.stateTitle()
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.title
          font.bold: true
          elide: Text.ElideRight
        }

        Text {
          textFormat: Text.PlainText
          width: parent.width
          text: root.book ? String(root.book.title || "") : ""
          color: Qt.darker(root.foreground, 1.45)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      PanelActionButton {
        id: closeButton
        iconText: root.sending ? "󰅖" : "󰅖"
        tooltipText: root.sending ? "Cancel sending" : "Close"
        foreground: root.foreground
        fontFamily: root.fontFamily
        focusable: true
        onClicked: root.sending ? root.cancelRequested() : root.canceled()
      }
    }

    PanelSeparator { width: parent.width }

    Item {
      width: parent.width
      height: Math.max(0, parent.height - y - footer.height - parent.spacing)

      Column {
        anchors.centerIn: parent
        width: Math.min(parent.width, Style.space(420))
        spacing: Style.space(10)

        Text {
          textFormat: Text.PlainText
          width: parent.width
          text: root.stateDescription()
          color: root.normalizedState === "error" || root.normalizedState === "locked"
            || root.normalizedState === "conflict"
            ? root.urgent : Qt.darker(root.foreground, 1.35)
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          horizontalAlignment: Text.AlignHCenter
          wrapMode: Text.WordWrap
        }

        Text {
          textFormat: Text.PlainText
          visible: root.normalizedState === "probing"
          width: parent.width
          text: "Please wait…"
          color: Qt.darker(root.foreground, 1.5)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          horizontalAlignment: Text.AlignHCenter
        }

        Column {
          visible: root.readerReady
          width: parent.width
          spacing: Style.space(8)

          Text {
            textFormat: Text.PlainText
            width: parent.width
            text: root.readerName
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.display
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
          }

          Text {
            textFormat: Text.PlainText
            visible: root.normalizedState === "ready" || root.normalizedState === "sending"
              || root.normalizedState === "conflict"
            width: parent.width
            text: root.book ? String(root.book.title || "") : ""
            color: Qt.darker(root.foreground, 1.4)
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
          }

          Dropdown {
            visible: root.normalizedState === "ready" || root.normalizedState === "sending"
              || root.normalizedState === "conflict"
            width: parent.width
            label: "BOOK FORMAT"
            options: root.formatOptions
            value: root.selectedFormat
            enabled: root.normalizedState === "ready" || root.normalizedState === "sent"
            foreground: root.foreground
            fontFamily: root.fontFamily
            onChanged: function(value) { root.selectedFormat = value }
          }

          Text {
            textFormat: Text.PlainText
            visible: root.normalizedState === "ready" && !root.formatAvailable
            width: parent.width
            text: "This book has no transferable formats."
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
          }

          BorderSurface {
            visible: root.normalizedState === "sending" || root.normalizedState === "ejecting"
            width: parent.width
            height: Style.space(5)
            color: Qt.darker(root.foreground, 2.4)
            borderSpec: Border.flat("transparent", 0)
            radius: Style.cornerRadius

            Rectangle {
              width: parent.width * Math.max(0.03, Math.min(1, Number(root.progressFraction || 0)))
              height: parent.height
              color: Color.accent
              radius: parent.radius
            }
          }

          Text {
            textFormat: Text.PlainText
            visible: root.normalizedState === "sent"
            width: parent.width
            text: root.selectedFormat ? root.selectedFormat + " is on the reader." : "The book is on the reader."
            color: Color.accent
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            horizontalAlignment: Text.AlignHCenter
          }
        }
      }
    }

    Row {
      id: footer
      width: parent.width
      spacing: Style.space(8)

      Text {
        textFormat: Text.PlainText
        width: Math.max(0, parent.width - cancelButton.width - retryButton.width - ejectButton.width - sendButton.width - parent.spacing * 4)
        text: root.normalizedState === "sending" || root.normalizedState === "ejecting"
          ? (root.progressMessage || "Working…") : ""
        color: Qt.darker(root.foreground, 1.45)
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        anchors.verticalCenter: parent.verticalCenter
        elide: Text.ElideRight
      }

      Button {
        id: cancelButton
        text: root.sending ? "Cancel" : "Close"
        foreground: root.foreground
        fontFamily: root.fontFamily
        focusable: true
        onClicked: root.sending ? root.cancelRequested() : root.canceled()
      }

      Button {
        id: retryButton
        visible: root.retryableState
        text: "Retry"
        bordered: true
        foreground: root.foreground
        fontFamily: root.fontFamily
        focusable: true
        onClicked: root.retryRequested()
      }

      Button {
        id: ejectButton
        visible: root.ejectAvailable && (root.normalizedState === "ready"
          || root.normalizedState === "sent" || root.normalizedState === "conflict")
        text: "Eject"
        foreground: root.foreground
        fontFamily: root.fontFamily
        focusable: true
        onClicked: root.ejectRequested()
      }

      Button {
        id: sendButton
        visible: root.sendAvailable && root.formatAvailable
          && (root.normalizedState === "ready" || root.normalizedState === "sent"
            || root.normalizedState === "conflict")
        text: root.replacesConflict ? "Replace"
          : (root.normalizedState === "sent" ? "Send again" : "Send")
        bordered: true
        foreground: root.foreground
        fontFamily: root.fontFamily
        focusable: true
        onClicked: root.sendRequested(root.selectedFormat, root.replacesConflict)
      }
    }
  }
}
