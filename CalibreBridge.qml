import QtQuick
import Quickshell.Io

Item {
  id: root
  visible: false

  property int requestNumber: 0
  property var pendingLines: []
  property string lastError: ""
  readonly property bool running: bridgeProcess.running
  readonly property string scriptPath: decodeURIComponent(
    String(Qt.resolvedUrl("backend/calibre_bridge.py")).replace(/^file:\/\//, ""))

  signal message(var payload)
  signal stopped(int exitCode)

  function start() {
    if (!bridgeProcess.running) bridgeProcess.running = true
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
    enqueue(JSON.stringify(request))
    return id
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
      message(JSON.parse(text))
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
        if (text) root.lastError = text
      }
    }
    onStarted: root.flush()
    onExited: function(exitCode) {
      if (exitCode !== 0 && !root.lastError)
        root.lastError = "The Calibre bridge stopped unexpectedly."
      root.stopped(exitCode)
    }
  }
}
