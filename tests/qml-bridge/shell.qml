import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
  id: root
  readonly property string resultPath: Quickshell.env("OMARCHY_CALIBRE_TEST_RESULT")
  property string requestId: ""

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
      if (payload.id !== root.requestId || payload.type === "accepted") return
      if (payload.type === "succeeded") root.finish({ ok: true, result: payload.result })
      else if (payload.type === "failed") root.finish({ ok: false, error: payload.error })
    }
  }

  Timer {
    interval: 1
    running: true
    repeat: false
    onTriggered: {
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
