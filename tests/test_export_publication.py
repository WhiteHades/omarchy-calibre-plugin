from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.calibre_bridge import BridgeError, CalibreBridge


class ExportPublicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.staging = self.root / "staging"
        self.destination = self.root / "destination"
        self.staging.mkdir()
        self.destination.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_new_target_created_during_publication_is_not_overwritten(self) -> None:
        source = self.staging / "book.epub"
        source.write_bytes(b"staged")
        target = self.destination / source.name
        original_publish = CalibreBridge.atomic_rename_noreplace
        raced = False

        def racing_publish(parent_fd, temporary_name, target_name):
            nonlocal raced
            if not raced:
                raced = True
                target.write_bytes(b"created concurrently")
            return original_publish(parent_fd, temporary_name, target_name)

        with patch.object(CalibreBridge, "atomic_rename_noreplace", side_effect=racing_publish):
            with self.assertRaises(BridgeError) as raised:
                CalibreBridge.publish_staged_export(
                    self.staging,
                    self.destination,
                    replace=False,
                )

        self.assertEqual(raised.exception.code, "confirmation_required")
        self.assertEqual(target.read_bytes(), b"created concurrently")

    def test_new_target_is_not_visible_until_its_contents_are_complete(self) -> None:
        source = self.staging / "book.epub"
        source.write_bytes(b"staged")
        target = self.destination / source.name

        def fail_during_copy(source_path, destination_fd):
            self.assertFalse(target.exists())
            os.write(destination_fd, b"partial")
            raise OSError("copy interrupted")

        with patch.object(CalibreBridge, "copy_export_contents", side_effect=fail_during_copy):
            with self.assertRaises(BridgeError):
                CalibreBridge.publish_staged_export(
                    self.staging,
                    self.destination,
                    replace=False,
                )

        self.assertFalse(target.exists())
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_confirmed_target_changed_at_publication_is_restored(self) -> None:
        source = self.staging / "book.epub"
        source.write_bytes(b"staged")
        target = self.destination / source.name
        target.write_bytes(b"confirmed original")
        revisions = {Path(source.name): CalibreBridge.file_revision(target)}
        original_exchange = CalibreBridge.atomic_exchange_export_file
        raced = False

        def racing_exchange(parent_fd, temporary_name, target_name):
            nonlocal raced
            if not raced:
                raced = True
                target.write_bytes(b"changed after final check")
            return original_exchange(parent_fd, temporary_name, target_name)

        with patch.object(CalibreBridge, "atomic_exchange_export_file", side_effect=racing_exchange):
            with self.assertRaises(BridgeError) as raised:
                CalibreBridge.publish_staged_export(
                    self.staging,
                    self.destination,
                    replace=True,
                    target_revisions=revisions,
                )

        self.assertEqual(raised.exception.code, "confirmation_stale")
        self.assertEqual(target.read_bytes(), b"changed after final check")

    def test_symlinked_parent_cannot_escape_the_export_destination(self) -> None:
        nested = self.staging / "Author"
        nested.mkdir()
        (nested / "book.epub").write_bytes(b"staged")
        outside = self.root / "outside"
        outside.mkdir()
        (self.destination / "Author").symlink_to(outside, target_is_directory=True)

        with self.assertRaises(BridgeError) as raised:
            CalibreBridge.publish_staged_export(
                self.staging,
                self.destination,
                replace=False,
            )

        self.assertEqual(raised.exception.code, "invalid_request")
        self.assertFalse((outside / "book.epub").exists())


if __name__ == "__main__":
    unittest.main()
