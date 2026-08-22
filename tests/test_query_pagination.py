from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from typing import Any

from backend.calibre_bridge import BOOK_FIELDS, CalibreBridge


class QueryFixtureBridge(CalibreBridge):
    def __init__(self) -> None:
        super().__init__()
        self.libraries["library"] = Path("/tmp/calibre-library")
        self.calls: list[list[str]] = []

    def run(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        fields = command[command.index("--fields") + 1]
        if fields == "id":
            rows = [{"id": 3}, {"id": 1}, {"id": 4}, {"id": 2}]
        else:
            rows = [
                {"id": 3, "title": "A Wizard of Earthsea"},
                {"id": 1, "title": "Dune"},
            ]
        return subprocess.CompletedProcess(command, 0, json.dumps(rows), "")


class QueryPaginationTest(unittest.TestCase):
    def test_zero_series_index_survives_book_normalization(self) -> None:
        book = CalibreBridge.normalize_book({"id": 1, "series_index": 0})

        self.assertEqual(book["seriesIndex"], 0.0)

    def test_only_the_requested_page_materializes_full_book_records(self) -> None:
        bridge = QueryFixtureBridge()

        page = bridge.query_books(
            "library",
            {
                "search": "tags:fantasy",
                "sort": "title",
                "direction": "ascending",
                "limit": 2,
            },
        )

        self.assertEqual(page["total"], 4)
        self.assertEqual(page["nextCursor"], "2")
        self.assertEqual([book["id"] for book in page["items"]], [3, 1])
        self.assertEqual(len(bridge.calls), 2)

        index_call, detail_call = bridge.calls
        self.assertEqual(index_call[index_call.index("--fields") + 1], "id")
        self.assertNotIn("--limit", index_call)
        self.assertEqual(detail_call[detail_call.index("--fields") + 1], BOOK_FIELDS)
        self.assertEqual(detail_call[detail_call.index("--limit") + 1], "2")
        detail_search = detail_call[detail_call.index("--search") + 1]
        self.assertIn("tags:fantasy", detail_search)
        self.assertIn("id:3", detail_search)
        self.assertIn("id:1", detail_search)

    def test_bootstrap_forwards_the_visible_query_state(self) -> None:
        class BootstrapFixtureBridge(CalibreBridge):
            def __init__(self) -> None:
                super().__init__()
                self.query_input: dict[str, Any] | None = None

            def calibre_info(self) -> dict[str, Any]:
                return {
                    "available": True,
                    "installed": True,
                    "supported": True,
                    "status": "ready",
                    "version": "9.4.0",
                    "missingCommands": [],
                }

            def register_library(self, candidate: Any) -> dict[str, str] | None:
                return {"token": "library", "name": "Library", "path": str(candidate)}

            def query_books(self, library_token: str, input_data: dict[str, Any]) -> dict[str, Any]:
                self.query_input = input_data
                return self.empty_page()

            def capabilities(self) -> dict[str, Any]:
                return {"actions": []}

        bridge = BootstrapFixtureBridge()
        bridge.bootstrap(
            {
                "rememberedLibraries": ["library"],
                "pageSize": 40,
                "search": "formats:=EPUB",
                "sort": "rating",
                "direction": "descending",
            }
        )

        self.assertEqual(
            bridge.query_input,
            {
                "limit": 40,
                "search": "formats:=EPUB",
                "sort": "rating",
                "direction": "descending",
            },
        )


if __name__ == "__main__":
    unittest.main()
