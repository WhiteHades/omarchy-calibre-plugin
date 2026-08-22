import QtQuick
import qs.Ui

Dropdown {
  id: root

  property string accessibleName: ""

  Accessible.role: Accessible.ComboBox
  Accessible.name: root.accessibleName !== "" ? root.accessibleName : root.label
  Accessible.description: "Selected " + root.currentLabel()
  Accessible.onPressAction: root.toggle()
}
