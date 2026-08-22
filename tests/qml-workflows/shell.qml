pragma ComponentBehavior: Bound

import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import "." as Plugin
import "Model.js" as Model

ShellRoot {
  id: root

  readonly property string resultPath: Quickshell.env("OMARCHY_CALIBRE_TEST_RESULT")
  property var failures: []

  function expect(condition, message) {
    if (condition) return
    var next = failures.slice()
    next.push(message)
    failures = next
  }

  function requestIdFor(kind) {
    for (var requestId in panel.requestKinds) {
      if (panel.requestKinds[requestId] === kind) return requestId
    }
    return ""
  }

  function finish() {
    safetyTimeout.stop()
    resultFile.setText(JSON.stringify({ ok: failures.length === 0, failures: failures }))
    Qt.callLater(Qt.quit)
  }

  FileView {
    id: resultFile
    path: root.resultPath
  }

  Item {
    id: anchor
    visible: false
    width: 1
    height: 1
  }

  QtObject {
    id: fakeShell
    function updateEntryInline(moduleName, settings) { return true }
  }

  QtObject {
    id: fakeBar
    property bool vertical: false
    property int barSize: 28
    property string position: "top"
    property string fontFamily: Style.font.family
    property color foreground: Color.foreground
    property color barForeground: Color.foreground
    property color background: Color.background
    property color urgent: Color.urgent
    property var shell: fakeShell
    property var activePopout: null
    property var clickTargets: []
    property bool centerHoverRevealSuppressed: false
    property bool foregroundAnimationEnabled: false

    function run(command) {}
    function showTooltip(owner, text) {}
    function hideTooltip(owner) {}
    function requestPopout(owner) { activePopout = owner }
    function releasePopout(owner) { if (activePopout === owner) activePopout = null }
    function switchPanelFrom(owner, direction) { return false }
    function registerClickTarget(target) {}
    function unregisterClickTarget(target) {}
    function targetBelongsToWindow(target, window) { return true }
  }

  Plugin.Panel {
    id: panel
    visible: false
    width: 1
    height: 1
    anchorItem: anchor
    bar: fakeBar
    settings: ({ pageSize: 50 })
  }

  Timer {
    interval: 1
    running: true
    repeat: false
    onTriggered: {
      var bookA = { id: 1, title: "Book A", authors: ["Author A"], formats: [{ name: "EPUB" }] }
      var bookB = { id: 2, title: "Book B", authors: ["Author B"], formats: [{ name: "EPUB" }, { name: "PDF" }] }
      var state = Model.initialState()
      state.mode = "library"
      state.loading = false
      state.currentLibrary = "library-b"
      state.books = [bookB]
      state.selectedBook = bookB
      state.capabilities = { actions: ["book.metadata.fetch", "device.send"], device: {} }
      panel.viewState = state

      panel.bootstrapGeneration = 2
      panel.bootstrapRequestId = "bootstrap-new"
      panel.requestKinds = ({ "bootstrap-old": "bootstrap", "bootstrap-new": "bootstrap" })
      panel.requestContexts = ({
        "bootstrap-old": { bootstrapGeneration: 1 },
        "bootstrap-new": { bootstrapGeneration: 2 }
      })
      panel.handleBridgeMessage({
        id: "bootstrap-old",
        sequence: 1,
        type: "succeeded",
        result: {
          readiness: { state: "ready" },
          currentLibrary: "library-a",
          libraries: [{ token: "library-a", name: "Old library" }],
          page: { items: [bookA], total: 1 },
          capabilities: { actions: [] }
        }
      })
      root.expect(panel.viewState.currentLibrary === "library-b", "stale bootstrap replaced the active library")
      root.expect(panel.bootstrapRequestId === "bootstrap-new", "stale bootstrap cleared the active request")

      panel.setCenterHoverRevealSuppressed(true)
      root.expect(fakeBar.centerHoverRevealSuppressed === true, "panel did not suppress the native center reveal")
      panel.setCenterHoverRevealSuppressed(false)
      root.expect(fakeBar.centerHoverRevealSuppressed === false, "panel did not release the native center reveal")

      panel.dialogMode = "metadata-download"
      panel.metadataSessionGeneration = 2
      panel.metadataBook = bookB
      panel.metadataRequestId = "metadata-b"
      panel.metadataPreview = { previewToken: "preview-b", changes: [] }
      panel.metadataPreviewToken = "preview-b"
      panel.metadataPreviewLibraryToken = "library-b"
      panel.metadataLoading = true
      panel.requestKinds = ({ "metadata-a": "metadata-fetch", "metadata-b": "metadata-fetch" })
      panel.requestContexts = ({
        "metadata-a": { sessionGeneration: 1, libraryToken: "library-a", bookId: bookA.id },
        "metadata-b": { sessionGeneration: 2, libraryToken: "library-b", bookId: bookB.id }
      })
      panel.handleBridgeMessage({
        id: "metadata-a",
        sequence: 1,
        type: "succeeded",
        result: { previewToken: "preview-a", changes: [{ field: "title" }] }
      })
      root.expect(panel.metadataPreviewToken === "preview-b", "stale metadata replaced the active preview")
      root.expect(panel.metadataRequestId === "metadata-b", "stale metadata cleared the active request")
      root.expect(panel.metadataLoading === true, "stale metadata changed the active loading state")
      var discardFound = false
      for (var requestId in panel.requestKinds) {
        if (panel.requestKinds[requestId] === "metadata-discard") discardFound = true
      }
      root.expect(discardFound, "stale metadata preview was not discarded")

      panel.dialogMode = "device"
      panel.deviceSessionGeneration = 2
      panel.deviceBook = bookB
      panel.deviceState = "ready"
      panel.deviceConflict = null
      panel.deviceRequestId = "device-b"
      panel.requestKinds = ({ "device-a": "device-send", "device-b": "device-probe" })
      panel.requestContexts = ({
        "device-a": { sessionGeneration: 1, libraryToken: "library-a", bookId: bookA.id, format: "EPUB" },
        "device-b": { sessionGeneration: 2, libraryToken: "library-b", bookId: bookB.id }
      })
      panel.handleBridgeMessage({
        id: "device-a",
        sequence: 1,
        type: "failed",
        error: {
          code: "destination_exists",
          message: "Already present",
          confirmationToken: "stale-device-confirmation"
        }
      })
      root.expect(panel.deviceState === "ready", "stale reader conflict changed the active dialog")
      root.expect(panel.deviceConflict === null, "stale reader conflict enabled replacement")
      root.expect(panel.deviceRequestId === "device-b", "stale reader conflict cleared the active request")
      root.expect(root.requestIdFor("device-discard") !== "", "stale reader confirmation was not discarded")

      panel.deviceSessionGeneration = 3
      panel.deviceRequestId = "device-locked"
      panel.requestKinds = ({ "device-locked": "device-probe" })
      panel.requestContexts = ({
        "device-locked": { sessionGeneration: 3, libraryToken: "library-b", bookId: bookB.id }
      })
      panel.handleBridgeMessage({
        id: "device-locked",
        sequence: 1,
        type: "succeeded",
        result: { state: "error", error: { code: "device_locked", message: "Unlock reader" } }
      })
      root.expect(panel.deviceState === "locked", "locked probe did not reach the locked state")

      panel.deviceSessionGeneration = 4
      panel.deviceState = "sending"
      panel.deviceRequestId = "device-unconfirmed"
      panel.requestKinds = ({ "device-unconfirmed": "device-send" })
      panel.requestContexts = ({
        "device-unconfirmed": {
          sessionGeneration: 4,
          libraryToken: "library-b",
          bookId: bookB.id,
          format: "EPUB"
        }
      })
      panel.handleBridgeMessage({
        id: "device-unconfirmed",
        sequence: 1,
        type: "failed",
        error: { code: "destination_exists", message: "Already present" }
      })
      root.expect(panel.deviceState === "error", "reader conflict without a token enabled replacement")

      panel.deviceSessionGeneration = 5
      panel.deviceState = "sending"
      panel.deviceRequestId = "device-conflict"
      panel.requestKinds = ({ "device-conflict": "device-send" })
      panel.requestContexts = ({
        "device-conflict": {
          sessionGeneration: 5,
          libraryToken: "library-b",
          bookId: bookB.id,
          format: "EPUB"
        }
      })
      panel.handleBridgeMessage({
        id: "device-conflict",
        sequence: 1,
        type: "failed",
        error: {
          code: "destination_exists",
          message: "Already present",
          confirmationToken: "device-confirmation"
        }
      })
      root.expect(panel.deviceState === "conflict", "current reader conflict was not shown")
      root.expect(panel.canReplaceDeviceConflict("EPUB") === true, "matching reader conflict cannot be replaced")
      root.expect(panel.canReplaceDeviceConflict("PDF") === false, "unconfirmed reader format can be replaced")

      panel.sendBookToDevice("EPUB", true)
      var commitId = root.requestIdFor("device-send-commit")
      root.expect(commitId !== "", "reader replacement did not submit a commit")
      root.expect(
        commitId && panel.requestContexts[commitId].confirmationToken === "device-confirmation",
        "reader replacement did not preserve the exact confirmation token"
      )
      root.expect(panel.deviceConflict === null, "reader replacement left the conflict active")
      panel.handleBridgeMessage({ id: commitId, sequence: 1, type: "cancelled" })
      root.expect(root.requestIdFor("device-discard") !== "", "cancelled reader replacement was not discarded")

      panel.deviceSessionGeneration = 6
      panel.deviceState = "sending"
      panel.deviceRequestId = "device-close-conflict"
      panel.requestKinds = ({ "device-close-conflict": "device-send" })
      panel.requestContexts = ({
        "device-close-conflict": {
          sessionGeneration: 6,
          libraryToken: "library-b",
          bookId: bookB.id,
          format: "EPUB"
        }
      })
      panel.handleBridgeMessage({
        id: "device-close-conflict",
        sequence: 1,
        type: "failed",
        error: {
          code: "destination_exists",
          message: "Already present",
          confirmationToken: "close-device-confirmation"
        }
      })
      panel.closeDeviceDialog()
      root.expect(root.requestIdFor("device-discard") !== "", "closing a reader conflict did not discard it")
      root.expect(panel.dialogMode === "", "closing a reader conflict left the dialog open")

      panel.confirmation = {
        token: "panel-close-confirmation",
        returnMode: "formats"
      }
      panel.dialogMode = "confirm"
      panel.dismissWorkflow()
      root.expect(panel.confirmation === null, "closing the panel retained a destructive confirmation")
      root.expect(panel.dialogMode === "", "closing the panel reopened the confirmation return mode")
      root.expect(root.requestIdFor("discard-confirmation") !== "", "closing the panel did not discard its confirmation")

      panel.dialogMode = "metadata-download"
      panel.metadataBook = bookB
      panel.metadataRequestId = "metadata-close"
      panel.metadataLoading = true
      panel.dismissWorkflow()
      root.expect(panel.dialogMode === "", "closing the panel retained the metadata dialog")
      root.expect(panel.metadataBook === null, "closing the panel retained metadata session state")
      root.expect(panel.metadataRequestId === "", "closing the panel retained a metadata request")

      panel.dialogMode = "jobs"
      panel.dismissWorkflow()
      root.expect(panel.dialogMode === "", "closing the panel retained a passive dialog")

      panel.workflowGeneration = 4
      panel.dialogMode = ""
      panel.confirmation = null
      panel.requestKinds = ({ "stale-prepare": "prepare-remove" })
      panel.requestContexts = ({
        "stale-prepare": {
          workflowGeneration: 3,
          libraryToken: "library-b",
          bookId: bookB.id
        }
      })
      panel.handleBridgeMessage({
        id: "stale-prepare",
        sequence: 1,
        type: "succeeded",
        result: { confirmationToken: "stale-prepare-token", summary: "Remove Book B" }
      })
      root.expect(panel.dialogMode === "", "stale preparation reopened a confirmation")
      root.expect(panel.confirmation === null, "stale preparation replaced confirmation state")
      root.expect(root.requestIdFor("discard-confirmation") !== "", "stale preparation token was not discarded")

      panel.workflowGeneration = 8
      panel.dialogMode = ""
      panel.requestKinds = ({ "stale-format-result": "commit-format-replace" })
      panel.requestContexts = ({
        "stale-format-result": {
          workflowGeneration: 7,
          libraryToken: "library-b",
          bookId: bookA.id
        }
      })
      panel.handleBridgeMessage({
        id: "stale-format-result",
        sequence: 1,
        type: "succeeded",
        result: {
          book: { id: bookA.id, title: "Changed Book A", authors: ["Author A"], formats: [{ name: "PDF" }] }
        }
      })
      root.expect(panel.viewState.books.length === 1, "stale mutation inserted a different book into the current view")
      root.expect(panel.viewState.selectedBook.id === bookB.id, "stale mutation replaced the visible selection")
      root.expect(panel.dialogMode === "", "stale mutation reopened the format manager")

      root.finish()
    }
  }

  Timer {
    id: safetyTimeout
    interval: 4000
    running: true
    repeat: false
    onTriggered: {
      root.expect(false, "workflow test timed out")
      root.finish()
    }
  }
}
