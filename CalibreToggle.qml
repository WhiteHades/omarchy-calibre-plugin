import QtQuick
import qs.Ui

Toggle {
  id: root

  Accessible.role: Accessible.CheckBox
  Accessible.name: root.label
  Accessible.description: root.description
  Accessible.checked: root.checked
  Accessible.onPressAction: root.clicked()
}
