import QtQuick
import qs.Ui

PanelActionButton {
  id: root

  focusable: true
  Accessible.role: Accessible.Button
  Accessible.name: root.tooltipText !== "" ? root.tooltipText : root.iconText
  Accessible.onPressAction: if (root.enabled) root.clicked()
}
