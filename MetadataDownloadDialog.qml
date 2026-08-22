import QtQuick
import QtQuick.Controls as QQC
import qs.Commons
import qs.Ui

BorderSurface {
  id: root

  property var book: null
  property var preview: null
  property bool loading: false
  property bool applying: false
  property string error: ""
  property color foreground: Color.foreground
  property color urgent: Color.urgent
  property string fontFamily: Style.font.family

  property var fieldSelection: ({})
  property bool coverSelection: false

  signal fetchRequested()
  signal applyRequested(var selectedFields, bool includeCover)
  signal retryRequested()
  signal cancelJobRequested()
  signal discarded()
  signal canceled()

  color: Color.background
  borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Style.normalBorderWidth)
  radius: Style.cornerRadius
  focus: visible

  readonly property var changedFields: normalizedFields()
  readonly property var coverInfo: normalizedCover()
  readonly property bool hasPreview: preview !== null && preview !== undefined
  readonly property bool hasReview: changedFields.length > 0 || coverInfo !== null
  readonly property bool noResult: hasPreview && !hasReview && !error && !loading && !applying

  onPreviewChanged: resetSelection()
  onVisibleChanged: if (visible) Qt.callLater(function() { root.forceActiveFocus() })
  Keys.priority: Keys.BeforeItem
  Keys.onPressed: function(event) {
    if (event.key === Qt.Key_Escape) {
      root.dismiss()
      event.accepted = true
    }
  }

  function canonicalFieldKey(value) {
    var key = String(value || "").trim().toLowerCase()
    var aliases = {
      author: "authors",
      authors: "authors",
      pubdate: "published",
      publicationdate: "published",
      series_index: "seriesIndex",
      seriesindex: "seriesIndex",
      language: "languages",
      languages: "languages",
      identifier: "identifiers",
      identifiers: "identifiers"
    }
    if (aliases[key] !== undefined) return aliases[key]

    var allowed = [
      "title", "authors", "tags", "series", "seriesIndex", "publisher",
      "published", "languages", "identifiers", "comments", "rating"
    ]
    return allowed.indexOf(key) >= 0 ? key : ""
  }

  function fieldLabel(key) {
    var labels = {
      title: "Title",
      authors: "Authors",
      tags: "Tags",
      series: "Series",
      seriesIndex: "Series index",
      publisher: "Publisher",
      published: "Published",
      languages: "Languages",
      identifiers: "Identifiers",
      comments: "Description",
      rating: "Rating"
    }
    return labels[key] || key
  }

  function displayValue(value) {
    if (value === undefined || value === null || value === "") return "Not set"
    if (value instanceof Array) return value.length > 0 ? value.join(", ") : "Not set"
    if (typeof value === "object") {
      var parts = []
      var keys = Object.keys(value).sort()
      for (var i = 0; i < keys.length; i++) parts.push(keys[i] + ":" + value[keys[i]])
      return parts.length > 0 ? parts.join(", ") : "Not set"
    }
    return String(value)
  }

  function localImageSource(value) {
    var text = String(value || "")
    return text.charAt(0) === "/" ? encodeURI("file://" + text) : text
  }

  function valuesEqual(left, right) {
    try {
      return JSON.stringify(left) === JSON.stringify(right)
    } catch (errorValue) {
      return String(left) === String(right)
    }
  }

  function rawFieldEntries() {
    var source = preview || {}
    var raw = source.fields
    if (raw === undefined) raw = source.changes
    if (raw instanceof Array) return raw
    if (!raw || typeof raw !== "object") return []

    var values = []
    var keys = Object.keys(raw)
    for (var i = 0; i < keys.length; i++) {
      var value = raw[keys[i]]
      if (value && typeof value === "object" && !(value instanceof Array)) {
        var copy = {}
        for (var key in value) copy[key] = value[key]
        if (copy.key === undefined && copy.field === undefined && copy.name === undefined) copy.key = keys[i]
        values.push(copy)
      } else {
        values.push({ key: keys[i], proposed: value })
      }
    }
    return values
  }

  function normalizedFields() {
    var entries = rawFieldEntries()
    var values = []
    var seen = {}

    for (var i = 0; i < entries.length; i++) {
      var entry = entries[i]
      if (!entry || typeof entry !== "object") continue

      var key = canonicalFieldKey(entry.key || entry.field || entry.name)
      if (!key || seen[key]) continue

      var hasProposed = entry.proposed !== undefined
        || entry.value !== undefined
        || entry.newValue !== undefined
      if (!hasProposed) continue

      var proposed = entry.proposed !== undefined ? entry.proposed
        : (entry.value !== undefined ? entry.value : entry.newValue)
      var hasCurrent = entry.current !== undefined || entry.oldValue !== undefined
      var current = entry.current !== undefined ? entry.current : entry.oldValue
      var changed = entry.changed === undefined
        ? (!hasCurrent || !valuesEqual(current, proposed))
        : !!entry.changed
      if (!changed) continue

      seen[key] = true
      values.push({
        key: key,
        label: fieldLabel(key),
        currentText: hasCurrent ? displayValue(current) : "Not set",
        proposed: proposed,
        proposedText: displayValue(proposed),
        selected: entry.selected === undefined ? true : !!entry.selected
      })
    }
    return values
  }

  function normalizedCover() {
    var source = preview || {}
    var raw = source.cover !== undefined ? source.cover : source.coverImage
    if (!raw && source.coverAvailable) {
      raw = { available: true, source: source.coverPath || source.coverUrl || "" }
    }
    if (!raw || raw.available === false || raw.changed === false) return null

    if (typeof raw === "string") return { source: localImageSource(raw), label: "Cover", description: "New cover" }

    var imageSource = raw.source || raw.url || raw.path || raw.data || ""
    return {
      source: localImageSource(imageSource),
      label: raw.label || "Cover",
      description: raw.description || "New cover",
      name: raw.name || ""
    }
  }

  function resetSelection() {
    var next = {}
    var fields = normalizedFields()
    for (var i = 0; i < fields.length; i++) next[fields[i].key] = fields[i].selected
    fieldSelection = next
    coverSelection = normalizedCover() !== null
  }

  function isFieldSelected(key) {
    return fieldSelection && fieldSelection[key] === true
  }

  function toggleField(key) {
    var next = {}
    for (var name in fieldSelection) next[name] = fieldSelection[name]
    next[key] = !isFieldSelected(key)
    fieldSelection = next
  }

  function selectedCount() {
    var count = 0
    for (var i = 0; i < changedFields.length; i++) {
      if (isFieldSelected(changedFields[i].key)) count++
    }
    return count
  }

  function selectedValues() {
    var values = {}
    for (var i = 0; i < changedFields.length; i++) {
      var field = changedFields[i]
      if (isFieldSelected(field.key)) values[field.key] = field.proposed
    }
    return values
  }

  function submit() {
    if (loading || applying || (selectedCount() === 0 && !coverSelection)) return
    applyRequested(selectedValues(), coverSelection && coverInfo !== null)
  }

  function dismiss() {
    if (loading || applying) {
      cancelJobRequested()
      canceled()
      return
    }
    if (hasPreview) discarded()
    canceled()
  }

  function revealReviewItem(item) {
    if (!item || !bodyScroll || !body) return
    Qt.callLater(function() {
      var point = item.mapToItem(body, 0, 0)
      var margin = Style.space(8)
      var top = point.y
      var bottom = top + item.height
      var maximum = Math.max(0, bodyScroll.contentHeight - bodyScroll.height)
      if (top < bodyScroll.contentY + margin)
        bodyScroll.contentY = Math.max(0, top - margin)
      else if (bottom > bodyScroll.contentY + bodyScroll.height - margin)
        bodyScroll.contentY = Math.min(maximum, bottom + margin - bodyScroll.height)
    })
  }

  Component.onCompleted: resetSelection()

  Column {
    anchors.fill: parent
    anchors.margins: Style.space(18)
    spacing: Style.space(12)

    Row {
      id: header
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
          text: root.loading ? "Fetching metadata"
            : (root.applying ? "Applying metadata" : (root.hasReview ? "Review metadata" : "Fetch metadata"))
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.title
          font.bold: true
          elide: Text.ElideRight
        }

        Text {
          textFormat: Text.PlainText
          width: parent.width
          text: root.loading ? "Calibre is looking up common book fields."
            : (root.applying ? "Saving only the fields you selected."
              : (root.hasReview ? "Choose the changes to add to this book."
                : "Use Calibre to look up common metadata."))
          color: Qt.darker(root.foreground, 1.45)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      CalibreActionButton {
        id: closeButton
        iconText: "󰅖"
        tooltipText: root.loading || root.applying ? "Cancel" : "Close"
        foreground: root.foreground
        fontFamily: root.fontFamily
        focusable: true
        enabled: !root.applying
        onClicked: root.dismiss()
      }
    }

    PanelSeparator { width: parent.width }

    Flickable {
      id: bodyScroll
      width: parent.width
      height: Math.max(0, parent.height - y - footer.implicitHeight - parent.spacing)
      contentWidth: width
      contentHeight: body.implicitHeight
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      flickableDirection: Flickable.VerticalFlick

      QQC.ScrollBar.vertical: QQC.ScrollBar { policy: QQC.ScrollBar.AsNeeded }

      Column {
        id: body
        width: bodyScroll.width
        spacing: Style.space(12)

        Column {
          id: fetchState
          visible: !root.loading && !root.applying && !root.error && !root.hasPreview
          width: parent.width
          height: visible ? implicitHeight : 0
          spacing: Style.space(8)

          Text {
            textFormat: Text.PlainText
            width: parent.width
            text: root.book ? root.bookSummary() : "Select a book to fetch metadata."
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            wrapMode: Text.WordWrap
          }

          Text {
            textFormat: Text.PlainText
            width: parent.width
            text: "Calibre will return a short review list. Existing fields stay unchanged until you apply a choice."
            color: Qt.darker(root.foreground, 1.45)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          CalibreButton {
            text: "Fetch metadata"
            bordered: true
            focusable: true
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: root.fetchRequested()
          }
        }

        Column {
          id: loadingState
          visible: root.loading
          width: parent.width
          height: visible ? implicitHeight : 0
          spacing: Style.space(8)

          Text {
            textFormat: Text.PlainText
            width: parent.width
            text: "Searching for common metadata…"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          Text {
            textFormat: Text.PlainText
            width: parent.width
            text: "This can take a moment."
            color: Qt.darker(root.foreground, 1.45)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          CalibreButton {
            text: "Cancel"
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: { root.cancelJobRequested(); root.canceled() }
          }
        }

        Column {
          id: errorState
          visible: !!root.error && !root.loading && !root.applying
          width: parent.width
          height: visible ? implicitHeight : 0
          spacing: Style.space(8)

          Text {
            textFormat: Text.PlainText
            width: parent.width
            text: root.error
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            wrapMode: Text.WordWrap
          }

          Row {
            spacing: Style.space(8)

            CalibreButton {
              text: "Retry"
              bordered: true
              focusable: true
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.retryRequested()
            }

            CalibreButton {
              text: "Close"
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.canceled()
            }
          }
        }

        Column {
          id: noResultState
          visible: root.noResult
          width: parent.width
          height: visible ? implicitHeight : 0
          spacing: Style.space(8)

          Text {
            textFormat: Text.PlainText
            width: parent.width
            text: root.preview && root.preview.message
              ? root.preview.message
              : "Calibre found no new common metadata."
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            wrapMode: Text.WordWrap
          }

          CalibreButton {
            text: "Close"
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: root.dismiss()
          }
        }

        Column {
          id: reviewState
          visible: root.hasReview && !root.loading && !root.applying && !root.error
          width: parent.width
          height: visible ? implicitHeight : 0
          spacing: Style.space(8)

          Text {
            textFormat: Text.PlainText
            width: parent.width
            text: "Only selected changes will be applied. Untouched metadata stays as it is."
            color: Qt.darker(root.foreground, 1.35)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          Repeater {
            model: root.changedFields

            delegate: BorderSurface {
              id: fieldRow
              required property var modelData

              width: reviewState.width
              height: Math.max(Style.space(58), fieldContent.implicitHeight + Style.space(12))
              radius: Style.cornerRadius
              activeFocusOnTab: true

              readonly property bool hot: fieldMouse.containsMouse
              readonly property bool checked: root.isFieldSelected(modelData.key)

              Accessible.role: Accessible.CheckBox
              Accessible.name: fieldRow.modelData.label
              Accessible.description: "New " + fieldRow.modelData.proposedText
                + "; current " + fieldRow.modelData.currentText
              Accessible.checked: fieldRow.checked
              Accessible.onPressAction: root.toggleField(fieldRow.modelData.key)

              color: Style.controlFill(activeFocus, hot, root.foreground, Color.accent)
              borderSpec: Border.controlSpec(activeFocus ? "focus" : (hot ? "hover-cursor" : "normal"), root.foreground, Color.accent)
              onActiveFocusChanged: if (activeFocus) root.revealReviewItem(fieldRow)

              Keys.onPressed: function(event) {
                if (event.key === Qt.Key_Space || event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                  root.toggleField(fieldRow.modelData.key)
                  event.accepted = true
                }
              }

              MouseArea {
                id: fieldMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                  fieldRow.forceActiveFocus()
                  root.toggleField(fieldRow.modelData.key)
                }
              }

              Row {
                id: fieldContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.margins: Style.space(8)
                spacing: Style.space(10)

                BorderSurface {
                  width: Style.space(22)
                  height: width
                  radius: Style.cornerRadius
                  color: fieldRow.checked ? Style.selectedFillFor(root.foreground, Color.accent) : "transparent"
                  borderSpec: Border.controlSpec(fieldRow.checked ? "selected" : "normal", root.foreground, Color.accent)
                  anchors.verticalCenter: parent.verticalCenter

                  Text {
                    textFormat: Text.PlainText
                    anchors.centerIn: parent
                    text: fieldRow.checked ? "✓" : ""
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    font.bold: true
                  }
                }

                Column {
                  width: Math.max(0, parent.width - Style.space(32))
                  spacing: Style.space(2)

                  Text {
                    textFormat: Text.PlainText
                    width: parent.width
                    text: fieldRow.modelData.label
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    font.bold: true
                    elide: Text.ElideRight
                  }

                  Text {
                    textFormat: Text.PlainText
                    width: parent.width
                    text: "New  " + fieldRow.modelData.proposedText
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    elide: Text.ElideRight
                  }

                  Text {
                    textFormat: Text.PlainText
                    width: parent.width
                    text: "Current  " + fieldRow.modelData.currentText
                    color: Qt.darker(root.foreground, 1.55)
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    elide: Text.ElideRight
                  }
                }
              }
            }
          }

          BorderSurface {
            id: coverRow
            visible: root.coverInfo !== null
            width: reviewState.width
            height: visible ? Math.max(Style.space(76), coverContent.implicitHeight + Style.space(12)) : 0
            radius: Style.cornerRadius
            activeFocusOnTab: true

            readonly property bool hot: coverMouse.containsMouse
            Accessible.role: Accessible.CheckBox
            Accessible.name: "Downloaded cover"
            Accessible.description: root.coverInfo ? root.coverInfo.description : "New cover"
            Accessible.checked: root.coverSelection
            Accessible.onPressAction: root.coverSelection = !root.coverSelection
            color: Style.controlFill(activeFocus, hot, root.foreground, Color.accent)
            borderSpec: Border.controlSpec(activeFocus ? "focus" : (hot ? "hover-cursor" : "normal"), root.foreground, Color.accent)
            onActiveFocusChanged: if (activeFocus) root.revealReviewItem(coverRow)

            Keys.onPressed: function(event) {
              if (event.key === Qt.Key_Space || event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                root.coverSelection = !root.coverSelection
                event.accepted = true
              }
            }

            MouseArea {
              id: coverMouse
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: {
                coverRow.forceActiveFocus()
                root.coverSelection = !root.coverSelection
              }
            }

            Row {
              id: coverContent
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.margins: Style.space(8)
              spacing: Style.space(10)

              BorderSurface {
                width: Style.space(22)
                height: width
                radius: Style.cornerRadius
                color: root.coverSelection ? Style.selectedFillFor(root.foreground, Color.accent) : "transparent"
                borderSpec: Border.controlSpec(root.coverSelection ? "selected" : "normal", root.foreground, Color.accent)
                anchors.verticalCenter: parent.verticalCenter

                Text {
                  textFormat: Text.PlainText
                  anchors.centerIn: parent
                  text: root.coverSelection ? "✓" : ""
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                }
              }

              BorderSurface {
                width: Style.space(48)
                height: Style.space(60)
                color: "transparent"
                borderSpec: Border.flat("transparent", 0)
                anchors.verticalCenter: parent.verticalCenter

                Image {
                  anchors.fill: parent
                  visible: root.coverInfo !== null && root.coverInfo.source !== ""
                  source: root.coverInfo ? root.coverInfo.source : ""
                  sourceSize.width: width * 2
                  sourceSize.height: height * 2
                  fillMode: Image.PreserveAspectFit
                  asynchronous: true
                  cache: true
                }

                Text {
                  textFormat: Text.PlainText
                  anchors.centerIn: parent
                  visible: root.coverInfo !== null && root.coverInfo.source === ""
                  text: "COVER"
                  color: Qt.darker(root.foreground, 1.45)
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  font.bold: true
                }
              }

              Column {
                width: Math.max(0, parent.width - Style.space(90))
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(2)

                Text {
                  textFormat: Text.PlainText
                  width: parent.width
                  text: root.coverInfo ? root.coverInfo.label : "Cover"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                  elide: Text.ElideRight
                }

                Text {
                  textFormat: Text.PlainText
                  width: parent.width
                  text: root.coverInfo ? root.coverInfo.description : "New cover"
                  color: Qt.darker(root.foreground, 1.45)
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }
              }
            }
          }
        }

        Column {
          id: applyingState
          visible: root.applying
          width: parent.width
          height: visible ? implicitHeight : 0
          spacing: Style.space(8)

          Text {
            textFormat: Text.PlainText
            width: parent.width
            text: "Saving your selected changes…"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          Text {
            textFormat: Text.PlainText
            width: parent.width
            text: "Calibre is updating the book record."
            color: Qt.darker(root.foreground, 1.45)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          CalibreButton {
            text: "Cancel"
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: { root.cancelJobRequested(); root.canceled() }
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
        width: Math.max(0, parent.width - applyButton.width - cancelButton.width - parent.spacing * 2)
        text: root.hasReview && !root.loading && !root.applying && !root.error
          ? (root.selectedCount() + (root.coverSelection ? 1 : 0)) + " selected"
          : ""
        color: Qt.darker(root.foreground, 1.45)
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        anchors.verticalCenter: parent.verticalCenter
        elide: Text.ElideRight
      }

      CalibreButton {
        id: cancelButton
        text: root.loading || root.applying ? "Cancel" : "Close"
        foreground: root.foreground
        fontFamily: root.fontFamily
        enabled: !root.applying || root.loading
        onClicked: root.dismiss()
      }

      CalibreButton {
        id: applyButton
        visible: root.hasReview && !root.loading && !root.applying && !root.error
        text: "Apply selected"
        bordered: true
        focusable: true
        enabled: root.selectedCount() > 0 || root.coverSelection
        foreground: root.foreground
        fontFamily: root.fontFamily
        onClicked: root.submit()
      }
    }
  }

  function bookSummary() {
    var value = book || {}
    var title = String(value.title || "Untitled book")
    var authors = value.authors instanceof Array ? value.authors.join(", ") : ""
    return authors ? title + "\n" + authors : title
  }
}
