import QtQuick
import QtQuick.Controls as QQC
import qs.Commons
import qs.Ui

BorderSurface {
  id: root

  property var book: null
  property var capabilities: ({})
  property var descriptor: null
  property bool describing: false
  property color foreground: Color.foreground
  property color urgent: Color.urgent
  property string fontFamily: Style.font.family

  property string inputFormat: ""
  property string outputFormat: ""
  property bool advanced: false
  property var optionValues: ({})
  property string validationError: ""

  signal advancedRequested(string inputFormat, string outputFormat)
  signal conversionRequested(string inputFormat, string outputFormat, var options, bool replacesFormat)
  signal canceled()

  color: Color.background
  borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Style.normalBorderWidth)
  radius: Style.cornerRadius

  readonly property var conversion: capabilities && capabilities.conversion
    ? capabilities.conversion
    : ({ inputFormats: [], outputFormats: [], preferredInputOrder: [], defaultOutputFormat: "EPUB" })
  readonly property var inputOptions: availableInputs()
  readonly property var outputOptions: availableOutputs()
  readonly property var advancedOptions: flattenOptions()
  readonly property bool replacesFormat: hasFormat(outputFormat)

  function hasFormat(name) {
    var formats = book && book.formats instanceof Array ? book.formats : []
    for (var i = 0; i < formats.length; i++) {
      if (String(formats[i].name).toUpperCase() === String(name).toUpperCase()) return true
    }
    return false
  }

  function availableInputs() {
    var supported = conversion.inputFormats instanceof Array ? conversion.inputFormats : []
    var formats = book && book.formats instanceof Array ? book.formats : []
    var values = []
    for (var i = 0; i < formats.length; i++) {
      var name = String(formats[i].name || "").toUpperCase()
      if (name && supported.indexOf(name) >= 0) values.push(name)
    }
    return values
  }

  function availableOutputs() {
    var supported = conversion.outputFormats instanceof Array ? conversion.outputFormats : []
    var common = ["EPUB", "AZW3", "PDF", "MOBI", "DOCX", "HTMLZ", "FB2", "RTF", "TXT"]
    var values = []
    var i
    for (i = 0; i < common.length; i++) if (supported.indexOf(common[i]) >= 0) values.push(common[i])
    for (i = 0; i < supported.length; i++) if (values.indexOf(supported[i]) === -1) values.push(supported[i])
    return values
  }

  function preferredInput() {
    var order = conversion.preferredInputOrder instanceof Array ? conversion.preferredInputOrder : []
    for (var i = 0; i < order.length; i++) {
      var candidate = String(order[i]).toUpperCase()
      if (inputOptions.indexOf(candidate) >= 0) return candidate
    }
    return inputOptions.length > 0 ? inputOptions[0] : ""
  }

  function preferredOutput() {
    var wanted = String(conversion.defaultOutputFormat || "EPUB").toUpperCase()
    if (wanted !== inputFormat && outputOptions.indexOf(wanted) >= 0) return wanted
    for (var i = 0; i < outputOptions.length; i++) {
      if (outputOptions[i] !== inputFormat && !hasFormat(outputOptions[i])) return outputOptions[i]
    }
    return outputOptions.length > 0 ? outputOptions[0] : ""
  }

  function initialize() {
    inputFormat = preferredInput()
    outputFormat = preferredOutput()
    descriptor = null
    optionValues = ({})
    advanced = false
    validationError = ""
  }

  function requestAdvanced() {
    if (!inputFormat || !outputFormat || describing) return
    advancedRequested(inputFormat, outputFormat)
  }

  function flattenOptions() {
    var values = []
    var groups = descriptor && descriptor.groups instanceof Array ? descriptor.groups : []
    for (var i = 0; i < groups.length; i++) {
      var options = groups[i].options instanceof Array ? groups[i].options : []
      for (var j = 0; j < options.length; j++) {
        var option = options[j]
        if (!option || !option.name) continue
        var copy = {}
        for (var key in option) copy[key] = option[key]
        copy.group = groups[i].label || groups[i].name || "Options"
        values.push(copy)
      }
    }
    return values
  }

  function optionValue(option) {
    return optionValues[option.name] === undefined ? option.default : optionValues[option.name]
  }

  function setOption(name, value) {
    var next = {}
    for (var key in optionValues) next[key] = optionValues[key]
    next[name] = value
    optionValues = next
  }

  function changedOptions() {
    var values = {}
    for (var i = 0; i < advancedOptions.length; i++) {
      var option = advancedOptions[i]
      var value = optionValue(option)
      if (value === option.default) continue
      if (option.type === "integer") value = Number(value)
      else if (option.type === "number") value = Number(value)
      values[option.name] = value
    }
    return values
  }

  function submit() {
    if (!inputFormat) validationError = "This book has no format that Calibre can convert."
    else if (!outputFormat) validationError = "Choose an output format."
    else {
      validationError = ""
      conversionRequested(inputFormat, outputFormat, advanced ? changedOptions() : {}, replacesFormat)
    }
  }

  onBookChanged: initialize()
  Component.onCompleted: initialize()
  Keys.onEscapePressed: root.canceled()

  Column {
    anchors.fill: parent
    anchors.margins: Style.space(18)
    spacing: Style.space(12)

    Row {
      width: parent.width
      spacing: Style.space(10)

      CalibreIcon {
        id: conversionIcon
        width: Style.space(32)
        height: width
        iconSize: width
        anchors.verticalCenter: parent.verticalCenter
      }

      Column {
        width: Math.max(0, parent.width - conversionIcon.width - closeButton.width - parent.spacing * 2)
        spacing: Style.space(2)

        Text {
          width: parent.width
          text: "Convert " + (root.book ? root.book.title : "book")
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.title
          font.bold: true
          elide: Text.ElideRight
        }

        Text {
          width: parent.width
          text: "Calibre defaults are used unless you change an advanced option."
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

    Row {
      width: parent.width
      spacing: Style.space(12)

      Dropdown {
        width: (parent.width - parent.spacing) / 2
        label: "INPUT FORMAT"
        options: root.inputOptions
        value: root.inputFormat
        foreground: root.foreground
        fontFamily: root.fontFamily
        onChanged: function(value) {
          root.inputFormat = value
          root.descriptor = null
          root.optionValues = ({})
          if (root.advanced) root.requestAdvanced()
        }
      }

      Dropdown {
        width: (parent.width - parent.spacing) / 2
        label: "OUTPUT FORMAT"
        options: root.outputOptions
        value: root.outputFormat
        foreground: root.foreground
        fontFamily: root.fontFamily
        onChanged: function(value) {
          root.outputFormat = value
          root.descriptor = null
          root.optionValues = ({})
          if (root.advanced) root.requestAdvanced()
        }
      }
    }

    Text {
      visible: root.replacesFormat
      width: parent.width
      text: root.outputFormat + " already exists. Calibre will stage the conversion and ask before replacing it."
      color: root.urgent
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.WordWrap
    }

    Toggle {
      width: parent.width
      label: "Advanced options"
      description: "Format-specific controls reported by this Calibre installation"
      checked: root.advanced
      foreground: root.foreground
      fontFamily: root.fontFamily
      onClicked: {
        root.advanced = !root.advanced
        if (root.advanced && !root.descriptor) root.requestAdvanced()
      }
    }

    Item {
      visible: root.advanced
      width: parent.width
      height: root.advanced ? Math.max(0, parent.height - y - footer.height - parent.spacing) : 0

      Text {
        visible: root.describing
        anchors.centerIn: parent
        text: "Loading Calibre options…"
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
      }

      Flickable {
        anchors.fill: parent
        visible: !root.describing && root.descriptor !== null
        contentWidth: width
        contentHeight: optionsColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick

        QQC.ScrollBar.vertical: QQC.ScrollBar { policy: QQC.ScrollBar.AsNeeded }

        Column {
          id: optionsColumn
          width: parent.width
          spacing: Style.space(8)

          Repeater {
            model: root.advancedOptions

            Column {
              id: optionRow
              required property var modelData
              width: optionsColumn.width
              spacing: Style.space(3)

              Text {
                width: parent.width
                text: optionRow.modelData.group.toUpperCase() + "  /  " + optionRow.modelData.label.toUpperCase()
                color: Qt.darker(root.foreground, 1.4)
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
                elide: Text.ElideRight
              }

              Loader {
                width: parent.width
                sourceComponent: optionRow.modelData.type === "boolean"
                  ? booleanOption
                  : (optionRow.modelData.type === "choice" ? choiceOption : textOption)
              }

              Text {
                visible: optionRow.modelData.help !== ""
                width: parent.width
                text: optionRow.modelData.help
                color: Qt.darker(root.foreground, 1.65)
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                wrapMode: Text.WordWrap
                maximumLineCount: 2
                elide: Text.ElideRight
              }

              Component {
                id: booleanOption
                ToggleSwitch {
                  checked: root.optionValue(optionRow.modelData) === true
                  foreground: root.foreground
                  onToggled: root.setOption(optionRow.modelData.name, !checked)
                }
              }

              Component {
                id: choiceOption
                Dropdown {
                  width: optionRow.width
                  showLabel: false
                  options: optionRow.modelData.choices || []
                  value: String(root.optionValue(optionRow.modelData))
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  onChanged: function(value) { root.setOption(optionRow.modelData.name, value) }
                }
              }

              Component {
                id: textOption
                TextField {
                  width: optionRow.width
                  text: String(root.optionValue(optionRow.modelData) === null ? "" : root.optionValue(optionRow.modelData))
                  foreground: root.foreground
                  onEditingFinished: root.setOption(optionRow.modelData.name, text)
                }
              }
            }
          }
        }
      }
    }

    Item {
      visible: !root.advanced
      width: parent.width
      height: Math.max(0, parent.height - y - footer.height - parent.spacing)

      Column {
        anchors.centerIn: parent
        spacing: Style.space(8)

        Text {
          anchors.horizontalCenter: parent.horizontalCenter
          text: root.inputFormat + "  →  " + root.outputFormat
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.display
          font.bold: true
        }

        Text {
          anchors.horizontalCenter: parent.horizontalCenter
          text: "Quick conversion"
          color: Qt.darker(root.foreground, 1.45)
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }
      }
    }

    Row {
      id: footer
      width: parent.width
      spacing: Style.space(8)

      Text {
        width: Math.max(0, parent.width - cancelButton.width - convertButton.width - parent.spacing * 2)
        text: root.validationError
        color: root.urgent
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
      }

      Button {
        id: cancelButton
        text: "Cancel"
        foreground: root.foreground
        fontFamily: root.fontFamily
        onClicked: root.canceled()
      }

      Button {
        id: convertButton
        text: root.replacesFormat ? "Review replacement" : "Convert"
        bordered: true
        foreground: root.foreground
        fontFamily: root.fontFamily
        onClicked: root.submit()
      }
    }
  }
}
