import QtQuick
import QtQuick.Controls as QQC
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root
  moduleName: "io.github.whitehades.calibre"
  ipcTarget: "io.github.whitehades.calibre"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  property var viewState: Model.initialState()
  property var requestKinds: ({})
  property var requestContexts: ({})
  property string lastError: ""
  property bool lastMessageIsError: false
  property string focusSection: "books"
  property int bookIndex: 0
  property int actionIndex: 0
  property bool cursorActive: false
  property bool bootstrapped: false
  property string dialogMode: ""
  property string sortField: "title"
  property string sortDirection: "ascending"
  property string filterQuery: ""
  property var confirmation: null

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.48)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property var selectedBook: viewState.selectedBook
  readonly property var setup: Model.setupContent(viewState)
  readonly property var jobs: Model.jobList(viewState)
  readonly property int activeJobCount: Model.activeJobCount(viewState)
  readonly property var primaryActions: {
    var actions = [
      { id: "open", label: "Open book", key: "o" },
      { id: "metadata", label: "Edit metadata", key: "e" }
    ]
    if (hasCapability("book.convert.quick")) actions.push({ id: "convert", label: "Convert", key: "c" })
    actions.push({ id: "export", label: "Export", key: "s" })
    return actions
  }
  readonly property var secondaryActions: [
    { id: "formats", label: "Manage formats", key: "f" },
    { id: "remove", label: "Remove from library", key: "del" }
  ]

  function open() {
    root.controller.show()
    if (!bootstrapped) bootstrap()
  }

  function close() {
    root.controller.hide()
  }

  function toggle() {
    if (opened) close()
    else open()
  }

  function switchPanel(direction) {
    if (bar && typeof bar.switchPanelFrom === "function")
      return bar.switchPanelFrom(barIdentity, direction)
    return false
  }

  function rememberRequest(id, kind, context) {
    var kinds = {}
    var contexts = {}
    var key
    for (key in requestKinds) kinds[key] = requestKinds[key]
    for (key in requestContexts) contexts[key] = requestContexts[key]
    kinds[id] = kind
    contexts[id] = context || ({})
    requestKinds = kinds
    requestContexts = contexts
  }

  function forgetRequest(id) {
    var kinds = {}
    var contexts = {}
    var key
    for (key in requestKinds) if (key !== id) kinds[key] = requestKinds[key]
    for (key in requestContexts) if (key !== id) contexts[key] = requestContexts[key]
    requestKinds = kinds
    requestContexts = contexts
  }

  function submit(operation, inputData, kind, context) {
    var id = bridge.submit(operation, viewState.currentLibrary, inputData)
    rememberRequest(id, kind || operation, context)
    viewState = Model.beginRequest(viewState, id, kind || operation)
    return id
  }

  function setStatus(message, isError) {
    lastError = String(message || "")
    lastMessageIsError = isError === true
  }

  function hasCapability(name) {
    var actions = viewState.capabilities && viewState.capabilities.actions instanceof Array
      ? viewState.capabilities.actions
      : []
    return actions.indexOf(name) >= 0
  }

  function rememberedLibraries() {
    var remembered = setting("rememberedLibraries", [])
    return remembered instanceof Array ? remembered : []
  }

  function bootstrap(extraLibrary) {
    setStatus("", false)
    var libraries = rememberedLibraries().slice()
    if (extraLibrary && libraries.indexOf(extraLibrary) === -1) libraries.unshift(extraLibrary)
    var id = bridge.submit("bootstrap", "", {
      rememberedLibraries: libraries,
      pageSize: Number(setting("pageSize", 50))
    })
    rememberRequest(id, "bootstrap")
  }

  function refresh() {
    bootstrap("")
  }

  function persistLibraries(libraries) {
    var paths = []
    for (var i = 0; i < libraries.length; i++) {
      var path = libraries[i] && libraries[i].path ? String(libraries[i].path) : ""
      if (path && paths.indexOf(path) === -1) paths.push(path)
    }
    var current = rememberedLibraries()
    if (JSON.stringify(current) === JSON.stringify(paths)) return

    var entry = { id: root.moduleName }
    for (var key in root.settings) if (key !== "id") entry[key] = root.settings[key]
    entry.rememberedLibraries = paths
    root.settings = entry
    if (root.hostWidget && "settings" in root.hostWidget) root.hostWidget.settings = entry
    if (root.bar && root.bar.shell && typeof root.bar.shell.updateEntryInline === "function")
      root.bar.shell.updateEntryInline(root.moduleName, entry)
  }

  function handleBridgeMessage(event) {
    if (!event || !event.id) return
    var kind = requestKinds[event.id] || ""
    var context = requestContexts[event.id] || ({})
    viewState = Model.applyBridgeEvent(viewState, event)
    if (event.type === "accepted" || event.type === "progress") return

    if (event.type === "cancelled") {
      setStatus("Calibre operation cancelled.", false)
      forgetRequest(event.id)
      return
    }

    if (kind === "discard-confirmation") {
      forgetRequest(event.id)
      return
    }

    if (event.type === "failed") {
      var errorCode = event.error && event.error.code ? String(event.error.code) : ""
      if (kind === "export" && errorCode === "confirmation_required") {
        forgetRequest(event.id)
        submit("action.prepare", {
          name: "book.export.replace",
          bookIds: context.bookIds,
          destination: context.destination
        }, "prepare-export")
        return
      }
      if (kind === "conversion-describe") conversionDialog.describing = false
      setStatus(event.error && event.error.message
        ? String(event.error.message)
        : "The Calibre operation failed.", true)
      forgetRequest(event.id)
      return
    }

    if (event.type !== "succeeded") return
    var result = event.result || {}
    if (kind === "bootstrap") {
      viewState = Model.applyBootstrap(viewState, result)
      bootstrapped = true
      persistLibraries(result.libraries || [])
      bookIndex = 0
    } else if (kind === "query" || kind === "query-append") {
      viewState = Model.applyQuery(viewState, result, kind === "query-append")
      if (kind === "query") bookIndex = 0
    } else if (kind === "conversion-describe") {
      conversionDialog.descriptor = result
      conversionDialog.describing = false
    } else if (kind.indexOf("prepare-") === 0) {
      presentConfirmation(kind, result)
    } else if (result.book) {
      viewState = Model.applyBook(viewState, result.book)
      if (kind === "metadata") setStatus("Metadata saved.", false)
      else if (kind === "cover") setStatus("Cover saved.", false)
      else if (kind === "format-add") setStatus("Format added.", false)
      else if (kind === "commit-format-replace") setStatus("Format replaced.", false)
      else if (kind === "commit-format-remove") setStatus("Format removed.", false)
      else if (kind === "convert" || kind === "commit-convert")
        setStatus("Created " + String(result.outputFormat || "the requested") + " format.", false)
      if (kind === "format-add" || kind.indexOf("commit-format-") === 0) dialogMode = "formats"
    } else if (kind === "import") {
      var added = result.addedIds instanceof Array ? result.addedIds.length : 0
      setStatus(added > 0 ? added + (added === 1 ? " book added." : " books added.") : "No new books were added.", false)
      search()
    } else if (kind === "export" || kind === "commit-export") {
      setStatus("Exported " + String(result.files ? result.files.length : 0) + " files.", false)
    } else if (kind === "commit-remove") {
      var removed = result.removedIds instanceof Array ? result.removedIds.length : 0
      setStatus("Removed " + removed + (removed === 1 ? " book." : " books."), false)
      search()
    }
    if (kind === "bootstrap" || kind === "query" || kind === "query-append")
      viewState = Model.forgetJob(viewState, event.id)
    forgetRequest(event.id)
  }

  function presentConfirmation(kind, result) {
    var options = {
      "prepare-remove": {
        title: "Remove from library",
        confirmLabel: "Remove",
        destructive: true,
        commitKind: "commit-remove",
        returnMode: ""
      },
      "prepare-format-replace": {
        title: "Replace format",
        confirmLabel: "Replace",
        destructive: true,
        commitKind: "commit-format-replace",
        returnMode: "formats"
      },
      "prepare-format-remove": {
        title: "Remove format",
        confirmLabel: "Remove",
        destructive: true,
        commitKind: "commit-format-remove",
        returnMode: "formats"
      },
      "prepare-convert": {
        title: "Replace converted format",
        confirmLabel: "Replace",
        destructive: true,
        commitKind: "commit-convert",
        returnMode: "conversion"
      },
      "prepare-export": {
        title: "Replace exported files",
        confirmLabel: "Replace files",
        destructive: true,
        commitKind: "commit-export",
        returnMode: ""
      }
    }[kind]
    if (!options || !result.confirmationToken) {
      setStatus("Calibre returned an invalid confirmation.", true)
      return
    }
    var next = {}
    for (var key in options) next[key] = options[key]
    next.token = result.confirmationToken
    next.body = result.summary || "Confirm this Calibre action."
    confirmation = next
    dialogMode = "confirm"
  }

  function commitConfirmation() {
    if (!confirmation || !confirmation.token) return
    var pending = confirmation
    confirmation = null
    dialogMode = ""
    submit("action.commit", { confirmationToken: pending.token }, pending.commitKind)
  }

  function cancelConfirmation() {
    var pending = confirmation
    var returnMode = pending && pending.returnMode ? pending.returnMode : ""
    confirmation = null
    dialogMode = returnMode
    if (pending && pending.token) {
      var id = bridge.submit("action.discard", viewState.currentLibrary, {
        confirmationToken: pending.token
      })
      rememberRequest(id, "discard-confirmation")
    }
  }

  function cancelJob(requestId) {
    bridge.cancel(requestId)
    setStatus("Cancellation requested.", false)
  }

  function forgetJob(requestId) {
    viewState = Model.forgetJob(viewState, requestId)
    if (jobs.length <= 1 && activeJobCount === 0) dialogMode = ""
  }

  function runSetupAction(actionId) {
    if (actionId === "install.calibre.omarchy") {
      if (bar) bar.run("omarchy-install-app Calibre calibre")
    } else if (actionId === "open.calibre.download") {
      Qt.openUrlExternally("https://calibre-ebook.com/download_linux")
    } else if (actionId === "choose.library") {
      if (!chooseLibrary.running) chooseLibrary.running = true
    } else if (actionId === "retry") {
      bootstrap("")
    }
  }

  function search() {
    if (viewState.mode !== "library" || !viewState.currentLibrary) return
    submit("books.query", {
      search: Model.combineSearch(searchField.text, filterQuery),
      sort: sortField,
      direction: sortDirection,
      limit: Number(setting("pageSize", 50))
    }, "query")
  }

  function loadMore() {
    if (!viewState.nextCursor || !viewState.currentLibrary) return
    submit("books.query", {
      search: Model.combineSearch(searchField.text, filterQuery),
      sort: sortField,
      direction: sortDirection,
      limit: Number(setting("pageSize", 50)),
      cursor: viewState.nextCursor
    }, "query-append")
  }

  function libraryOptions() {
    var options = []
    var libraries = viewState.libraries instanceof Array ? viewState.libraries : []
    for (var i = 0; i < libraries.length; i++) {
      options.push({ value: String(libraries[i].token), label: String(libraries[i].name || "Library") })
    }
    return options
  }

  function switchLibrary(token) {
    if (!token || token === viewState.currentLibrary) return
    viewState = Model.selectLibrary(viewState, token)
    bookIndex = 0
    search()
  }

  function selectBook(index) {
    if (index < 0 || index >= viewState.books.length) return
    bookIndex = index
    viewState = Model.selectBook(viewState, viewState.books[index].id)
  }

  function moveCursor(dx, dy) {
    cursorActive = true
    if (viewState.mode !== "library") return
    if (dx < 0) focusSection = "books"
    else if (dx > 0) focusSection = "actions"
    if (focusSection === "books" && dy !== 0) {
      selectBook(Math.max(0, Math.min(viewState.books.length - 1, bookIndex + dy)))
      scrollSelectedIntoView()
    } else if (focusSection === "actions" && dy !== 0) {
      actionIndex = Math.max(0, Math.min(primaryActions.length - 1, actionIndex + dy))
    }
  }

  function activateCursor() {
    if (focusSection === "books") selectBook(bookIndex)
    else runPrimaryAction(primaryActions[actionIndex].id)
  }

  function runPrimaryAction(actionId) {
    if (!selectedBook) return
    if (actionId === "open") openBook()
    else if (actionId === "metadata") {
      metadataEditor.loadBook()
      dialogMode = "metadata"
    }
    else if (actionId === "convert") {
      conversionDialog.descriptor = null
      conversionDialog.describing = false
      conversionDialog.initialize()
      dialogMode = "conversion"
    }
    else if (actionId === "export" && !exportFolder.running) exportFolder.running = true
  }

  function runSecondaryAction(actionId) {
    if (!selectedBook) return
    if (actionId === "formats") dialogMode = "formats"
    else if (actionId === "remove") submit("action.prepare", {
      name: "book.remove",
      bookIds: [selectedBook.id]
    }, "prepare-remove")
  }

  function commandList() {
    var commands = []
    if (selectedBook) {
      commands.push({ id: "open", label: "Open selected book", key: "o", keywords: "read view format" })
      commands.push({ id: "metadata", label: "Edit metadata", key: "e", keywords: "title author tags cover" })
      if (hasCapability("book.convert.quick"))
        commands.push({ id: "convert", label: "Convert selected book", key: "c", keywords: "epub azw3 pdf mobi" })
      commands.push({ id: "export", label: "Export selected book", key: "s", keywords: "save copy folder" })
      commands.push({ id: "formats", label: "Manage book formats", key: "f", keywords: "add replace remove file" })
      commands.push({ id: "remove", label: "Remove selected book", key: "x", keywords: "delete trash library" })
    }
    commands.push({ id: "search", label: "Search library", key: "/", keywords: "find filter query" })
    commands.push({ id: "add-files", label: "Add book files", key: "", keywords: "import books" })
    commands.push({ id: "add-folder", label: "Add books from folder", key: "", keywords: "import recurse directory" })
    commands.push({ id: "choose-library", label: "Add another library", key: "", keywords: "switch choose folder" })
    commands.push({ id: "refresh", label: "Refresh Calibre library", key: "r", keywords: "reload rescan" })
    if (jobs.length > 0)
      commands.push({ id: "jobs", label: "Show Calibre jobs", key: "", keywords: "progress cancel history" })
    return commands
  }

  function runCommand(commandId) {
    dialogMode = ""
    if (["open", "metadata", "convert", "export"].indexOf(commandId) >= 0) runPrimaryAction(commandId)
    else if (["formats", "remove"].indexOf(commandId) >= 0) runSecondaryAction(commandId)
    else if (commandId === "search") Qt.callLater(function() {
      searchField.forceActiveFocus()
      searchField.selectAll()
    })
    else if (commandId === "add-files" && !addBooks.running) addBooks.running = true
    else if (commandId === "add-folder" && !addFolder.running) addFolder.running = true
    else if (commandId === "choose-library" && !chooseLibrary.running) chooseLibrary.running = true
    else if (commandId === "refresh") refresh()
    else if (commandId === "jobs") dialogMode = "jobs"
  }

  function preferredFormat(book) {
    var preferences = Model.parseFormatPreference(setting("preferredFormats", "EPUB,AZW3,PDF,MOBI"))
    var wanted = Model.preferredFormat(book, preferences)
    var formats = book && book.formats instanceof Array ? book.formats : []
    for (var i = 0; i < formats.length; i++) {
      if (String(formats[i].name || "").toUpperCase() === wanted) return formats[i]
    }
    return null
  }

  function openFormat(format) {
    var path = format && format.path ? String(format.path) : ""
    if (!path) {
      setStatus("This book has no file to open.", true)
      return
    }
    Quickshell.execDetached(["xdg-open", path])
    setStatus("Opening " + String(format.name || "book") + ".", false)
  }

  function openBook() {
    openFormat(preferredFormat(selectedBook))
  }

  function describeConversion(inputFormat, outputFormat) {
    if (!selectedBook) return
    conversionDialog.describing = true
    conversionDialog.descriptor = null
    submit("conversion.describe", {
      bookId: selectedBook.id,
      inputFormat: inputFormat,
      outputFormat: outputFormat
    }, "conversion-describe")
  }

  function convertBook(inputFormat, outputFormat, options, replacesFormat) {
    if (!selectedBook) return
    dialogMode = ""
    var inputData = {
      name: replacesFormat ? "book.convert.replace" : "book.convert.quick",
      bookId: selectedBook.id,
      inputFormat: inputFormat,
      outputFormat: outputFormat,
      options: options || ({})
    }
    if (replacesFormat) submit("action.prepare", inputData, "prepare-convert")
    else submit("action.run", inputData, "convert")
  }

  function formatNameForPath(path) {
    var name = String(path || "")
    var slash = Math.max(name.lastIndexOf("/"), name.lastIndexOf("\\"))
    var dot = name.lastIndexOf(".")
    return dot > slash && dot < name.length - 1 ? name.slice(dot + 1).toUpperCase() : ""
  }

  function hasFormat(name) {
    var formats = selectedBook && selectedBook.formats instanceof Array ? selectedBook.formats : []
    for (var i = 0; i < formats.length; i++) {
      if (String(formats[i].name || "").toUpperCase() === String(name || "").toUpperCase()) return true
    }
    return false
  }

  function addFormatPath(rawPath) {
    var path = String(rawPath || "").trim()
    if (!path || !selectedBook) return
    var replacement = hasFormat(formatNameForPath(path))
    var inputData = {
      name: replacement ? "format.replace" : "format.add",
      bookId: selectedBook.id,
      path: path
    }
    if (replacement) submit("action.prepare", inputData, "prepare-format-replace")
    else submit("action.run", inputData, "format-add")
  }

  function removeFormat(format) {
    if (!selectedBook || !format || !format.name) return
    submit("action.prepare", {
      name: "format.remove",
      bookId: selectedBook.id,
      format: String(format.name)
    }, "prepare-format-remove")
  }

  function setMetadata(fields) {
    if (!selectedBook) return
    dialogMode = ""
    submit("action.run", {
      name: "book.metadata.update",
      bookId: selectedBook.id,
      fields: fields
    }, "metadata")
  }

  function addBookPaths(raw, recursive) {
    var lines = String(raw || "").split(/\r?\n/)
    var paths = []
    for (var i = 0; i < lines.length; i++) {
      var path = lines[i].trim()
      if (path) paths.push(path)
    }
    if (paths.length === 0) return
    submit("action.run", {
      name: "books.import",
      paths: paths,
      recursive: recursive === true,
      duplicatePolicy: "calibre-default"
    }, "import")
  }

  function fileUrl(path) {
    return path ? encodeURI("file://" + String(path)) : ""
  }

  function scrollSelectedIntoView() {
    Qt.callLater(function() {
      if (!bookColumn || bookIndex < 0 || bookIndex >= bookColumn.children.length) return
      var item = bookColumn.children[bookIndex]
      var top = item.y
      var bottom = top + item.height
      if (top < bookScroll.contentY) bookScroll.contentY = top
      else if (bottom > bookScroll.contentY + bookScroll.height)
        bookScroll.contentY = bottom - bookScroll.height
    })
  }

  function authors(book) {
    return book && book.authors instanceof Array && book.authors.length > 0
      ? book.authors.join(", ")
      : "Unknown author"
  }

  function formats(book) {
    if (!book || !(book.formats instanceof Array) || book.formats.length === 0) return "NO FORMAT"
    var names = []
    for (var i = 0; i < book.formats.length; i++) names.push(String(book.formats[i].name || ""))
    return names.join("  ")
  }

  function tags(book) {
    return book && book.tags instanceof Array && book.tags.length > 0
      ? book.tags.join(" · ")
      : "No tags"
  }

  function libraryName() {
    for (var i = 0; i < viewState.libraries.length; i++) {
      if (viewState.libraries[i].token === viewState.currentLibrary) return viewState.libraries[i].name
    }
    return "Library"
  }

  onOpenedChanged: if (opened) {
    cursorActive = false
    if (!bootstrapped) bootstrap("")
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  CalibreBridge {
    id: bridge
    onMessage: function(payload) { root.handleBridgeMessage(payload) }
    onStopped: function(exitCode) {
      if (exitCode !== 0) root.lastError = bridge.lastError || "The Calibre bridge stopped."
    }
  }

  Timer {
    id: searchDelay
    interval: 220
    repeat: false
    onTriggered: root.search()
  }

  Process {
    id: chooseLibrary
    running: false
    command: ["omarchy-file-select", "--title", "Choose Calibre library", "--directory"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var path = String(text || "").trim()
        if (path) root.bootstrap(path)
      }
    }
  }

  Process {
    id: addBooks
    running: false
    command: [
      "omarchy-file-select",
      "--title", "Add books to Calibre",
      "--multiple",
      "--extensions", "epub pdf azw3 mobi docx txt rtf cbz cbr fb2 html htm odt lit prc"
    ]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.addBookPaths(text, false)
    }
  }

  Process {
    id: addFolder
    running: false
    command: ["omarchy-file-select", "--title", "Add a folder to Calibre", "--directory"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.addBookPaths(text, true)
    }
  }

  Process {
    id: coverFile
    running: false
    command: [
      "omarchy-file-select",
      "--title", "Choose a book cover",
      "--extensions", "jpg jpeg png webp gif avif"
    ]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var path = String(text || "").trim()
        if (!path || !root.selectedBook) return
        root.submit("action.run", {
          name: "book.cover.set",
          bookId: root.selectedBook.id,
          path: path
        }, "cover")
      }
    }
  }

  Process {
    id: exportFolder
    running: false
    command: ["omarchy-file-select", "--title", "Export book", "--directory"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var path = String(text || "").trim()
        if (!path || !root.selectedBook) return
        var request = {
          name: "book.export",
          bookIds: [root.selectedBook.id],
          destination: path
        }
        root.submit("action.run", request, "export", request)
      }
    }
  }

  Process {
    id: formatFile
    running: false
    command: [
      "omarchy-file-select",
      "--title", "Add or replace a book format",
      "--extensions", "epub azw3 mobi pdf docx odt rtf txt html htm htmlz fb2 lit prc cbz cbr kepub"
    ]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.addFormatPath(text)
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    centerOnBar: true
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(900))
    contentHeight: panel.fittedContentHeight(Style.space(610), Style.space(610))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: root.dialogMode !== "" || searchField.activeFocus
        || libraryDropdown.popupOpen || sortDropdown.popupOpen || filterDropdown.popupOpen
      onMoveRequested: function(dx, dy) { root.moveCursor(dx, dy) }
      onActivateRequested: root.activateCursor()
      onCloseRequested: root.close()
      onDeleteRequested: root.runSecondaryAction("remove")
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(text) {
        if (text === "/") {
          searchField.forceActiveFocus()
          searchField.selectAll()
        } else if (text === "r" || text === "R") root.refresh()
        else if (text === "o" || text === "O") root.runPrimaryAction("open")
        else if (text === "e" || text === "E") root.runPrimaryAction("metadata")
        else if (text === "c" || text === "C") root.runPrimaryAction("convert")
        else if (text === "s" || text === "S") root.runPrimaryAction("export")
        else if (text === "f" || text === "F") root.runSecondaryAction("formats")
        else if (text === "p" || text === "P" || text === ":") root.dialogMode = "commands"
      }

      Item {
        anchors.fill: parent

        Column {
          id: setupColumn
          visible: root.viewState.mode !== "library"
          width: Math.min(parent.width, Style.space(500))
          anchors.centerIn: parent
          spacing: Style.space(16)

          CalibreIcon {
            width: Style.space(64)
            height: width
            iconSize: width
            anchors.horizontalCenter: parent.horizontalCenter
            opacity: root.viewState.mode === "loading" ? 0.65 : 1
          }

          Text {
            width: parent.width
            text: root.viewState.mode === "loading" ? "Starting Calibre" : root.setup.title
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.title
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
          }

          Text {
            width: parent.width
            text: root.viewState.mode === "loading"
              ? "Checking the Calibre command-line tools and your library."
              : root.setup.body
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
          }

          Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: Style.space(8)

            Repeater {
              model: root.viewState.mode === "loading" ? [] : root.setup.actions

              Button {
                required property var modelData
                text: modelData.label
                bordered: true
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.runSetupAction(modelData.id)
              }
            }
          }

          Text {
            visible: root.lastError !== ""
            width: parent.width
            text: root.lastError
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
          }
        }

        Column {
          id: libraryColumn
          visible: root.viewState.mode === "library"
          anchors.fill: parent
          spacing: Style.space(10)

          Row {
            width: parent.width
            spacing: Style.space(10)

            CalibreIcon {
              id: libraryIcon
              width: Style.space(34)
              height: width
              iconSize: width
              anchors.verticalCenter: parent.verticalCenter
            }

            Column {
              id: libraryControl
              width: Style.space(145)
              spacing: Style.space(2)

              Dropdown {
                id: libraryDropdown
                width: parent.width
                showLabel: false
                options: root.libraryOptions()
                value: root.viewState.currentLibrary
                foreground: root.foreground
                fontFamily: root.fontFamily
                onChanged: function(value) { root.switchLibrary(value) }
              }

              Text {
                width: parent.width
                text: root.viewState.total + (root.viewState.total === 1 ? " book" : " books")
                  + "  ·  calibre " + String(root.viewState.calibre.version || "")
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
              }
            }

            Dropdown {
              id: sortDropdown
              width: Style.space(105)
              showLabel: false
              options: [
                { value: "title", label: "Title" },
                { value: "authors", label: "Author" },
                { value: "timestamp", label: "Date added" },
                { value: "last_modified", label: "Modified" },
                { value: "pubdate", label: "Published" },
                { value: "rating", label: "Rating" }
              ]
              value: root.sortField
              foreground: root.foreground
              fontFamily: root.fontFamily
              anchors.verticalCenter: parent.verticalCenter
              onChanged: function(value) {
                root.sortField = value
                root.search()
              }
            }

            Button {
              id: sortDirectionButton
              text: root.sortDirection === "ascending" ? "↑" : "↓"
              tooltipText: root.sortDirection === "ascending" ? "Ascending" : "Descending"
              foreground: root.foreground
              fontFamily: root.fontFamily
              anchors.verticalCenter: parent.verticalCenter
              onClicked: {
                root.sortDirection = root.sortDirection === "ascending" ? "descending" : "ascending"
                root.search()
              }
            }

            TextField {
              id: searchField
              width: Style.space(180)
              anchors.verticalCenter: parent.verticalCenter
              placeholderText: "Search library  /"
              foreground: root.foreground
              onTextChanged: searchDelay.restart()
              Keys.onEscapePressed: {
                focus = false
                keyCatcher.forceActiveFocus()
              }
              Keys.onReturnPressed: {
                searchDelay.stop()
                root.search()
              }
            }

            Dropdown {
              id: filterDropdown
              width: Style.space(110)
              showLabel: false
              options: [
                { value: "", label: "All books" },
                { value: "date:>30daysago", label: "Added lately" },
                { value: "rating:>=4", label: "Rated 4+" },
                { value: "cover:false", label: "Missing cover" },
                { value: "formats:false", label: "No files" },
                { value: "formats:=EPUB", label: "EPUB" },
                { value: "formats:=PDF", label: "PDF" }
              ]
              value: root.filterQuery
              foreground: root.foreground
              fontFamily: root.fontFamily
              anchors.verticalCenter: parent.verticalCenter
              onChanged: function(value) {
                root.filterQuery = value
                root.search()
              }
            }

            Button {
              text: "Library"
              tooltipText: "Add another Calibre library"
              foreground: root.foreground
              fontFamily: root.fontFamily
              anchors.verticalCenter: parent.verticalCenter
              onClicked: if (!chooseLibrary.running) chooseLibrary.running = true
            }

            Button {
              visible: root.jobs.length > 0
              text: root.activeJobCount > 0 ? "Jobs " + root.activeJobCount : "Jobs"
              tooltipText: "Show Calibre jobs"
              foreground: root.foreground
              fontFamily: root.fontFamily
              anchors.verticalCenter: parent.verticalCenter
              onClicked: root.dialogMode = "jobs"
            }

            Button {
              id: addBooksButton
              text: "Add books"
              iconText: "+"
              bordered: true
              foreground: root.foreground
              fontFamily: root.fontFamily
              anchors.verticalCenter: parent.verticalCenter
              tooltipText: "Right-click to add a folder"
              onClicked: if (!addBooks.running) addBooks.running = true
              onRightClicked: if (!addFolder.running) addFolder.running = true
            }
          }

          BorderSurface {
            visible: root.viewState.notice !== ""
            width: parent.width
            implicitHeight: degradedText.implicitHeight + Style.space(12)
            color: Style.hoverFillFor(root.foreground, Color.accent)
            borderSpec: Border.controlSpec("normal", root.foreground, Color.accent)
            radius: Style.cornerRadius

            Text {
              id: degradedText
              anchors.fill: parent
              anchors.margins: Style.space(6)
              text: root.viewState.notice
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }
          }

          Row {
            width: parent.width
            height: Math.max(0, libraryColumn.height - y)
            spacing: Style.space(12)

            Column {
              width: Math.floor((parent.width - parent.spacing) * 0.47)
              height: parent.height
              spacing: Style.space(6)

              PanelSectionHeader {
                text: "CATALOGUE"
                foreground: root.foreground
                fontFamily: root.fontFamily
              }

              Flickable {
                id: bookScroll
                width: parent.width
                height: Math.max(0, parent.height - y)
                contentWidth: width
                contentHeight: bookColumn.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                flickableDirection: Flickable.VerticalFlick
                interactive: contentHeight > height

                QQC.ScrollBar.vertical: QQC.ScrollBar { policy: QQC.ScrollBar.AsNeeded }

                Column {
                  id: bookColumn
                  width: bookScroll.width
                  spacing: Style.space(2)

                  Repeater {
                    model: root.viewState.books

                    CursorSurface {
                      required property var modelData
                      required property int index
                      width: bookColumn.width
                      implicitHeight: bookRow.implicitHeight + Style.space(12)
                      current: root.selectedBook && String(root.selectedBook.id) === String(modelData.id)
                      hasCursor: root.cursorActive && root.focusSection === "books" && root.bookIndex === index
                      foreground: root.foreground

                      Column {
                        id: bookRow
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.margins: Style.space(6)
                        spacing: Style.space(2)

                        Text {
                          width: parent.width
                          text: (root.selectedBook && String(root.selectedBook.id) === String(modelData.id) ? "> " : "  ") + modelData.title
                          color: root.foreground
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.body
                          font.bold: root.selectedBook && String(root.selectedBook.id) === String(modelData.id)
                          elide: Text.ElideRight
                        }

                        Text {
                          width: parent.width
                          text: root.authors(modelData)
                          color: root.dim
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.caption
                          elide: Text.ElideRight
                        }

                        Text {
                          width: parent.width
                          text: root.formats(modelData)
                          color: root.dim
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.caption
                          font.bold: true
                          elide: Text.ElideRight
                        }
                      }

                      MouseArea {
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onEntered: {
                          root.cursorActive = true
                          root.focusSection = "books"
                          root.selectBook(index)
                        }
                        onClicked: root.selectBook(index)
                      }
                    }
                  }

                  Button {
                    visible: root.viewState.nextCursor !== null
                    width: parent.width
                    text: "Load more"
                    iconText: "+"
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    onClicked: root.loadMore()
                  }
                }
              }
            }

            PanelSeparator {
              width: Style.normalBorderWidth
              height: parent.height
            }

            Flickable {
              width: Math.max(0, parent.width - x)
              height: parent.height
              contentWidth: width
              contentHeight: inspector.implicitHeight
              clip: true
              boundsBehavior: Flickable.StopAtBounds
              flickableDirection: Flickable.VerticalFlick

              QQC.ScrollBar.vertical: QQC.ScrollBar { policy: QQC.ScrollBar.AsNeeded }

              Column {
                id: inspector
                width: parent.width
                spacing: Style.space(12)

                PanelHero {
                  visible: root.selectedBook !== null
                  width: parent.width
                  title: root.selectedBook ? root.selectedBook.title : ""
                  meta: root.selectedBook ? root.authors(root.selectedBook) : ""
                  detail: root.selectedBook ? "#" + root.selectedBook.id : ""
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  iconComponent: Component {
                    Item {
                      width: Style.space(54)
                      height: Style.space(70)

                      Image {
                        anchors.fill: parent
                        visible: source !== ""
                        source: root.selectedBook ? root.fileUrl(root.selectedBook.cover) : ""
                        sourceSize.width: width * 2
                        sourceSize.height: height * 2
                        fillMode: Image.PreserveAspectFit
                        asynchronous: true
                      }

                      CalibreIcon {
                        visible: !root.selectedBook || !root.selectedBook.cover
                        anchors.centerIn: parent
                        width: Style.space(42)
                        height: width
                        iconSize: width
                      }
                    }
                  }
                }

                Text {
                  visible: root.selectedBook === null
                  width: parent.width
                  text: "No books match this search."
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  horizontalAlignment: Text.AlignHCenter
                }

                PanelSeparator { visible: root.selectedBook !== null; width: parent.width }

                PanelSectionHeader {
                  visible: root.selectedBook !== null
                  text: "BOOK"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                }

                Grid {
                  visible: root.selectedBook !== null
                  width: parent.width
                  columns: 2
                  columnSpacing: Style.space(12)
                  rowSpacing: Style.space(7)

                  Repeater {
                    model: root.selectedBook ? [
                      { label: "Formats", value: root.formats(root.selectedBook) },
                      { label: "Series", value: root.selectedBook.series || "—" },
                      { label: "Publisher", value: root.selectedBook.publisher || "—" },
                      { label: "Published", value: Model.formatPublished(root.selectedBook.published) },
                      { label: "Rating", value: root.selectedBook.rating ? root.selectedBook.rating + " / 5" : "—" },
                      { label: "Tags", value: root.tags(root.selectedBook) }
                    ] : []

                    Column {
                      required property var modelData
                      width: Math.floor((inspector.width - Style.space(12)) / 2)
                      spacing: Style.space(2)

                      Text {
                        width: parent.width
                        text: modelData.label.toUpperCase()
                        color: root.dim
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        font.bold: true
                        elide: Text.ElideRight
                      }

                      Text {
                        width: parent.width
                        text: modelData.value
                        color: root.foreground
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.body
                        wrapMode: Text.Wrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                      }
                    }
                  }
                }

                PanelSeparator { visible: root.selectedBook !== null; width: parent.width }

                PanelSectionHeader {
                  visible: root.selectedBook !== null
                  text: "ACTIONS"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                }

                Column {
                  visible: root.selectedBook !== null
                  width: parent.width
                  spacing: Style.space(3)

                  Repeater {
                    model: root.primaryActions

                    Button {
                      required property var modelData
                      required property int index
                      width: parent.width
                      text: modelData.label
                      iconText: modelData.key
                      leftAlign: true
                      foreground: root.foreground
                      fontFamily: root.fontFamily
                      hasCursor: root.cursorActive && root.focusSection === "actions" && root.actionIndex === index
                      onHovered: function(isHovered) {
                        if (!isHovered) return
                        root.cursorActive = true
                        root.focusSection = "actions"
                        root.actionIndex = index
                      }
                      onClicked: root.runPrimaryAction(modelData.id)
                    }
                  }
                }

                PanelSectionHeader {
                  visible: root.selectedBook !== null
                  text: "MORE"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                }

                Row {
                  visible: root.selectedBook !== null
                  width: parent.width
                  spacing: Style.space(6)

                  Repeater {
                    model: root.secondaryActions

                    Button {
                      required property var modelData
                      width: (parent.width - parent.spacing) / 2
                      text: modelData.label
                      iconText: modelData.key
                      foreground: modelData.id === "remove" ? root.urgent : root.foreground
                      fontFamily: root.fontFamily
                      onClicked: root.runSecondaryAction(modelData.id)
                    }
                  }
                }

                Text {
                  visible: root.lastError !== ""
                  width: parent.width
                  text: root.lastError
                  color: root.lastMessageIsError ? root.urgent : root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }

                Text {
                  visible: root.selectedBook !== null
                  width: parent.width
                  text: "/ search  ·  p commands  ·  r refresh"
                  color: Qt.darker(root.foreground, 1.65)
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  horizontalAlignment: Text.AlignHCenter
                }
              }
            }
          }
        }

        MetadataEditor {
          id: metadataEditor
          visible: root.dialogMode === "metadata"
          anchors.fill: parent
          z: 10
          book: root.selectedBook
          foreground: root.foreground
          urgent: root.urgent
          fontFamily: root.fontFamily
          onSaved: function(fields) { root.setMetadata(fields) }
          onCoverRequested: if (!coverFile.running) coverFile.running = true
          onCanceled: root.dialogMode = ""
        }

        ConversionDialog {
          id: conversionDialog
          visible: root.dialogMode === "conversion"
          anchors.fill: parent
          z: 10
          book: root.selectedBook
          capabilities: root.viewState.capabilities
          foreground: root.foreground
          urgent: root.urgent
          fontFamily: root.fontFamily
          onAdvancedRequested: function(inputFormat, outputFormat) {
            root.describeConversion(inputFormat, outputFormat)
          }
          onConversionRequested: function(inputFormat, outputFormat, options, replacesFormat) {
            root.convertBook(inputFormat, outputFormat, options, replacesFormat)
          }
          onCanceled: root.dialogMode = ""
        }

        FormatManager {
          visible: root.dialogMode === "formats"
          anchors.fill: parent
          z: 10
          book: root.selectedBook
          foreground: root.foreground
          urgent: root.urgent
          fontFamily: root.fontFamily
          onOpenRequested: function(format) { root.openFormat(format) }
          onAddRequested: if (!formatFile.running) formatFile.running = true
          onRemoveRequested: function(format) { root.removeFormat(format) }
          onCanceled: root.dialogMode = ""
        }

        JobsDialog {
          visible: root.dialogMode === "jobs"
          anchors.fill: parent
          z: 10
          jobs: root.jobs
          foreground: root.foreground
          urgent: root.urgent
          fontFamily: root.fontFamily
          onCancelRequested: function(requestId) { root.cancelJob(requestId) }
          onForgetRequested: function(requestId) { root.forgetJob(requestId) }
          onCanceled: root.dialogMode = ""
        }

        CommandPalette {
          visible: root.dialogMode === "commands"
          anchors.fill: parent
          z: 10
          commands: root.commandList()
          foreground: root.foreground
          fontFamily: root.fontFamily
          onCommandRequested: function(commandId) { root.runCommand(commandId) }
          onCanceled: root.dialogMode = ""
        }

        CalibreConfirmDialog {
          opened: root.dialogMode === "confirm"
          anchors.fill: parent
          z: 11
          title: root.confirmation ? root.confirmation.title : "Confirm action"
          body: root.confirmation ? root.confirmation.body : ""
          confirmLabel: root.confirmation ? root.confirmation.confirmLabel : "Continue"
          foreground: root.foreground
          fontFamily: root.fontFamily
          onConfirmed: root.commitConfirmation()
          onCanceled: root.cancelConfirmation()
        }
      }
    }
  }
}
