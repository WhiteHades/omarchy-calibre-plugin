import QtQuick
import Quickshell.Io

Item {
  id: root
  visible: false

  property int requestNumber: 0
  property var pendingLines: []
  property var activeRequests: ({})
  property string lastError: ""
  readonly property bool running: bridgeProcess.running
  readonly property string scriptPath: decodeURIComponent(
    String(Qt.resolvedUrl("backend/calibre_bridge.py")).replace(/^file:\/\//, ""))

  signal message(var payload)
  signal stopped(int exitCode)

  function start() {
    if (!bridgeProcess.running) {
      lastError = ""
      bridgeProcess.running = true
    }
  }

  function stop() {
    if (bridgeProcess.running) bridgeProcess.running = false
  }

  function nextId(operation) {
    requestNumber += 1
    return String(operation).replace(/[^a-z0-9]+/gi, "-") + "-" + requestNumber
  }

  function submit(operation, libraryToken, inputData) {
    var id = nextId(operation)
    var request = {
      protocol: 1,
      id: id,
      operation: operation,
      input: inputData || {}
    }
    if (libraryToken) request.library = libraryToken
    trackRequest(id, -1)
    enqueue(JSON.stringify(request))
    return id
  }

  function trackRequest(requestId, sequence) {
    var next = {}
    for (var id in activeRequests) next[id] = activeRequests[id]
    next[requestId] = Number(sequence)
    activeRequests = next
  }

  function forgetRequest(requestId) {
    var next = {}
    for (var id in activeRequests) if (id !== requestId) next[id] = activeRequests[id]
    activeRequests = next
  }

  function failPending() {
    var pending = activeRequests
    activeRequests = ({})
    pendingLines = []
    for (var id in pending) {
      message({
        protocol: 1,
        id: id,
        sequence: Math.max(0, Number(pending[id]) || 0) + 1,
        type: "failed",
        error: {
          code: "bridge_stopped",
          message: "The Calibre bridge stopped before the operation finished.",
          retryable: true
        }
      })
    }
  }

  function cancel(requestId) {
    var id = String(requestId || "")
    if (!id) return
    enqueue(JSON.stringify({
      protocol: 1,
      type: "cancel",
      id: id
    }))
  }

  function enqueue(line) {
    if (bridgeProcess.running) {
      bridgeProcess.write(line + "\n")
      return
    }
    var queued = pendingLines.slice()
    queued.push(line)
    pendingLines = queued
    start()
  }

  function flush() {
    var queued = pendingLines
    pendingLines = []
    for (var i = 0; i < queued.length; i++) bridgeProcess.write(queued[i] + "\n")
  }

  function readLine(line) {
    var text = String(line || "").trim()
    if (!text) return
    try {
      var payload = JSON.parse(text)
      var id = String(payload.id || "")
      if (id && activeRequests[id] !== undefined) trackRequest(id, payload.sequence)
      message(payload)
      if (id && (payload.type === "succeeded" || payload.type === "failed"
          || payload.type === "cancelled")) forgetRequest(id)
    } catch (error) {
      lastError = "The Calibre bridge returned invalid data."
    }
  }

  Process {
    id: bridgeProcess
    running: false
    command: ["python3", root.scriptPath]
    stdinEnabled: true
    stdout: SplitParser { onRead: function(line) { root.readLine(line) } }
    stderr: SplitParser {
      onRead: function(line) {
        var text = String(line || "").trim()
        if (text) root.lastError = "The Calibre bridge reported an internal error."
      }
    }
    onStarted: root.flush()
    onExited: function(exitCode) {
      if (exitCode !== 0 && !root.lastError)
        root.lastError = "The Calibre bridge stopped unexpectedly."
      root.failPending()
      root.stopped(exitCode)
    }
  }
}
