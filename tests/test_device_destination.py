from __future__ import annotations

import copy
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from backend.calibre_bridge import BridgeError, CalibreBridge
from backend.device_adapter import DeviceError


class ListingDevice:
    def __init__(self, entries: list[dict[str, Any]] | None = None, *, error: DeviceError | None = None) -> None:
        self.entries = entries or []
        self.error = error
        self.calls: list[str] = []

    def list(self, path: str = "/", *, recursive: bool = False) -> dict[str, Any]:
        self.calls.append(path)
        if self.error is not None:
            raise self.error
        return {"path": path, "entries": self.entries}


class ConfirmationDevice(ListingDevice):
    def __init__(self) -> None:
        super().__init__()
        self.target = {
            "name": "Dune - Frank Herbert.epub",
            "path": "/Books/Dune - Frank Herbert.epub",
            "isDirectory": False,
            "size": 2048,
            "mode": "-rw-r--r--",
            "modified": "2026-08-22 19:04",
        }
        self.identity = {
            "deviceName": "Kobo Clara",
            "deviceVersion": "1",
            "softwareVersion": "4.38",
            "mimeType": "application/x-kobo",
        }
        self.target_content = b"A" * 2048
        self.sends: list[tuple[str, bool]] = []

    def list(self, path: str = "/", *, recursive: bool = False) -> dict[str, Any]:
        self.calls.append(path)
        if path == "/":
            entries = [{"name": "Books", "path": "/Books", "isDirectory": True}]
        elif path == "/Books":
            entries = [copy.deepcopy(self.target)]
        else:
            entries = []
        return {"path": path, "entries": entries}

    def info(self) -> dict[str, str]:
        return dict(self.identity)

    def send(self, source: Path, destination: str, *, force: bool = False) -> dict[str, Any]:
        self.sends.append((destination, force))
        if not force:
            raise DeviceError("destination_exists", "This book already exists on the ebook reader")
        return {"source": str(source), "destination": destination, "replaced": True}

    def receive(self, source: str, destination: Path) -> dict[str, str]:
        Path(destination).write_bytes(self.target_content)
        return {"source": source, "destination": str(destination)}


class DeviceConfirmationBridge(CalibreBridge):
    def __init__(self, root: Path, device: ConfirmationDevice) -> None:
        super().__init__(device_adapter=device)  # type: ignore[arg-type]
        self.root = root
        self.book = {
            "id": 1,
            "title": "Dune",
            "authors": ["Frank Herbert"],
            "modified": "revision-1",
            "formats": [{"name": "EPUB", "path": str(root / "Dune.epub")}],
        }

    def require_library(self, library_token: object) -> Path:
        return self.root

    def get_book(self, library_token: str, book_id: int) -> dict[str, Any]:
        return copy.deepcopy(self.book)

    def run(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        destination = Path(command[command.index("--to-dir") + 1])
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "Dune.epub").write_bytes(b"staged epub")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


class DeviceDestinationTest(unittest.TestCase):
    def test_device_paths_are_normalized_and_parented_across_storage_roots(self) -> None:
        self.assertEqual(
            CalibreBridge.require_device_destination("dev:/Books/Dune.epub"),
            "/Books/Dune.epub",
        )
        self.assertEqual(CalibreBridge.device_parent_path("/Dune.epub"), "/")
        self.assertEqual(CalibreBridge.device_parent_path("/Books/Dune.epub"), "/Books")
        self.assertEqual(CalibreBridge.device_parent_path("carda:/Dune.epub"), "carda:/")
        self.assertEqual(
            CalibreBridge.device_parent_path("cardb:/Books/Dune.epub"),
            "cardb:/Books",
        )

    def test_prefers_the_existing_books_folder_and_uses_calibres_common_filename(self) -> None:
        device = ListingDevice(
            [
                {"name": "Documents", "path": "/Documents", "isDirectory": True},
                {"name": "Books", "path": "/Books", "isDirectory": True},
            ]
        )
        bridge = CalibreBridge(device_adapter=device)  # type: ignore[arg-type]

        destination = bridge.default_device_destination(
            {"title": "Dune", "authors": ["Frank Herbert"]},
            "EPUB",
        )

        self.assertEqual(destination, "/Books/Dune - Frank Herbert.epub")
        self.assertEqual(device.calls, ["/"])

    def test_falls_back_to_the_reader_root_when_no_common_book_folder_exists(self) -> None:
        device = ListingDevice(
            [{"name": "Pictures", "path": "/Pictures", "isDirectory": True}]
        )
        bridge = CalibreBridge(device_adapter=device)  # type: ignore[arg-type]

        destination = bridge.default_device_destination(
            {"title": "Kindred", "authors": ["Octavia E. Butler"]},
            "PDF",
        )

        self.assertEqual(destination, "/Kindred - Octavia E. Butler.pdf")

    def test_unsupported_listing_still_allows_a_root_transfer(self) -> None:
        device = ListingDevice(error=DeviceError("unsupported", "Listing is unavailable"))
        bridge = CalibreBridge(device_adapter=device)  # type: ignore[arg-type]

        destination = bridge.default_device_destination(
            {"title": "The Left Hand of Darkness", "authors": ["Ursula K. Le Guin"]},
            "AZW3",
        )

        self.assertEqual(destination, "/The Left Hand of Darkness - Ursula K. Le Guin.azw3")

    def test_generated_filename_is_safe_for_common_reader_filesystems(self) -> None:
        device = ListingDevice()
        bridge = CalibreBridge(device_adapter=device)  # type: ignore[arg-type]

        destination = bridge.default_device_destination(
            {
                "title": "  A/B: <Very> ? Long* Title  ",
                "authors": ["An\\Author", "Another|Author"],
            },
            "EPUB",
        )

        filename = destination.removeprefix("/")
        self.assertTrue(filename.endswith(".epub"))
        self.assertFalse(any(character in filename for character in '<>:"/\\|?*'))
        self.assertLessEqual(len(filename.encode("utf-8")), 240)
        self.assertNotIn("  ", filename)


class DeviceReplacementConfirmationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.device = ConfirmationDevice()
        self.bridge = DeviceConfirmationBridge(self.root, self.device)

    def tearDown(self) -> None:
        self.bridge.close()
        self.temporary.cleanup()

    def prepare_replacement(self) -> tuple[str, Path]:
        with self.assertRaises(BridgeError) as raised:
            self.bridge.device_send("library-token", {"bookId": 1, "format": "EPUB"})
        self.assertEqual(raised.exception.code, "destination_exists")
        payload = raised.exception.as_dict()
        token = payload["confirmationToken"]
        plan = self.bridge.confirmations[token]
        self.assertEqual(plan["destination"], self.device.target["path"])
        self.assertTrue(plan["staging"].is_dir())
        return token, plan["staging"]

    def test_direct_force_is_rejected_and_replace_requires_a_one_use_token(self) -> None:
        with self.assertRaises(BridgeError) as raised:
            self.bridge.device_send(
                "library-token",
                {"bookId": 1, "format": "EPUB", "force": True},
            )
        self.assertEqual(raised.exception.code, "confirmation_required")

        token, staging = self.prepare_replacement()
        self.assertEqual(
            self.bridge.scheduling_key({
                "operation": "action.commit",
                "input": {"confirmationToken": token},
            }),
            ("device", "calibre"),
        )
        result = self.bridge.action_commit({"confirmationToken": token})

        self.assertTrue(result["replaced"])
        self.assertEqual(result["destination"], "/Books/Dune - Frank Herbert.epub")
        self.assertEqual(self.device.sends[-1], ("/Books/Dune - Frank Herbert.epub", True))
        self.assertFalse(staging.exists())

    def test_changed_book_or_device_invalidates_the_replacement(self) -> None:
        token, staging = self.prepare_replacement()
        self.bridge.book["title"] = "Dune Messiah"
        self.bridge.book["modified"] = "revision-2"

        with self.assertRaises(BridgeError) as raised:
            self.bridge.action_commit({"confirmationToken": token})
        self.assertEqual(raised.exception.code, "confirmation_stale")
        self.assertFalse(staging.exists())
        self.assertFalse(any(force for _, force in self.device.sends))

        self.bridge.book["title"] = "Dune"
        self.bridge.book["modified"] = "revision-1"
        token, staging = self.prepare_replacement()
        self.device.identity["deviceName"] = "Different reader"

        with self.assertRaises(BridgeError) as raised:
            self.bridge.action_commit({"confirmationToken": token})
        self.assertEqual(raised.exception.code, "confirmation_stale")
        self.assertFalse(staging.exists())
        self.assertFalse(any(force for _, force in self.device.sends))

    def test_changed_reader_copy_invalidates_the_replacement(self) -> None:
        token, staging = self.prepare_replacement()
        self.device.target_content = b"B" * 2048

        with self.assertRaises(BridgeError) as raised:
            self.bridge.action_commit({"confirmationToken": token})

        self.assertEqual(raised.exception.code, "confirmation_stale")
        self.assertFalse(staging.exists())
        self.assertFalse(any(force for _, force in self.device.sends))

    def test_case_insensitive_conflict_uses_the_readers_canonical_path(self) -> None:
        canonical = "/Books/dune - frank herbert.epub"
        self.device.target["name"] = "dune - frank herbert.epub"
        self.device.target["path"] = canonical

        token, _ = self.prepare_replacement()
        result = self.bridge.action_commit({"confirmationToken": token})

        self.assertEqual(result["destination"], canonical)
        self.assertEqual(self.device.sends[-1], (canonical, True))

    def test_discard_expiry_and_shutdown_remove_retained_reader_staging(self) -> None:
        token, staging = self.prepare_replacement()
        self.assertEqual(
            self.bridge.action_discard({"confirmationToken": token}),
            {"discarded": True},
        )
        self.assertFalse(staging.exists())

        token, staging = self.prepare_replacement()
        self.bridge.confirmations[token]["expires"] = 0
        self.bridge.prune_confirmations()
        self.assertFalse(staging.exists())

        _, staging = self.prepare_replacement()
        self.bridge.close()
        self.assertFalse(staging.exists())


if __name__ == "__main__":
    unittest.main()
