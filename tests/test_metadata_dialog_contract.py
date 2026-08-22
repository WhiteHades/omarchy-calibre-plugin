import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MetadataDownloadDialogContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (ROOT / "MetadataDownloadDialog.qml").read_text()

    def test_exposes_the_review_dialog_contract(self) -> None:
        for declaration in (
            "property var book",
            "property var preview",
            "property bool loading",
            "property bool applying",
            'property string error: ""',
            "property color foreground",
            "property color urgent",
            "property string fontFamily",
            "signal fetchRequested()",
            "signal applyRequested(var selectedFields, bool includeCover)",
            "signal retryRequested()",
            "signal cancelJobRequested()",
            "signal discarded()",
            "signal canceled()",
        ):
            self.assertIn(declaration, self.source)

    def test_review_keeps_selection_explicit_and_preserves_untouched_fields(self) -> None:
        self.assertIn('var allowed = [', self.source)
        for field in ("title", "authors", "tags", "series", "publisher", "published", "languages", "identifiers", "comments"):
            self.assertIn(f'"{field}"', self.source)
        self.assertIn("function selectedValues()", self.source)
        self.assertIn("if (isFieldSelected(field.key)) values[field.key] = field.proposed", self.source)
        self.assertIn("applyRequested(selectedValues(), coverSelection && coverInfo !== null)", self.source)

    def test_review_has_recovery_and_keyboard_paths(self) -> None:
        self.assertIn("function dismiss()", self.source)
        self.assertIn("if (hasReview) discarded()", self.source)
        self.assertIn("cancelJobRequested()", self.source)
        self.assertIn("retryRequested()", self.source)
        self.assertIn("Qt.Key_Escape", self.source)
        self.assertRegex(self.source, r"CalibreIcon\s*\{")

    def test_inactive_states_do_not_render_outside_zero_height_containers(self) -> None:
        for state in (
            "fetchState",
            "loadingState",
            "errorState",
            "noResultState",
            "reviewState",
            "applyingState",
        ):
            block = re.search(rf"id: {state}\n(?P<body>.*?)(?=\n        \}})", self.source, re.DOTALL)
            self.assertIsNotNone(block, state)
            self.assertIn("visible:", block.group("body"), state)
            self.assertIn("height: visible ? implicitHeight : 0", block.group("body"), state)


if __name__ == "__main__":
    unittest.main()
