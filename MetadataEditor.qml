import QtQuick
import QtQuick.Controls as QQC
import qs.Commons
import qs.Ui

BorderSurface {
  id: root

  property var book: null
  property color foreground: Color.foreground
  property color urgent: Color.urgent
  property string fontFamily: Style.font.family
  property string validationError: ""
  property bool downloadAvailable: false

  signal saved(var fields)
  signal coverRequested()
  signal downloadRequested()
  signal canceled()

  color: Color.background
  borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Style.normalBorderWidth)
  radius: Style.cornerRadius
  focus: visible

  Keys.onEscapePressed: root.canceled()

  function split(value, separator) {
    var raw = String(value || "").split(separator)
    var values = []
    for (var i = 0; i < raw.length; i++) {
      var item = raw[i].trim()
      if (item) values.push(item)
    }
    return values
  }

  function parseIdentifiers(value) {
    var identifiers = {}
    var parts = split(value, ",")
    for (var i = 0; i < parts.length; i++) {
      var separator = parts[i].indexOf(":")
      if (separator < 1 || separator === parts[i].length - 1) return null
      var key = parts[i].slice(0, separator).trim()
      var item = parts[i].slice(separator + 1).trim()
      if (!/^[A-Za-z0-9_-]+$/.test(key)) return null
      identifiers[key] = item
    }
    return identifiers
  }

  function identifiersText(value) {
    var parts = []
    var source = value || {}
    var keys = Object.keys(source).sort()
    for (var i = 0; i < keys.length; i++) parts.push(keys[i] + ":" + source[keys[i]])
    return parts.join(", ")
  }

  function loadBook() {
    var value = book || {}
    titleField.text = value.title || ""
    authorsField.text = value.authors instanceof Array ? value.authors.join(" & ") : ""
    tagsField.text = value.tags instanceof Array ? value.tags.join(", ") : ""
    seriesField.text = value.series || ""
    seriesIndexField.text = value.seriesIndex === undefined ? "1" : String(value.seriesIndex)
    ratingField.text = value.rating === undefined ? "0" : String(value.rating)
    publisherField.text = value.publisher || ""
    publishedField.text = String(value.published || "").slice(0, 10)
    languagesField.text = value.languages instanceof Array ? value.languages.join(", ") : ""
    identifiersField.text = identifiersText(value.identifiers)
    commentsField.text = value.comments || ""
    validationError = ""
  }

  function submit() {
    var title = titleField.text.trim()
    var authors = split(authorsField.text, "&")
    var seriesIndex = Number(seriesIndexField.text)
    var rating = Number(ratingField.text)
    var identifiers = parseIdentifiers(identifiersField.text)

    if (!title) validationError = "Title is required."
    else if (authors.length === 0) validationError = "Add at least one author."
    else if (!isFinite(seriesIndex) || seriesIndex < 0) validationError = "Series index must be zero or greater."
    else if (!isFinite(rating) || rating < 0 || rating > 5) validationError = "Rating must be between 0 and 5."
    else if (identifiers === null) validationError = "Use identifier:value pairs separated by commas."
    else {
      validationError = ""
      saved({
        title: title,
        authors: authors,
        tags: split(tagsField.text, ","),
        series: seriesField.text.trim(),
        seriesIndex: seriesIndex,
        rating: rating,
        publisher: publisherField.text.trim(),
        published: publishedField.text.trim(),
        languages: split(languagesField.text, ","),
        identifiers: identifiers,
        comments: commentsField.text
      })
    }
  }

  onBookChanged: loadBook()
  onVisibleChanged: if (visible) Qt.callLater(function() { root.forceActiveFocus() })
  Component.onCompleted: loadBook()

  Column {
    anchors.fill: parent
    anchors.margins: Style.space(18)
    spacing: Style.space(12)

    Row {
      width: parent.width
      spacing: Style.space(12)

      CalibreIcon {
        id: metadataIcon
        width: Style.space(30)
        height: width
        iconSize: width
        anchors.verticalCenter: parent.verticalCenter
      }

      Text {
        width: Math.max(0, parent.width - metadataIcon.width - coverButton.width
          - (root.downloadAvailable ? downloadButton.width : 0)
          - parent.spacing * (root.downloadAvailable ? 3 : 2))
        text: "Edit metadata"
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.title
        font.bold: true
      }

      Button {
        id: downloadButton
        visible: root.downloadAvailable
        text: "Fetch metadata"
        foreground: root.foreground
        fontFamily: root.fontFamily
        focusable: true
        onClicked: root.downloadRequested()
      }

      Button {
        id: coverButton
        text: root.book && root.book.cover ? "Change cover" : "Add cover"
        bordered: true
        foreground: root.foreground
        fontFamily: root.fontFamily
        focusable: true
        onClicked: root.coverRequested()
      }
    }

    PanelSeparator { width: parent.width }

    Flickable {
      width: parent.width
      height: Math.max(0, parent.height - y - footer.height - parent.spacing)
      contentWidth: width
      contentHeight: form.implicitHeight
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      flickableDirection: Flickable.VerticalFlick

      QQC.ScrollBar.vertical: QQC.ScrollBar { policy: QQC.ScrollBar.AsNeeded }

      Column {
        id: form
        width: parent.width
        spacing: Style.space(12)

        Grid {
          width: parent.width
          columns: 2
          columnSpacing: Style.space(12)
          rowSpacing: Style.space(10)

          EditorField { id: titleField; label: "Title"; fieldWidth: (form.width - Style.space(12)) / 2; foreground: root.foreground }
          EditorField { id: authorsField; label: "Authors, separated by &"; fieldWidth: (form.width - Style.space(12)) / 2; foreground: root.foreground }
          EditorField { id: seriesField; label: "Series"; fieldWidth: (form.width - Style.space(12)) / 2; foreground: root.foreground }
          EditorField { id: seriesIndexField; label: "Series index"; fieldWidth: (form.width - Style.space(12)) / 2; foreground: root.foreground }
          EditorField { id: tagsField; label: "Tags, comma separated"; fieldWidth: (form.width - Style.space(12)) / 2; foreground: root.foreground }
          EditorField { id: ratingField; label: "Rating, 0 to 5"; fieldWidth: (form.width - Style.space(12)) / 2; foreground: root.foreground }
          EditorField { id: publisherField; label: "Publisher"; fieldWidth: (form.width - Style.space(12)) / 2; foreground: root.foreground }
          EditorField { id: publishedField; label: "Published, YYYY-MM-DD"; fieldWidth: (form.width - Style.space(12)) / 2; foreground: root.foreground }
          EditorField { id: languagesField; label: "Languages, ISO codes"; fieldWidth: (form.width - Style.space(12)) / 2; foreground: root.foreground }
          EditorField { id: identifiersField; label: "Identifiers"; fieldWidth: (form.width - Style.space(12)) / 2; foreground: root.foreground }
        }

        Text {
          text: "COMMENTS"
          color: Qt.darker(root.foreground, 1.4)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          font.bold: true
        }

        QQC.TextArea {
          id: commentsField
          width: parent.width
          height: Style.space(110)
          color: root.foreground
          selectionColor: Style.selectionFillFor(root.foreground, Color.accent)
          selectedTextColor: root.foreground
          placeholderText: "Description or notes"
          placeholderTextColor: Qt.darker(root.foreground, 1.6)
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          wrapMode: TextEdit.Wrap
          leftPadding: Style.spacing.controlPaddingX
          rightPadding: Style.spacing.controlPaddingX
          topPadding: Style.spacing.inputPaddingY
          bottomPadding: Style.spacing.inputPaddingY
          background: BorderSurface {
            color: Style.controlFill(commentsField.activeFocus, commentsField.hovered, root.foreground, Color.accent)
            borderSpec: Border.controlSpec(commentsField.activeFocus ? "focus" : "normal", root.foreground, Color.accent)
            radius: Style.cornerRadius
          }
        }
      }
    }

    Row {
      id: footer
      width: parent.width
      spacing: Style.space(8)

      Text {
        width: Math.max(0, parent.width - cancelButton.width - saveButton.width - parent.spacing * 2)
        text: root.validationError
        color: root.urgent
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        anchors.verticalCenter: parent.verticalCenter
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
        id: saveButton
        text: "Save"
        bordered: true
        foreground: root.foreground
        fontFamily: root.fontFamily
        onClicked: root.submit()
      }
    }
  }
}
