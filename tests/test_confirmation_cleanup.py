from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from backend.calibre_bridge import CalibreBridge


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


if __name__ == "__main__":
    unittest.main()
