from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from backend.calibre_bridge import BridgeError, CalibreBridge


ROOT = Path(__file__).resolve().parents[1]
TEST_TMP = ROOT / ".tmp" / "tests"


class ConfirmationCleanupTest(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TMP.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.bridge = CalibreBridge()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def staged_plan(self, token: str, *, expired: bool = False) -> Path:
        staging = Path(self.temporary.name) / token
        staging.mkdir()
        (staging / "book.epub").write_bytes(b"book")
        self.bridge.confirmations[token] = {
            "expires": time.monotonic() - 1 if expired else time.monotonic() + 60,
            "name": "book.export.replace",
            "staging": staging,
        }
        return staging

    def test_discard_is_idempotent_and_removes_staged_files(self) -> None:
        staging = self.staged_plan("discard-me")
        request = {
            "protocol": 1,
            "id": "discard-1",
            "operation": "action.discard",
            "input": {"confirmationToken": "discard-me"},
        }

        first = self.bridge.handle(request)[-1]
        request["id"] = "discard-2"
        second = self.bridge.handle(request)[-1]

        self.assertEqual(first["type"], "succeeded")
        self.assertEqual(first["result"], {"discarded": True})
        self.assertFalse(staging.exists())
        self.assertEqual(second["type"], "succeeded")
        self.assertEqual(second["result"], {"discarded": False})

    def test_pruning_removes_expired_confirmation_staging(self) -> None:
        staging = self.staged_plan("expired", expired=True)

        removed = self.bridge.prune_confirmations()

        self.assertEqual(removed, 1)
        self.assertFalse(staging.exists())
        self.assertNotIn("expired", self.bridge.confirmations)

    def test_bridge_shutdown_removes_all_confirmation_staging(self) -> None:
        first = self.staged_plan("first")
        second = self.staged_plan("second")

        self.bridge.close()

        self.assertEqual(self.bridge.confirmations, {})
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())

    def test_export_commit_rejects_a_collision_created_after_confirmation(self) -> None:
        destination = Path(self.temporary.name) / "destination"
        destination.mkdir()
        existing = destination / "existing.txt"
        existing.write_text("old", encoding="utf-8")

        class ExportFixtureBridge(CalibreBridge):
            def export_request(self, library_token, input_data):
                return [1], destination

            def stage_export(self, library, book_ids):
                staging = Path(self.temporary_root) / "staging"
                staging.mkdir()
                (staging / "existing.txt").write_text("new", encoding="utf-8")
                (staging / "appears-later.txt").write_text("staged", encoding="utf-8")
                return staging

        bridge = ExportFixtureBridge()
        bridge.temporary_root = self.temporary.name
        prepared = bridge.prepare_export_replace("library", Path("library"), {})
        appeared = destination / "appears-later.txt"
        appeared.write_text("created after confirmation", encoding="utf-8")

        with self.assertRaises(BridgeError) as raised:
            bridge.action_commit({"confirmationToken": prepared["confirmationToken"]})

        self.assertEqual(raised.exception.code, "confirmation_stale")
        self.assertEqual(appeared.read_text(encoding="utf-8"), "created after confirmation")
        self.assertEqual(existing.read_text(encoding="utf-8"), "old")

    def test_export_preparation_failure_removes_unregistered_staging(self) -> None:
        destination = Path(self.temporary.name) / "destination"
        destination.mkdir()
        target = destination / "book.epub"
        target.write_bytes(b"old")

        class RevisionFailureBridge(CalibreBridge):
            def __init__(self, root):
                super().__init__()
                self.staging = root / "staging-failure"

            def export_request(self, library_token, input_data):
                return [1], destination

            def stage_export(self, library, book_ids):
                self.staging.mkdir()
                (self.staging / "book.epub").write_bytes(b"new")
                return self.staging

            def file_revision(self, path):
                if path == target:
                    raise BridgeError("file_unavailable", "target disappeared")
                return super().file_revision(path)

        bridge = RevisionFailureBridge(Path(self.temporary.name))

        with self.assertRaises(BridgeError):
            bridge.prepare_export_replace("library", Path("library"), {})

        self.assertFalse(bridge.staging.exists())
        self.assertEqual(bridge.confirmations, {})


if __name__ == "__main__":
    unittest.main()
