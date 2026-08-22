from __future__ import annotations

import unittest
from typing import Any

from backend.calibre_bridge import CalibreBridge
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


class DeviceDestinationTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
