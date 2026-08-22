import QtQuick
import qs.Ui

Button {
  id: root

  focusable: true
  clip: true
  Accessible.role: Accessible.Button
  Accessible.name: root.text !== "" ? root.text : root.tooltipText
  Accessible.description: root.tooltipText !== root.Accessible.name ? root.tooltipText : ""
  Accessible.onPressAction: if (root.enabled) root.clicked()
}
