import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeviceDialogContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (ROOT / "DeviceDialog.qml").read_text()

    def test_dialog_uses_native_surface_and_calibre_identity(self) -> None:
        self.assertIn("BorderSurface {", self.source)
        self.assertIn("CalibreIcon {", self.source)
        self.assertIn("PanelSeparator {", self.source)
        self.assertIn("Dropdown {", self.source)
        self.assertIn("Button {", self.source)

    def test_reader_states_and_actions_are_explicit(self) -> None:
        for state in ("probing", "no-device", "locked", "error", "ready", "sending", "sent", "conflict", "ejected"):
            self.assertIn('"' + state + '"', self.source)
        for signal in (
            "sendRequested",
            "retryRequested",
            "ejectRequested",
            "cancelRequested",
            "canceled",
        ):
            self.assertIn("signal " + signal, self.source)

    def test_format_picker_is_preferred_and_does_not_render_device_paths(self) -> None:
        self.assertIn("preferredFormats", self.source)
        self.assertIn("selectedFormat", self.source)
        self.assertIn("options: root.formatOptions", self.source)
        self.assertNotIn("/Books/", self.source)
        self.assertNotIn("dev:", self.source)
        self.assertNotIn("destination", self.source)

    def test_existing_reader_file_requires_an_explicit_replace_retry(self) -> None:
        self.assertIn("signal sendRequested(string format, bool force)", self.source)
        self.assertIn('normalizedState === "conflict"', self.source)
        self.assertIn('text: root.normalizedState === "conflict" ? "Replace"', self.source)
        self.assertIn('root.sendRequested(root.selectedFormat, root.normalizedState === "conflict")', self.source)

    def test_keyboard_escape_and_focusable_actions_are_present(self) -> None:
        self.assertIn("focus: visible", self.source)
        self.assertIn("Keys.onEscapePressed", self.source)
        self.assertGreaterEqual(self.source.count("focusable: true"), 4)


if __name__ == "__main__":
    unittest.main()
