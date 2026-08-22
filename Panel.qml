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
  property int bootstrapGeneration: 0
  property string bootstrapRequestId: ""
  property bool openedFromHotkey: false
  property string dialogMode: ""
  property string sortField: "title"
  property string sortDirection: "ascending"
  property string filterQuery: ""
  property var confirmation: null
  property int workflowGeneration: 0
  property int queryGeneration: 0
  property int conversionSessionGeneration: 0
  property string conversionRequestId: ""
  property int deviceSessionGeneration: 0
  property string deviceRequestId: ""
  property string deviceState: "probing"
  property var deviceBook: null
  property var deviceInfo: null
  property var deviceError: null
  property var deviceConflict: null
  property real deviceProgressFraction: 0
  property bool deviceProgressDeterminate: false
  property string deviceProgressMessage: ""
  property int metadataSessionGeneration: 0
  property string metadataRequestId: ""
  property var metadataBook: null
  property var metadataPreview: null
  property string metadataPreviewToken: ""
  property string metadataPreviewLibraryToken: ""
  property string metadataError: ""
  property bool metadataLoading: false
  property bool metadataApplying: false
  property var coverPickerContext: null
  property var exportPickerContext: null
  property var formatPickerContext: null

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
    if (hasCapability("device.send") && selectedBook
        && selectedBook.formats instanceof Array && selectedBook.formats.length > 0)
      actions.push({ id: "device", label: "Send to reader", key: "d" })
    if (hasCapability("book.convert.quick")) actions.push({ id: "convert", label: "Convert", key: "c" })
    actions.push({ id: "export", label: "Export", key: "s" })
    return actions
  }
  readonly property var secondaryActions: [
    { id: "formats", label: "Manage formats", key: "f" },
    { id: "remove", label: "Remove from library", key: "del" }
  ]

  function open() {
    openedFromHotkey = false
    setCenterHoverRevealSuppressed(false)
    root.controller.show()
  }

  function openFromHotkey() {
    openedFromHotkey = true
    root.controller.show()
    Qt.callLater(function() {
      if (root.opened) setCenterHoverRevealSuppressed(true)
    })
  }

  function close() {
    dismissWorkflow()
    setCenterHoverRevealSuppressed(false)
    root.controller.hide()
  }

  function dismissWorkflow() {
    searchDelay.stop()
    workflowGeneration += 1
    if (dialogMode === "confirm") {
      cancelConfirmation()
      dialogMode = ""
      return
    }
    if (dialogMode === "metadata-download") {
      closeMetadataDialog()
      return
    }
    if (dialogMode === "device") {
      closeDeviceDialog()
      return
    }
    if (dialogMode === "conversion") {
      closeConversionDialog()
      return
    }
    dialogMode = ""
  }

  function toggle() {
    if (opened) close()
    else openFromHotkey()
  }

  function switchPanel(direction) {
    if (bar && typeof bar.switchPanelFrom === "function")
      return bar.switchPanelFrom(barIdentity, direction)
    return false
  }

  function setCenterHoverRevealSuppressed(value) {
    if (bar && "centerHoverRevealSuppressed" in bar)
      bar.centerHoverRevealSuppressed = value
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
    return submitForLibrary(viewState.currentLibrary, operation, inputData, kind, context)
  }

  function submitForLibrary(libraryToken, operation, inputData, kind, context) {
    var id = bridge.submit(operation, libraryToken, inputData)
    rememberRequest(id, kind || operation, context)
    viewState = Model.beginRequest(viewState, id, kind || operation)
    return id
  }

  function beginWorkflowContext(libraryToken, bookId) {
    workflowGeneration += 1
    return {
      workflowGeneration: workflowGeneration,
      libraryToken: String(libraryToken || viewState.currentLibrary || ""),
      bookId: bookId
    }
  }

  function isCurrentWorkflow(context) {
    if (!context || Number(context.workflowGeneration) !== workflowGeneration || !opened)
      return false
    if (String(context.libraryToken || "") !== String(viewState.currentLibrary || ""))
      return false
    return context.bookId === undefined || context.bookId === null
      || (selectedBook !== null && String(context.bookId) === String(selectedBook.id))
  }

  function isCurrentBookContext(context) {
    if (!context || context.bookId === undefined || context.bookId === null)
      return false
    if (String(context.libraryToken || "") !== String(viewState.currentLibrary || ""))
      return false
    if (context.workflowGeneration !== undefined
        && Number(context.workflowGeneration) !== workflowGeneration)
      return false
    return selectedBook !== null && String(context.bookId) === String(selectedBook.id)
  }

  function cancelOutstandingQueries() {
    for (var requestId in requestKinds) {
      var kind = requestKinds[requestId]
      if (kind === "query" || kind === "query-append") bridge.cancel(requestId)
    }
  }

  function hasOutstandingAppend() {
    for (var requestId in requestKinds) {
      if (requestKinds[requestId] !== "query-append") continue
      var context = requestContexts[requestId] || ({})
      if (Number(context.queryGeneration) === queryGeneration) return true
    }
    return false
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
    queryGeneration += 1
    cancelOutstandingQueries()
    bootstrapGeneration += 1
    if (bootstrapRequestId) bridge.cancel(bootstrapRequestId)
    var generation = bootstrapGeneration
    var libraries = rememberedLibraries().slice()
    if (extraLibrary && libraries.indexOf(extraLibrary) === -1) libraries.unshift(extraLibrary)
    var id = bridge.submit("bootstrap", "", {
      rememberedLibraries: libraries,
      pageSize: Number(setting("pageSize", 50)),
      search: Model.combineSearch(searchField.text, filterQuery),
      sort: sortField,
      direction: sortDirection
    })
    bootstrapRequestId = id
    rememberRequest(id, "bootstrap", { bootstrapGeneration: generation })
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

  function isDeviceRequest(kind) {
    return kind === "device-probe" || kind === "device-send"
      || kind === "device-send-commit" || kind === "device-eject"
  }

  function isMetadataRequest(kind) {
    return kind === "metadata-fetch" || kind === "metadata-apply"
  }

  function messageForError(error, fallback) {
    return error && error.message ? String(error.message) : fallback
  }

  function stateForDeviceError(error) {
    var code = error && error.code ? String(error.code) : ""
    if (code === "no_device") return "no-device"
    if (code === "device_locked") return "locked"
    if (code === "destination_exists") return "conflict"
    return "error"
  }

  function isCurrentDeviceRequest(context, requestId) {
    return deviceRequestId === requestId
      && dialogMode === "device"
      && deviceBook !== null
      && Number(context.sessionGeneration) === deviceSessionGeneration
      && String(context.libraryToken || "") === String(viewState.currentLibrary || "")
      && String(context.bookId || "") === String(deviceBook.id || "")
  }

  function isCurrentMetadataRequest(context, requestId) {
    return metadataRequestId === requestId
      && dialogMode === "metadata-download"
      && metadataBook !== null
      && Number(context.sessionGeneration) === metadataSessionGeneration
      && String(context.libraryToken || "") === String(viewState.currentLibrary || "")
      && String(context.bookId || "") === String(metadataBook.id || "")
  }

  function handleDeviceTerminal(event, kind, context) {
    var current = isCurrentDeviceRequest(context, event.id)
    var error = event.error || ({})
    var returnedToken = String(error.confirmationToken || "")
    if (!current) {
      if (event.type === "failed" && returnedToken)
        discardDeviceToken(returnedToken, context.libraryToken)
      if ((event.type === "failed" || event.type === "cancelled")
          && kind === "device-send-commit")
        discardDeviceToken(context.confirmationToken, context.libraryToken)
      return
    }
    deviceRequestId = ""
    deviceProgressMessage = ""

    if (event.type === "cancelled") {
      if (kind === "device-send-commit")
        discardDeviceToken(context.confirmationToken, context.libraryToken)
      deviceState = deviceInfo ? "ready" : "no-device"
      deviceConflict = null
      return
    }
    if (event.type === "failed") {
      if (kind === "device-send-commit")
        discardDeviceToken(context.confirmationToken, context.libraryToken)
      deviceError = event.error || ({ message: "The ebook reader operation failed." })
      if (String(deviceError.code || "") === "destination_exists" && returnedToken) {
        deviceState = "conflict"
        deviceConflict = {
          sessionGeneration: context.sessionGeneration,
          libraryToken: context.libraryToken,
          bookId: context.bookId,
          format: context.format,
          confirmationToken: returnedToken
        }
      } else {
        deviceState = String(deviceError.code || "") === "destination_exists"
          ? "error" : stateForDeviceError(deviceError)
        deviceConflict = null
      }
      return
    }
    if (event.type !== "succeeded") return

    var result = event.result || ({})
    if (kind === "device-probe") {
      deviceState = result.state === "error"
        ? stateForDeviceError(result.error)
        : String(result.state || "error")
      deviceInfo = result.info || null
      deviceError = result.error || null
      deviceConflict = null
    } else if (kind === "device-send" || kind === "device-send-commit") {
      deviceState = "sent"
      deviceError = null
      deviceConflict = null
      deviceProgressFraction = 1
      deviceProgressDeterminate = true
      setStatus("Book sent to " + String(deviceInfo && deviceInfo.deviceName || "ebook reader") + ".", false)
    } else if (kind === "device-eject") {
      deviceState = "ejected"
      deviceError = null
      deviceConflict = null
      setStatus("Ebook reader ejected.", false)
    }
  }

  function handleMetadataTerminal(event, kind, context) {
    var current = isCurrentMetadataRequest(context, event.id)
    var result = event.result || ({})

    if (event.type === "cancelled") {
      if (kind === "metadata-apply")
        discardMetadataToken(context.previewToken, context.libraryToken)
      if (!current) return
      metadataRequestId = ""
      metadataPreview = null
      metadataPreviewToken = ""
      metadataPreviewLibraryToken = ""
      metadataLoading = false
      metadataApplying = false
      return
    }

    if (event.type === "failed") {
      if (!current) return
      metadataRequestId = ""
      metadataLoading = false
      metadataApplying = false
      metadataPreviewToken = ""
      metadataPreviewLibraryToken = ""
      var errorCode = event.error && event.error.code ? String(event.error.code) : ""
      var message = messageForError(event.error, "Calibre could not fetch metadata.")
      if (kind === "metadata-fetch" && errorCode === "metadata_no_result") {
        metadataPreview = { message: message }
        metadataError = ""
      } else {
        metadataPreview = null
        metadataError = message
      }
      return
    }

    if (event.type !== "succeeded") return
    if (kind === "metadata-fetch") {
      if (!current) {
        discardMetadataToken(result.previewToken, context.libraryToken)
        return
      }
      metadataRequestId = ""
      metadataLoading = false
      metadataPreview = result
      metadataPreviewToken = String(result.previewToken || "")
      metadataPreviewLibraryToken = String(context.libraryToken || "")
      metadataError = ""
      return
    }

    if (kind === "metadata-apply") {
      if (result.book && String(context.libraryToken || "") === String(viewState.currentLibrary || ""))
        viewState = Model.applyBook(viewState, result.book)
      if (!current) return
      metadataRequestId = ""
      metadataApplying = false
      metadataPreview = null
      metadataPreviewToken = ""
      metadataPreviewLibraryToken = ""
      metadataError = ""
      metadataSessionGeneration += 1
      metadataBook = null
      dialogMode = ""
      setStatus("Downloaded metadata applied.", false)
    }
  }

  function isCurrentConversionRequest(context, requestId) {
    return conversionRequestId === requestId
      && dialogMode === "conversion"
      && selectedBook !== null
      && Number(context.sessionGeneration) === conversionSessionGeneration
      && String(context.libraryToken || "") === String(viewState.currentLibrary || "")
      && String(context.bookId || "") === String(selectedBook.id || "")
  }

  function handleConversionTerminal(event, context) {
    var current = isCurrentConversionRequest(context, event.id)
    if (!current) {
      viewState = Model.forgetJob(viewState, event.id)
      forgetRequest(event.id)
      return
    }
    conversionRequestId = ""
    conversionDialog.describing = false
    if (event.type === "succeeded") conversionDialog.descriptor = event.result || ({})
    else if (event.type === "failed")
      setStatus(messageForError(event.error, "Calibre could not load conversion options."), true)
    forgetRequest(event.id)
  }

  function handleBridgeMessage(event) {
    if (!event || !event.id) return
    var kind = requestKinds[event.id] || ""
    var context = requestContexts[event.id] || ({})
    viewState = Model.applyBridgeEvent(viewState, event)
    if (event.type === "accepted" || event.type === "progress") {
      if (event.type === "progress" && isDeviceRequest(kind)
          && isCurrentDeviceRequest(context, event.id)) {
        var progress = event.progress || ({})
        if (progress.fraction !== undefined && progress.fraction !== null
            && isFinite(Number(progress.fraction))) {
          deviceProgressFraction = Math.max(0, Math.min(1, Number(progress.fraction)))
          deviceProgressDeterminate = true
        }
        if (progress.message) deviceProgressMessage = String(progress.message)
      }
      return
    }

    var isQueryRequest = kind === "query" || kind === "query-append"
    if (isQueryRequest && Number(context.queryGeneration) !== queryGeneration) {
      viewState = Model.forgetJob(viewState, event.id)
      forgetRequest(event.id)
      return
    }

    if (kind === "bootstrap" && Number(context.bootstrapGeneration) !== bootstrapGeneration) {
      forgetRequest(event.id)
      return
    }
    if (kind === "bootstrap") bootstrapRequestId = ""

    if (kind.indexOf("prepare-") === 0 && !isCurrentWorkflow(context)) {
      if (event.type === "succeeded" && event.result && event.result.confirmationToken)
        discardConfirmationToken(event.result.confirmationToken, context.libraryToken)
      viewState = Model.forgetJob(viewState, event.id)
      forgetRequest(event.id)
      return
    }

    if (kind === "conversion-describe") {
      handleConversionTerminal(event, context)
      return
    }

    if (isDeviceRequest(kind)) {
      handleDeviceTerminal(event, kind, context)
      forgetRequest(event.id)
      return
    }
    if (isMetadataRequest(kind)) {
      handleMetadataTerminal(event, kind, context)
      forgetRequest(event.id)
      return
    }

    if (event.type === "cancelled") {
      setStatus("Calibre operation cancelled.", false)
      forgetRequest(event.id)
      return
    }

    if (kind === "discard-confirmation" || kind === "metadata-discard" || kind === "device-discard") {
      forgetRequest(event.id)
      return
    }

    if (event.type === "failed") {
      var errorCode = event.error && event.error.code ? String(event.error.code) : ""
      if (kind === "export" && errorCode === "confirmation_required") {
        forgetRequest(event.id)
        if (!isCurrentWorkflow(context)) return
        submitForLibrary(context.libraryToken, "action.prepare", {
          name: "book.export.replace",
          bookIds: context.bookIds,
          destination: context.destination
        }, "prepare-export", context)
        return
      }
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
    } else if (kind.indexOf("prepare-") === 0) {
      presentConfirmation(kind, result, context)
    } else if (result.book) {
      var currentBookContext = isCurrentBookContext(context)
      if (currentBookContext) viewState = Model.applyBook(viewState, result.book)
      if (currentBookContext) {
        if (kind === "metadata") setStatus("Metadata saved.", false)
        else if (kind === "cover") setStatus("Cover saved.", false)
        else if (kind === "format-add") setStatus("Format added.", false)
        else if (kind === "commit-format-replace") setStatus("Format replaced.", false)
        else if (kind === "commit-format-remove") setStatus("Format removed.", false)
        else if (kind === "convert" || kind === "commit-convert")
          setStatus("Created " + String(result.outputFormat || "the requested") + " format.", false)
      }
      if (currentBookContext && opened
          && (kind === "format-add" || kind.indexOf("commit-format-") === 0))
        dialogMode = "formats"
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

  function presentConfirmation(kind, result, context) {
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
    if (!isCurrentWorkflow(context)) {
      discardConfirmationToken(result.confirmationToken, context.libraryToken)
      return
    }
    var next = {}
    for (var key in options) next[key] = options[key]
    next.token = result.confirmationToken
    next.body = result.summary || "Confirm this Calibre action."
    next.workflowGeneration = context.workflowGeneration
    next.libraryToken = context.libraryToken
    next.bookId = context.bookId
    confirmation = next
    dialogMode = "confirm"
  }

  function commitConfirmation() {
    if (!confirmation || !confirmation.token) return
    var pending = confirmation
    confirmation = null
    dialogMode = ""
    if (!isCurrentWorkflow(pending)) {
      discardConfirmationToken(pending.token, pending.libraryToken)
      return
    }
    submitForLibrary(pending.libraryToken, "action.commit", {
      confirmationToken: pending.token
    }, pending.commitKind, {
      workflowGeneration: pending.workflowGeneration,
      libraryToken: pending.libraryToken,
      bookId: pending.bookId
    })
  }

  function discardConfirmationToken(token, libraryToken) {
    var value = String(token || "")
    if (!value) return
    var id = bridge.submit("action.discard", String(libraryToken || viewState.currentLibrary || ""), {
      confirmationToken: value
    })
    rememberRequest(id, "discard-confirmation")
  }

  function cancelConfirmation() {
    var pending = confirmation
    var returnMode = pending && pending.returnMode ? pending.returnMode : ""
    confirmation = null
    dialogMode = returnMode
    workflowGeneration += 1
    if (pending && pending.token)
      discardConfirmationToken(pending.token, pending.libraryToken)
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
    queryGeneration += 1
    cancelOutstandingQueries()
    var generation = queryGeneration
    submit("books.query", {
      search: Model.combineSearch(searchField.text, filterQuery),
      sort: sortField,
      direction: sortDirection,
      limit: Number(setting("pageSize", 50))
    }, "query", { queryGeneration: generation })
  }

  function loadMore() {
    if (!viewState.nextCursor || !viewState.currentLibrary || hasOutstandingAppend()) return
    var generation = queryGeneration
    submit("books.query", {
      search: Model.combineSearch(searchField.text, filterQuery),
      sort: sortField,
      direction: sortDirection,
      limit: Number(setting("pageSize", 50)),
      cursor: viewState.nextCursor
    }, "query-append", { queryGeneration: generation })
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
    workflowGeneration += 1
    viewState = Model.selectLibrary(viewState, token)
    bookIndex = 0
    search()
  }

  function selectBook(index) {
    if (index < 0 || index >= viewState.books.length) return
    var nextBook = viewState.books[index]
    if (!selectedBook || String(selectedBook.id) !== String(nextBook.id))
      workflowGeneration += 1
    bookIndex = index
    viewState = Model.selectBook(viewState, nextBook.id)
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
    else if (primaryActions.length > 0) {
      actionIndex = Math.max(0, Math.min(primaryActions.length - 1, actionIndex))
      runPrimaryAction(primaryActions[actionIndex].id)
    }
  }

  function runPrimaryAction(actionId) {
    if (!selectedBook) return
    if (actionId === "open") openBook()
    else if (actionId === "metadata") {
      metadataEditor.loadBook()
      dialogMode = "metadata"
    }
    else if (actionId === "device") openDeviceDialog()
    else if (actionId === "convert") openConversionDialog()
    else if (actionId === "export") openExportPicker()
  }

  function runSecondaryAction(actionId) {
    if (!selectedBook) return
    if (actionId === "formats") dialogMode = "formats"
    else if (actionId === "remove") {
      var context = beginWorkflowContext(viewState.currentLibrary, selectedBook.id)
      submit("action.prepare", {
        name: "book.remove",
        bookIds: [selectedBook.id]
      }, "prepare-remove", context)
    }
  }

  function commandList() {
    var commands = []
    if (selectedBook) {
      commands.push({ id: "open", label: "Open selected book", key: "o", keywords: "read view format" })
      commands.push({ id: "metadata", label: "Edit metadata", key: "e", keywords: "title author tags cover" })
      if (hasCapability("book.metadata.fetch"))
        commands.push({ id: "metadata-fetch", label: "Fetch metadata for selected book", key: "", keywords: "download cover isbn publisher tags" })
      if (hasCapability("device.send") && selectedBook.formats instanceof Array && selectedBook.formats.length > 0)
        commands.push({ id: "device", label: "Send selected book to reader", key: "d", keywords: "ebook device kindle kobo" })
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
    commands.push({ id: "help", label: "Keyboard help and diagnostics", key: "?", keywords: "shortcuts status version troubleshoot" })
    if (jobs.length > 0)
      commands.push({ id: "jobs", label: "Show Calibre jobs", key: "", keywords: "progress cancel history" })
    return commands
  }

  function runCommand(commandId) {
    dialogMode = ""
    if (["open", "metadata", "device", "convert", "export"].indexOf(commandId) >= 0) runPrimaryAction(commandId)
    else if (["formats", "remove"].indexOf(commandId) >= 0) runSecondaryAction(commandId)
    else if (commandId === "metadata-fetch") openMetadataDownload()
    else if (commandId === "search") Qt.callLater(function() {
      searchField.forceActiveFocus()
      searchField.selectAll()
    })
    else if (commandId === "add-files" && !addBooks.running) addBooks.running = true
    else if (commandId === "add-folder" && !addFolder.running) addFolder.running = true
    else if (commandId === "choose-library" && !chooseLibrary.running) chooseLibrary.running = true
    else if (commandId === "refresh") refresh()
    else if (commandId === "jobs") dialogMode = "jobs"
    else if (commandId === "help") dialogMode = "help"
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
    if (conversionRequestId) bridge.cancel(conversionRequestId)
    conversionSessionGeneration += 1
    var generation = conversionSessionGeneration
    conversionDialog.describing = true
    conversionDialog.descriptor = null
    conversionRequestId = submit("conversion.describe", {
      bookId: selectedBook.id,
      inputFormat: inputFormat,
      outputFormat: outputFormat
    }, "conversion-describe", {
      sessionGeneration: generation,
      libraryToken: viewState.currentLibrary,
      bookId: selectedBook.id
    })
  }

  function openConversionDialog() {
    if (!selectedBook) return
    closeConversionDialog()
    conversionSessionGeneration += 1
    conversionDialog.descriptor = null
    conversionDialog.describing = false
    conversionDialog.initialize()
    dialogMode = "conversion"
  }

  function closeConversionDialog() {
    var requestId = conversionRequestId
    conversionRequestId = ""
    conversionSessionGeneration += 1
    if (requestId) bridge.cancel(requestId)
    conversionDialog.describing = false
    conversionDialog.descriptor = null
    dialogMode = ""
  }

  function convertBook(inputFormat, outputFormat, options, replacesFormat) {
    if (!selectedBook) return
    var libraryToken = viewState.currentLibrary
    var bookId = selectedBook.id
    if (conversionRequestId) bridge.cancel(conversionRequestId)
    conversionRequestId = ""
    conversionSessionGeneration += 1
    dialogMode = ""
    var inputData = {
      name: replacesFormat ? "book.convert.replace" : "book.convert.quick",
      bookId: bookId,
      inputFormat: inputFormat,
      outputFormat: outputFormat,
      options: options || ({})
    }
    var context = beginWorkflowContext(libraryToken, bookId)
    if (replacesFormat) {
      submitForLibrary(libraryToken, "action.prepare", inputData, "prepare-convert", context)
    } else {
      submitForLibrary(libraryToken, "action.run", inputData, "convert", context)
    }
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

  function addFormatPath(rawPath, pickerContext) {
    var path = String(rawPath || "").trim()
    var snapshot = pickerContext || ({})
    var book = snapshot.book || selectedBook
    var libraryToken = String(snapshot.libraryToken || viewState.currentLibrary || "")
    if (!path || !book || !libraryToken) return
    var formats = book.formats instanceof Array ? book.formats : []
    var formatName = formatNameForPath(path)
    var replacement = formats.some(function(format) {
      return String(format.name || "").toUpperCase() === formatName
    })
    var inputData = {
      name: replacement ? "format.replace" : "format.add",
      bookId: book.id,
      path: path
    }
    var context = beginWorkflowContext(libraryToken, book.id)
    if (replacement)
      submitForLibrary(libraryToken, "action.prepare", inputData, "prepare-format-replace", context)
    else submitForLibrary(libraryToken, "action.run", inputData, "format-add", context)
  }

  function removeFormat(format) {
    if (!selectedBook || !format || !format.name) return
    var context = beginWorkflowContext(viewState.currentLibrary, selectedBook.id)
    submit("action.prepare", {
      name: "format.remove",
      bookId: selectedBook.id,
      format: String(format.name)
    }, "prepare-format-remove", context)
  }

  function setMetadata(fields) {
    if (!selectedBook) return
    var libraryToken = viewState.currentLibrary
    var bookId = selectedBook.id
    var context = beginWorkflowContext(libraryToken, bookId)
    dialogMode = ""
    submitForLibrary(libraryToken, "action.run", {
      name: "book.metadata.update",
      bookId: bookId,
      fields: fields
    }, "metadata", context)
  }

  function discardMetadataToken(value, libraryToken) {
    var token = String(value || "")
    if (!token) return
    var requestId = bridge.submit("action.discard", String(libraryToken || viewState.currentLibrary || ""), {
      confirmationToken: token
    })
    rememberRequest(requestId, "metadata-discard")
  }

  function discardMetadataPreview() {
    var token = metadataPreviewToken
    var libraryToken = metadataPreviewLibraryToken
    metadataPreview = null
    metadataPreviewToken = ""
    metadataPreviewLibraryToken = ""
    discardMetadataToken(token, libraryToken)
  }

  function openMetadataDownload() {
    if (!selectedBook || !hasCapability("book.metadata.fetch")) return
    if (metadataRequestId) bridge.cancel(metadataRequestId)
    metadataRequestId = ""
    discardMetadataPreview()
    metadataSessionGeneration += 1
    metadataBook = selectedBook
    metadataError = ""
    metadataLoading = false
    metadataApplying = false
    dialogMode = "metadata-download"
    fetchMetadata()
  }

  function fetchMetadata() {
    if (!metadataBook || dialogMode !== "metadata-download"
        || !hasCapability("book.metadata.fetch")) return
    if (metadataRequestId) return
    discardMetadataPreview()
    metadataError = ""
    metadataLoading = true
    metadataApplying = false
    metadataRequestId = submit("action.run", {
      name: "book.metadata.fetch",
      bookId: metadataBook.id
    }, "metadata-fetch", {
      sessionGeneration: metadataSessionGeneration,
      libraryToken: viewState.currentLibrary,
      bookId: metadataBook.id
    })
  }

  function applyMetadataPreview(selectedFields, includeCover) {
    if (!metadataBook || !metadataPreviewToken || metadataLoading || metadataApplying
        || metadataPreviewLibraryToken !== viewState.currentLibrary) return
    var fields = []
    var values = selectedFields || ({})
    for (var field in values) fields.push(field)
    if (includeCover === true) fields.push("cover")
    if (fields.length === 0) return
    metadataError = ""
    metadataApplying = true
    metadataRequestId = submit("action.commit", {
      confirmationToken: metadataPreviewToken,
      fields: fields
    }, "metadata-apply", {
      sessionGeneration: metadataSessionGeneration,
      libraryToken: metadataPreviewLibraryToken,
      bookId: metadataBook.id,
      previewToken: metadataPreviewToken
    })
  }

  function cancelMetadataJob() {
    if (metadataRequestId) bridge.cancel(metadataRequestId)
  }

  function closeMetadataDialog() {
    var requestId = metadataRequestId
    metadataRequestId = ""
    metadataSessionGeneration += 1
    if (requestId) bridge.cancel(requestId)
    discardMetadataPreview()
    metadataBook = null
    metadataError = ""
    metadataLoading = false
    metadataApplying = false
    dialogMode = ""
  }

  function openDeviceDialog() {
    if (!selectedBook || !hasCapability("device.send")) return
    if (deviceRequestId) bridge.cancel(deviceRequestId)
    discardDeviceConflict()
    deviceRequestId = ""
    deviceSessionGeneration += 1
    deviceBook = selectedBook
    dialogMode = "device"
    deviceState = "probing"
    deviceInfo = null
    deviceError = null
    deviceProgressFraction = 0
    deviceProgressDeterminate = false
    deviceProgressMessage = ""
    probeReader()
  }

  function probeReader() {
    if (!deviceBook || dialogMode !== "device" || deviceRequestId) return
    deviceState = "probing"
    deviceError = null
    deviceProgressFraction = 0
    deviceProgressDeterminate = false
    deviceProgressMessage = ""
    deviceRequestId = submit("device.probe", {}, "device-probe", {
      sessionGeneration: deviceSessionGeneration,
      libraryToken: viewState.currentLibrary,
      bookId: deviceBook.id
    })
  }

  function discardDeviceToken(token, libraryToken) {
    var value = String(token || "")
    if (!value) return
    var id = bridge.submit("action.discard", libraryToken || viewState.currentLibrary, {
      confirmationToken: value
    })
    rememberRequest(id, "device-discard")
  }

  function discardDeviceConflict() {
    var conflict = deviceConflict
    deviceConflict = null
    if (conflict && conflict.confirmationToken)
      discardDeviceToken(conflict.confirmationToken, conflict.libraryToken)
  }

  function sendBookToDevice(format, replace) {
    if (!deviceBook || dialogMode !== "device" || !format || deviceRequestId) return
    var requestedFormat = String(format).toUpperCase()
    var replaceConfirmed = replace === true && canReplaceDeviceConflict(requestedFormat)
    var conflict = replaceConfirmed ? deviceConflict : null
    if (replaceConfirmed) deviceConflict = null
    else discardDeviceConflict()
    deviceState = "sending"
    deviceError = null
    deviceProgressFraction = 0
    deviceProgressDeterminate = false
    deviceProgressMessage = ""
    var requestContext = {
      sessionGeneration: deviceSessionGeneration,
      libraryToken: viewState.currentLibrary,
      bookId: deviceBook.id,
      format: requestedFormat
    }
    if (replaceConfirmed) {
      requestContext.confirmationToken = conflict.confirmationToken
      deviceRequestId = submit("action.commit", {
        confirmationToken: conflict.confirmationToken
      }, "device-send-commit", requestContext)
    } else {
      deviceRequestId = submit("device.send", {
        bookId: deviceBook.id,
        format: requestedFormat
      }, "device-send", requestContext)
    }
  }

  function canReplaceDeviceConflict(format) {
    var requestedFormat = String(format || "").toUpperCase()
    return deviceState === "conflict" && deviceConflict !== null && deviceBook !== null
      && Number(deviceConflict.sessionGeneration) === deviceSessionGeneration
      && String(deviceConflict.libraryToken || "") === String(viewState.currentLibrary || "")
      && String(deviceConflict.bookId || "") === String(deviceBook.id || "")
      && String(deviceConflict.format || "") === requestedFormat
      && String(deviceConflict.confirmationToken || "") !== ""
  }

  function ejectReader() {
    if (!deviceBook || dialogMode !== "device" || deviceRequestId) return
    discardDeviceConflict()
    deviceState = "ejecting"
    deviceError = null
    deviceProgressFraction = 0
    deviceProgressDeterminate = false
    deviceProgressMessage = ""
    deviceRequestId = submit("device.eject", {}, "device-eject", {
      sessionGeneration: deviceSessionGeneration,
      libraryToken: viewState.currentLibrary,
      bookId: deviceBook.id
    })
  }

  function cancelDeviceJob() {
    if (deviceRequestId) bridge.cancel(deviceRequestId)
  }

  function closeDeviceDialog() {
    var requestId = deviceRequestId
    deviceRequestId = ""
    deviceSessionGeneration += 1
    if (requestId) bridge.cancel(requestId)
    discardDeviceConflict()
    deviceBook = null
    dialogMode = ""
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

  function openCoverPicker() {
    if (!selectedBook || coverFile.running) return
    coverPickerContext = {
      workflowGeneration: workflowGeneration,
      libraryToken: viewState.currentLibrary,
      bookId: selectedBook.id
    }
    coverFile.running = true
  }

  function openExportPicker() {
    if (!selectedBook || exportFolder.running) return
    exportPickerContext = {
      libraryToken: viewState.currentLibrary,
      bookId: selectedBook.id
    }
    exportFolder.running = true
  }

  function openFormatPicker() {
    if (!selectedBook || formatFile.running) return
    formatPickerContext = {
      libraryToken: viewState.currentLibrary,
      bookId: selectedBook.id,
      book: selectedBook
    }
    formatFile.running = true
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

  function currentLibraryRecord() {
    for (var i = 0; i < viewState.libraries.length; i++) {
      if (viewState.libraries[i].token === viewState.currentLibrary) return viewState.libraries[i]
    }
    return null
  }

  function libraryName() {
    var library = currentLibraryRecord()
    if (library) return library.name || "Library"
    return "Library"
  }

  function libraryPath() {
    var library = currentLibraryRecord()
    return library && library.path ? String(library.path) : ""
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

  Shortcut {
    sequence: "Escape"
    context: Qt.WindowShortcut
    enabled: root.opened && root.dialogMode === "" && !searchField.activeFocus
      && !libraryDropdown.popupOpen && !sortDropdown.popupOpen && !filterDropdown.popupOpen
    onActivated: root.close()
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
        if (!path || !root.coverPickerContext) {
          root.coverPickerContext = null
          return
        }
        var picker = root.coverPickerContext
        root.submitForLibrary(picker.libraryToken, "action.run", {
          name: "book.cover.set",
          bookId: root.coverPickerContext.bookId,
          path: path
        }, "cover", picker)
        root.coverPickerContext = null
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
        if (!path || !root.exportPickerContext) {
          root.exportPickerContext = null
          return
        }
        var picker = root.exportPickerContext
        var context = root.beginWorkflowContext(picker.libraryToken, picker.bookId)
        context.bookIds = [picker.bookId]
        context.destination = path
        var request = {
          name: "book.export",
          bookIds: [root.exportPickerContext.bookId],
          destination: path
        }
        root.submitForLibrary(picker.libraryToken, "action.run", request, "export", context)
        root.exportPickerContext = null
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
      onStreamFinished: {
        root.addFormatPath(text, root.formatPickerContext)
        root.formatPickerContext = null
      }
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
      blocked: root.viewState.mode !== "library" || root.dialogMode !== "" || searchField.activeFocus
        || libraryDropdown.popupOpen || sortDropdown.popupOpen || filterDropdown.popupOpen
        || !keyCatcher.activeFocus
      onMoveRequested: function(dx, dy) { root.moveCursor(dx, dy) }
      onActivateRequested: root.activateCursor()
      onCloseRequested: root.close()
      onDeleteRequested: root.runSecondaryAction("remove")
      onTabRequested: function(direction) {
        if (direction < 0) addBooksButton.forceActiveFocus()
        else searchField.forceActiveFocus()
      }
      onTextKey: function(text) {
        if (text === "/") {
          searchField.forceActiveFocus()
          searchField.selectAll()
        } else if (text === "r" || text === "R") root.refresh()
        else if (text === "o" || text === "O") root.runPrimaryAction("open")
        else if (text === "e" || text === "E") root.runPrimaryAction("metadata")
        else if (text === "d" || text === "D") root.runPrimaryAction("device")
        else if (text === "c" || text === "C") root.runPrimaryAction("convert")
        else if (text === "s" || text === "S") root.runPrimaryAction("export")
        else if (text === "f" || text === "F") root.runSecondaryAction("formats")
        else if (text === "p" || text === "P" || text === ":") root.dialogMode = "commands"
        else if (text === "?") root.dialogMode = "help"
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
            textFormat: Text.PlainText
            width: parent.width
            text: root.viewState.mode === "loading" ? "Starting Calibre" : root.setup.title
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.title
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
          }

          Text {
            textFormat: Text.PlainText
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

          Flow {
            width: parent.width
            height: implicitHeight
            spacing: Style.space(8)

            Repeater {
              model: root.viewState.mode === "loading" ? [] : root.setup.actions

              CalibreButton {
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
            textFormat: Text.PlainText
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

          Flow {
            id: toolbar
            width: parent.width
            height: implicitHeight
            spacing: Style.space(10)

            CalibreIcon {
              id: libraryIcon
              width: Style.space(34)
              height: width
              iconSize: width
            }

            Column {
              id: libraryControl
              width: Math.min(Style.space(145), toolbar.width)
              spacing: Style.space(2)

              CalibreDropdown {
                id: libraryDropdown
                width: parent.width
                accessibleName: "Calibre library"
                showLabel: false
                options: root.libraryOptions()
                value: root.viewState.currentLibrary
                foreground: root.foreground
                fontFamily: root.fontFamily
                onChanged: function(value) { root.switchLibrary(value) }
              }

              Text {
                textFormat: Text.PlainText
                width: parent.width
                text: root.viewState.total + (root.viewState.total === 1 ? " book" : " books")
                  + "  ·  calibre " + String(root.viewState.calibre.version || "")
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
              }
            }

            CalibreDropdown {
              id: sortDropdown
              width: Math.min(Style.space(105), toolbar.width)
              accessibleName: "Sort books"
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
              onChanged: function(value) {
                root.sortField = value
                root.search()
              }
            }

            CalibreButton {
              id: sortDirectionButton
              text: root.sortDirection === "ascending" ? "↑" : "↓"
              tooltipText: root.sortDirection === "ascending" ? "Ascending" : "Descending"
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: {
                root.sortDirection = root.sortDirection === "ascending" ? "descending" : "ascending"
                root.search()
              }
            }

            CalibreTextField {
              id: searchField
              width: Math.min(Style.space(180), toolbar.width)
              accessibleName: "Search library"
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

            CalibreDropdown {
              id: filterDropdown
              width: Math.min(Style.space(110), toolbar.width)
              accessibleName: "Filter books"
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
              onChanged: function(value) {
                root.filterQuery = value
                root.search()
              }
            }

            CalibreButton {
              text: "Library"
              tooltipText: "Add another Calibre library"
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: if (!chooseLibrary.running) chooseLibrary.running = true
            }

            CalibreButton {
              visible: root.jobs.length > 0
              text: root.activeJobCount > 0 ? "Jobs " + root.activeJobCount : "Jobs"
              tooltipText: "Show Calibre jobs"
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.dialogMode = "jobs"
            }

            CalibreButton {
              id: addBooksButton
              text: "Add books"
              iconText: "+"
              bordered: true
              foreground: root.foreground
              fontFamily: root.fontFamily
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
              textFormat: Text.PlainText
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

          Grid {
            id: libraryPanes
            readonly property bool stacked: width < Style.space(640)
            readonly property real availableWidth: Math.max(0,
              width - paneSeparator.width - (stacked ? 0 : columnSpacing * 2))
            readonly property real availableHeight: Math.max(0,
              height - paneSeparator.height - (stacked ? rowSpacing * 2 : 0))
            width: parent.width
            height: Math.max(0, libraryColumn.height - y)
            columns: stacked ? 1 : 3
            columnSpacing: stacked ? 0 : Style.space(12)
            rowSpacing: stacked ? Style.space(8) : 0

            Column {
              id: cataloguePane
              width: libraryPanes.stacked ? libraryPanes.width
                : Math.floor(libraryPanes.availableWidth * 0.47)
              height: libraryPanes.stacked ? Math.floor(libraryPanes.availableHeight * 0.44)
                : libraryPanes.height
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
                      Accessible.role: Accessible.ListItem
                      Accessible.name: String(modelData.title || "Untitled") + ", " + root.authors(modelData)
                      Accessible.selected: current
                      Accessible.onPressAction: root.selectBook(index)

                      Column {
                        id: bookRow
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.margins: Style.space(6)
                        spacing: Style.space(2)

                        Text {
                          textFormat: Text.PlainText
                          width: parent.width
                          text: (root.selectedBook && String(root.selectedBook.id) === String(modelData.id) ? "> " : "  ") + modelData.title
                          color: root.foreground
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.body
                          font.bold: root.selectedBook && String(root.selectedBook.id) === String(modelData.id)
                          elide: Text.ElideRight
                        }

                        Text {
                          textFormat: Text.PlainText
                          width: parent.width
                          text: root.authors(modelData)
                          color: root.dim
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.caption
                          elide: Text.ElideRight
                        }

                        Text {
                          textFormat: Text.PlainText
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

                  CalibreButton {
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
              id: paneSeparator
              width: libraryPanes.stacked ? libraryPanes.width : Style.normalBorderWidth
              height: libraryPanes.stacked ? Style.normalBorderWidth : libraryPanes.height
            }

            Flickable {
              width: libraryPanes.stacked ? libraryPanes.width
                : Math.max(0, libraryPanes.availableWidth - cataloguePane.width)
              height: libraryPanes.stacked
                ? Math.max(0, libraryPanes.availableHeight - cataloguePane.height)
                : libraryPanes.height
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
                  textFormat: Text.PlainText
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
                        textFormat: Text.PlainText
                        width: parent.width
                        text: modelData.label.toUpperCase()
                        color: root.dim
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        font.bold: true
                        elide: Text.ElideRight
                      }

                      Text {
                        textFormat: Text.PlainText
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

                    CalibreButton {
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

                    CalibreButton {
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
                  textFormat: Text.PlainText
                  visible: root.lastError !== ""
                  width: parent.width
                  text: root.lastError
                  color: root.lastMessageIsError ? root.urgent : root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }

                Text {
                  textFormat: Text.PlainText
                  visible: root.selectedBook !== null
                  width: parent.width
                  text: "/ search  ·  p commands  ·  ? help  ·  r refresh"
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
          downloadAvailable: root.hasCapability("book.metadata.fetch")
          onSaved: function(fields) { root.setMetadata(fields) }
          onCoverRequested: root.openCoverPicker()
          onDownloadRequested: root.openMetadataDownload()
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
          onCanceled: root.closeConversionDialog()
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
          onAddRequested: root.openFormatPicker()
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

        DeviceDialog {
          visible: root.dialogMode === "device"
          anchors.fill: parent
          z: 10
          book: root.deviceBook
          preferredFormats: Model.parseFormatPreference(root.setting("preferredFormats", "EPUB,AZW3,PDF,MOBI"))
          deviceState: root.deviceState
          deviceInfo: root.deviceInfo
          deviceError: root.deviceError
          conflictFormat: root.deviceConflict ? String(root.deviceConflict.format || "") : ""
          deviceCapabilities: root.viewState.capabilities.device || ({})
          progressFraction: root.deviceProgressFraction
          progressDeterminate: root.deviceProgressDeterminate
          progressMessage: root.deviceProgressMessage
          foreground: root.foreground
          urgent: root.urgent
          fontFamily: root.fontFamily
          onSendRequested: function(format, replace) { root.sendBookToDevice(format, replace) }
          onRetryRequested: root.probeReader()
          onEjectRequested: root.ejectReader()
          onCancelRequested: root.cancelDeviceJob()
          onCanceled: root.closeDeviceDialog()
        }

        MetadataDownloadDialog {
          visible: root.dialogMode === "metadata-download"
          anchors.fill: parent
          z: 10
          book: root.metadataBook
          preview: root.metadataPreview
          loading: root.metadataLoading
          applying: root.metadataApplying
          error: root.metadataError
          foreground: root.foreground
          urgent: root.urgent
          fontFamily: root.fontFamily
          onFetchRequested: root.fetchMetadata()
          onRetryRequested: root.fetchMetadata()
          onApplyRequested: function(selectedFields, includeCover) {
            root.applyMetadataPreview(selectedFields, includeCover)
          }
          onCancelJobRequested: root.cancelMetadataJob()
          onDiscarded: root.discardMetadataPreview()
          onCanceled: root.closeMetadataDialog()
        }

        HelpDialog {
          visible: root.dialogMode === "help"
          anchors.fill: parent
          z: 10
          calibreVersion: String(root.viewState.calibre.version || "")
          calibreStatus: String(root.viewState.readiness.state || root.viewState.calibre.status || "")
          libraryName: root.libraryName()
          libraryPath: root.libraryPath()
          foreground: root.foreground
          fontFamily: root.fontFamily
          onRetryRequested: {
            root.dialogMode = ""
            root.refresh()
          }
          onCanceled: root.dialogMode = ""
        }

        CalibreConfirmDialog {
          opened: root.dialogMode === "confirm"
          anchors.fill: parent
          z: 11
          title: root.confirmation && root.confirmation.title
            ? String(root.confirmation.title) : "Confirm action"
          body: root.confirmation && root.confirmation.body ? String(root.confirmation.body) : ""
          confirmLabel: root.confirmation && root.confirmation.confirmLabel
            ? String(root.confirmation.confirmLabel) : "Continue"
          foreground: root.foreground
          fontFamily: root.fontFamily
          onConfirmed: root.commitConfirmation()
          onCanceled: root.cancelConfirmation()
        }
      }
    }
  }
}
