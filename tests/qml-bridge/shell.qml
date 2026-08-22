import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
  id: root
  readonly property string resultPath: Quickshell.env("OMARCHY_CALIBRE_TEST_RESULT")
  property string requestId: ""
  property bool pendingFailureValid: false

  function finish(payload) {
    timeout.stop()
    resultFile.setText(JSON.stringify(payload))
    Qt.callLater(Qt.quit)
  }

  FileView {
    id: resultFile
    path: root.resultPath
  }

  CalibreBridge {
    id: bridge
    onMessage: function(payload) {
      if (payload.id === "pending-on-exit") {
        root.pendingFailureValid = payload.type === "failed"
          && payload.sequence === 4
          && payload.error && payload.error.code === "bridge_stopped"
          && payload.error.retryable === true
        return
      }
      if (payload.id !== root.requestId || payload.type === "accepted") return
      if (payload.type === "succeeded") root.finish({
        ok: true,
        pendingFailure: root.pendingFailureValid,
        result: payload.result
      })
      else if (payload.type === "failed") root.finish({ ok: false, error: payload.error })
    }
  }

  Timer {
    interval: 1
    running: true
    repeat: false
    onTriggered: {
      bridge.trackRequest("pending-on-exit", 3)
      bridge.failPending()
      root.requestId = bridge.submit("bootstrap", "", {
        rememberedLibraries: [],
        pageSize: 50
      })
    }
  }

  Timer {
    id: timeout
    interval: 5000
    running: true
    repeat: false
    onTriggered: root.finish({ ok: false, error: { message: "Bridge test timed out" } })
  }
}
