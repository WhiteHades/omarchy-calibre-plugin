import QtQuick
import qs.Ui

TextField {
  id: root

  property string accessibleName: ""

  Accessible.role: Accessible.EditableText
  Accessible.name: root.accessibleName !== "" ? root.accessibleName : root.placeholderText
}
