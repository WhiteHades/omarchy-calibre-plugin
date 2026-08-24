import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HelpDialogContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (ROOT / "HelpDialog.qml").read_text()

    def test_help_uses_native_components_and_calibre_identity(self) -> None:
        for component in ("BorderSurface {", "CalibreIcon {", "PanelSectionHeader {", "PanelSeparator {", "Button {"):
            self.assertIn(component, self.source)

    def test_help_reports_the_manifest_version(self) -> None:
        manifest = json.loads((ROOT / "manifest.json").read_text())

        self.assertIn(f'Calibre {manifest["version"]}', self.source)

    def test_help_exposes_shortcuts_and_runtime_diagnostics(self) -> None:
        for prop in (
            "property string calibreVersion",
            "property string calibreStatus",
            "property string libraryName",
            "property string libraryPath",
        ):
            self.assertIn(prop, self.source)
        for shortcut in ("Search", "Commands", "Open book", "Edit metadata", "Send to reader", "Refresh"):
            self.assertIn(f'"{shortcut}"', self.source)
        self.assertIn("signal retryRequested()", self.source)

    def test_help_is_keyboard_dismissible(self) -> None:
        self.assertIn("focus: visible", self.source)
        self.assertIn("Keys.onEscapePressed: root.canceled()", self.source)
        self.assertIn("onVisibleChanged:", self.source)


if __name__ == "__main__":
    unittest.main()
