import QtQuick
import qs.Commons
import qs.Ui

Item {
  id: root

  property bool opened: false
  property string title: "Confirm action"
  property string body: ""
  property string confirmLabel: "Continue"
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family

  signal confirmed()
  signal canceled()

  visible: opened

  onOpenedChanged: if (opened) Qt.callLater(function() { keyScope.forceActiveFocus() })

  ConfirmDialog {
    id: dialog
    anchors.fill: parent
    opened: root.opened
    message: root.body ? root.title + "\n\n" + root.body : root.title
    confirmText: root.confirmLabel
    background: Color.background
    foreground: root.foreground
    selectedText: Color.accent
    fontFamily: root.fontFamily
    onConfirmed: root.confirmed()
    onCanceled: root.canceled()
  }

  FocusScope {
    id: keyScope
    anchors.fill: parent
    focus: root.opened
    Keys.priority: Keys.BeforeItem
    Keys.onPressed: function(event) {
      event.accepted = dialog.handleKey(event)
    }
  }
}
