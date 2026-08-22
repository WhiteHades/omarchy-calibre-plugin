import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MetadataEditorContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (ROOT / "MetadataEditor.qml").read_text()

    def test_metadata_lookup_is_available_inside_the_editor(self) -> None:
        self.assertIn("property bool downloadAvailable: false", self.source)
        self.assertIn("signal downloadRequested()", self.source)
        self.assertIn("id: downloadButton", self.source)
        self.assertIn('text: "Fetch metadata"', self.source)
        self.assertIn("visible: root.downloadAvailable", self.source)
        self.assertIn("onClicked: root.downloadRequested()", self.source)

    def test_editor_can_close_from_the_keyboard(self) -> None:
        self.assertIn("focus: visible", self.source)
        self.assertIn("Keys.onEscapePressed: root.canceled()", self.source)
        self.assertIn("onVisibleChanged:", self.source)


if __name__ == "__main__":
    unittest.main()
