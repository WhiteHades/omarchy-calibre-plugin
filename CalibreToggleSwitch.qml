import QtQuick
import qs.Ui

ToggleSwitch {
  id: root

  property string accessibleName: ""

  activeFocusOnTab: root.interactive
  Keys.onReturnPressed: if (root.interactive && !root.busy) root.toggled()
  Keys.onEnterPressed: if (root.interactive && !root.busy) root.toggled()
  Keys.onSpacePressed: if (root.interactive && !root.busy) root.toggled()
  Accessible.role: Accessible.CheckBox
  Accessible.name: root.accessibleName
  Accessible.checked: root.checked
  Accessible.onPressAction: if (root.interactive && !root.busy) root.toggled()
}
