from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "backend" / "calibre_bridge.py"
TEST_TMP = ROOT / ".tmp" / "tests"


class BridgeProcess:
    def __init__(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, str(BRIDGE)],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def request(self, request: dict) -> list[dict]:
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()

        events: list[dict] = []
        while True:
            line = self.process.stdout.readline()
            if line == "":
                assert self.process.stderr is not None
                error = self.process.stderr.read()
                self.process.wait(timeout=5)
                self.fail(f"bridge exited before a terminal event: {error}")
            event = json.loads(line)
            if event.get("id") != request["id"]:
                continue
            events.append(event)
            if event.get("type") in {"succeeded", "failed", "cancelled"}:
                return events

    def fail(self, message: str) -> None:
        raise AssertionError(message)

    def close(self) -> None:
        if self.process.poll() is None and self.process.stdin is not None:
            self.process.stdin.close()
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)
        if self.process.stdout is not None:
            self.process.stdout.close()
        if self.process.stderr is not None:
            self.process.stderr.close()


class CalibreBridgeContractTest(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TMP.mkdir(parents=True, exist_ok=True)
        self.temp_dir = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.library = Path(self.temp_dir.name) / "Science Fiction"
        self.add_book(
            title="Dune",
            authors="Frank Herbert",
            tags="science fiction,classic",
        )
        self.bridge = BridgeProcess()

    def add_book(self, *, title: str, authors: str, tags: str = "") -> None:
        command = [
            "calibredb",
            "add",
            "--empty",
            "--with-library",
            str(self.library),
            "--title",
            title,
            "--authors",
            authors,
        ]
        if tags:
            command.extend(["--tags", tags])
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )

    def tearDown(self) -> None:
        self.bridge.close()
        self.temp_dir.cleanup()

    def test_bootstrap_returns_a_normalized_first_page(self) -> None:
        events = self.bridge.request(
            {
                "protocol": 1,
                "id": "bootstrap-1",
                "operation": "bootstrap",
                "input": {
                    "rememberedLibraries": [str(self.library)],
                    "pageSize": 50,
                },
            }
        )

        self.assertEqual([event["type"] for event in events], ["accepted", "succeeded"])
        result = events[-1]["result"]
        self.assertTrue(result["calibre"]["available"])
        self.assertRegex(result["calibre"]["version"], r"^9\.")
        self.assertEqual(len(result["libraries"]), 1)
        self.assertEqual(result["libraries"][0]["name"], "Science Fiction")
        self.assertEqual(result["currentLibrary"], result["libraries"][0]["token"])
        self.assertEqual(result["page"]["total"], 1)
        self.assertIsNone(result["page"]["nextCursor"])
        self.assertEqual(
            result["page"]["items"][0],
            {
                "id": 1,
                "title": "Dune",
                "authors": ["Frank Herbert"],
                "authorSort": "Herbert, Frank",
                "series": "",
                "seriesIndex": 1.0,
                "rating": 0,
                "tags": ["science fiction", "classic"],
                "publisher": "",
                "published": "",
                "languages": [],
                "identifiers": {},
                "comments": "",
                "formats": [],
                "cover": "",
                "modified": result["page"]["items"][0]["modified"],
            },
        )
        self.assertIn("book.metadata.update", result["capabilities"]["actions"])
        self.assertIn("book.convert.quick", result["capabilities"]["actions"])

    def test_books_query_searches_sorts_and_pages(self) -> None:
        self.add_book(title="Kindred", authors="Octavia E. Butler")
        self.add_book(title="The Left Hand of Darkness", authors="Ursula K. Le Guin")
        bootstrap = self.bridge.request(
            {
                "protocol": 1,
                "id": "bootstrap-query",
                "operation": "bootstrap",
                "input": {"rememberedLibraries": [str(self.library)]},
            }
        )[-1]["result"]
        library_token = bootstrap["currentLibrary"]

        first_page = self.bridge.request(
            {
                "protocol": 1,
                "id": "query-1",
                "operation": "books.query",
                "library": library_token,
                "input": {
                    "search": "",
                    "sort": "title",
                    "direction": "ascending",
                    "limit": 2,
                },
            }
        )[-1]["result"]
        self.assertEqual([book["title"] for book in first_page["items"]], ["Dune", "Kindred"])
        self.assertEqual(first_page["total"], 3)
        self.assertEqual(first_page["nextCursor"], "2")

        second_page = self.bridge.request(
            {
                "protocol": 1,
                "id": "query-2",
                "operation": "books.query",
                "library": library_token,
                "input": {
                    "search": "",
                    "sort": "title",
                    "direction": "ascending",
                    "limit": 2,
                    "cursor": first_page["nextCursor"],
                },
            }
        )[-1]["result"]
        self.assertEqual([book["title"] for book in second_page["items"]], ["The Left Hand of Darkness"])
        self.assertIsNone(second_page["nextCursor"])

        search_page = self.bridge.request(
            {
                "protocol": 1,
                "id": "query-search",
                "operation": "books.query",
                "library": library_token,
                "input": {"search": "Kindred", "limit": 50},
            }
        )[-1]["result"]
        self.assertEqual([book["title"] for book in search_page["items"]], ["Kindred"])

    def test_metadata_update_returns_the_saved_book(self) -> None:
        bootstrap = self.bridge.request(
            {
                "protocol": 1,
                "id": "bootstrap-metadata",
                "operation": "bootstrap",
                "input": {"rememberedLibraries": [str(self.library)]},
            }
        )[-1]["result"]

        events = self.bridge.request(
            {
                "protocol": 1,
                "id": "metadata-1",
                "operation": "action.run",
                "library": bootstrap["currentLibrary"],
                "input": {
                    "name": "book.metadata.update",
                    "bookId": 1,
                    "fields": {
                        "title": "Dune Messiah",
                        "tags": ["science fiction", "politics"],
                        "rating": 4,
                    },
                },
            }
        )

        self.assertEqual([event["type"] for event in events], ["accepted", "succeeded"])
        book = events[-1]["result"]["book"]
        self.assertEqual(book["title"], "Dune Messiah")
        self.assertEqual(book["tags"], ["science fiction", "politics"])
        self.assertEqual(book["rating"], 4)

        saved = json.loads(
            subprocess.run(
                [
                    "calibredb",
                    "list",
                    "--with-library",
                    str(self.library),
                    "--for-machine",
                    "--fields",
                    "title,tags,rating",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )[0]
        self.assertEqual(saved["title"], "Dune Messiah")
        self.assertEqual(saved["tags"], ["science fiction", "politics"])
        self.assertEqual(saved["rating"], 8.0)

    def test_import_uses_calibre_duplicate_defaults(self) -> None:
        incoming = Path(self.temp_dir.name) / "Parable of the Sower.txt"
        incoming.write_text("All that you touch, you change.\n", encoding="utf-8")
        bootstrap = self.bridge.request(
            {
                "protocol": 1,
                "id": "bootstrap-import",
                "operation": "bootstrap",
                "input": {"rememberedLibraries": [str(self.library)]},
            }
        )[-1]["result"]
        token = bootstrap["currentLibrary"]
        request = {
            "protocol": 1,
            "id": "import-1",
            "operation": "action.run",
            "library": token,
            "input": {
                "name": "books.import",
                "paths": [str(incoming)],
                "duplicatePolicy": "calibre-default",
            },
        }

        first = self.bridge.request(request)[-1]["result"]
        self.assertEqual(len(first["addedIds"]), 1)
        self.assertEqual(first["skipped"], 0)

        request["id"] = "import-2"
        second = self.bridge.request(request)[-1]["result"]
        self.assertEqual(second["addedIds"], [])
        self.assertEqual(second["skipped"], 1)

        books = json.loads(
            subprocess.run(
                [
                    "calibredb",
                    "list",
                    "--with-library",
                    str(self.library),
                    "--for-machine",
                    "--fields",
                    "title",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
        self.assertEqual(len(books), 2)

    def test_format_add_attaches_a_normalized_format(self) -> None:
        source = Path(self.temp_dir.name) / "Dune notes.txt"
        source.write_text("Fear is the mind-killer.\n", encoding="utf-8")
        bootstrap = self.bridge.request(
            {
                "protocol": 1,
                "id": "bootstrap-format-add",
                "operation": "bootstrap",
                "input": {"rememberedLibraries": [str(self.library)]},
            }
        )[-1]["result"]

        events = self.bridge.request(
            {
                "protocol": 1,
                "id": "format-add",
                "operation": "action.run",
                "library": bootstrap["currentLibrary"],
                "input": {
                    "name": "format.add",
                    "bookId": 1,
                    "path": str(source),
                },
            }
        )

        self.assertEqual([event["type"] for event in events], ["accepted", "succeeded"])
        result = events[-1]["result"]
        self.assertFalse(result["replaced"])
        self.assertEqual(result["format"]["name"], "TXT")
        self.assertEqual(result["format"]["size"], source.stat().st_size)
        attached_path = Path(result["format"]["path"])
        self.assertTrue(attached_path.is_file())
        self.assertEqual(attached_path.read_text(encoding="utf-8"), source.read_text(encoding="utf-8"))
        self.assertEqual(result["book"]["formats"], [result["format"]])

    def test_format_replacement_and_removal_require_confirmation(self) -> None:
        original = Path(self.temp_dir.name) / "original.txt"
        replacement = Path(self.temp_dir.name) / "replacement.txt"
        original.write_text("Original edition\n", encoding="utf-8")
        replacement.write_text("Revised edition\n", encoding="utf-8")
        bootstrap = self.bridge.request(
            {
                "protocol": 1,
                "id": "bootstrap-format-change",
                "operation": "bootstrap",
                "input": {"rememberedLibraries": [str(self.library)]},
            }
        )[-1]["result"]
        library_token = bootstrap["currentLibrary"]
        self.bridge.request(
            {
                "protocol": 1,
                "id": "format-original",
                "operation": "action.run",
                "library": library_token,
                "input": {"name": "format.add", "bookId": 1, "path": str(original)},
            }
        )

        unsafe_replace = self.bridge.request(
            {
                "protocol": 1,
                "id": "format-unsafe-replace",
                "operation": "action.run",
                "library": library_token,
                "input": {"name": "format.add", "bookId": 1, "path": str(replacement)},
            }
        )[-1]
        self.assertEqual(unsafe_replace["type"], "failed")
        self.assertEqual(unsafe_replace["error"]["code"], "confirmation_required")

        replace_plan = self.bridge.request(
            {
                "protocol": 1,
                "id": "format-replace-prepare",
                "operation": "action.prepare",
                "library": library_token,
                "input": {"name": "format.replace", "bookId": 1, "path": str(replacement)},
            }
        )[-1]["result"]
        self.assertIn("TXT", replace_plan["summary"])
        replaced = self.bridge.request(
            {
                "protocol": 1,
                "id": "format-replace-commit",
                "operation": "action.commit",
                "input": {"confirmationToken": replace_plan["confirmationToken"]},
            }
        )[-1]["result"]
        replaced_path = Path(replaced["format"]["path"])
        self.assertTrue(replaced["replaced"])
        self.assertEqual(replaced_path.read_text(encoding="utf-8"), "Revised edition\n")

        remove_plan = self.bridge.request(
            {
                "protocol": 1,
                "id": "format-remove-prepare",
                "operation": "action.prepare",
                "library": library_token,
                "input": {"name": "format.remove", "bookId": 1, "format": "TXT"},
            }
        )[-1]["result"]
        self.assertIn("TXT", remove_plan["summary"])
        removed = self.bridge.request(
            {
                "protocol": 1,
                "id": "format-remove-commit",
                "operation": "action.commit",
                "input": {"confirmationToken": remove_plan["confirmationToken"]},
            }
        )[-1]["result"]
        self.assertEqual(removed["removedFormat"], "TXT")
        self.assertEqual(removed["book"]["formats"], [])

    def test_export_stages_calibre_default_output_before_publishing_it(self) -> None:
        source = Path(self.temp_dir.name) / "dune.txt"
        source.write_text("A beginning is the time for taking care.\n", encoding="utf-8")
        destination = Path(self.temp_dir.name) / "Exported books"
        bootstrap = self.bridge.request(
            {
                "protocol": 1,
                "id": "bootstrap-export",
                "operation": "bootstrap",
                "input": {"rememberedLibraries": [str(self.library)]},
            }
        )[-1]["result"]
        library_token = bootstrap["currentLibrary"]
        self.bridge.request(
            {
                "protocol": 1,
                "id": "format-for-export",
                "operation": "action.run",
                "library": library_token,
                "input": {"name": "format.add", "bookId": 1, "path": str(source)},
            }
        )

        events = self.bridge.request(
            {
                "protocol": 1,
                "id": "export-defaults",
                "operation": "action.run",
                "library": library_token,
                "input": {
                    "name": "book.export",
                    "bookIds": [1],
                    "destination": str(destination),
                },
            }
        )

        self.assertEqual([event["type"] for event in events], ["accepted", "succeeded"])
        result = events[-1]["result"]
        self.assertEqual(result["destination"], str(destination.resolve()))
        exported = [Path(item["path"]) for item in result["files"]]
        self.assertTrue(exported)
        self.assertTrue(all(path.is_file() for path in exported))
        self.assertTrue(all(path.is_relative_to(destination) for path in exported))
        self.assertTrue(any(path.suffix.lower() == ".txt" for path in exported))
        self.assertTrue(any(path.suffix.lower() == ".opf" for path in exported))
        self.assertFalse(any(path.name.startswith(".omarchy-calibre-export-") for path in destination.iterdir()))

    def test_export_requires_fresh_confirmation_before_replacing_files(self) -> None:
        source = Path(self.temp_dir.name) / "dune.txt"
        source.write_text("Library edition\n", encoding="utf-8")
        destination = Path(self.temp_dir.name) / "Exported books"
        bootstrap = self.bridge.request(
            {
                "protocol": 1,
                "id": "bootstrap-export-replace",
                "operation": "bootstrap",
                "input": {"rememberedLibraries": [str(self.library)]},
            }
        )[-1]["result"]
        library_token = bootstrap["currentLibrary"]
        self.bridge.request(
            {
                "protocol": 1,
                "id": "format-for-export-replace",
                "operation": "action.run",
                "library": library_token,
                "input": {"name": "format.add", "bookId": 1, "path": str(source)},
            }
        )
        export_input = {
            "name": "book.export",
            "bookIds": [1],
            "destination": str(destination),
        }
        first = self.bridge.request(
            {
                "protocol": 1,
                "id": "export-first",
                "operation": "action.run",
                "library": library_token,
                "input": export_input,
            }
        )[-1]["result"]
        exported_txt = next(Path(item["path"]) for item in first["files"] if item["path"].endswith(".txt"))
        exported_txt.write_text("Keep this copy until confirmed.\n", encoding="utf-8")

        unsafe = self.bridge.request(
            {
                "protocol": 1,
                "id": "export-unsafe-replace",
                "operation": "action.run",
                "library": library_token,
                "input": export_input,
            }
        )[-1]
        self.assertEqual(unsafe["type"], "failed")
        self.assertEqual(unsafe["error"]["code"], "confirmation_required")
        self.assertEqual(exported_txt.read_text(encoding="utf-8"), "Keep this copy until confirmed.\n")

        prepared = self.bridge.request(
            {
                "protocol": 1,
                "id": "export-replace-prepare",
                "operation": "action.prepare",
                "library": library_token,
                "input": {**export_input, "name": "book.export.replace"},
            }
        )[-1]["result"]
        self.assertIn("existing", prepared["summary"].lower())
        committed = self.bridge.request(
            {
                "protocol": 1,
                "id": "export-replace-commit",
                "operation": "action.commit",
                "input": {"confirmationToken": prepared["confirmationToken"]},
            }
        )[-1]["result"]
        self.assertEqual(committed["destination"], str(destination.resolve()))
        self.assertEqual(exported_txt.read_text(encoding="utf-8"), "Library edition\n")

    def test_quick_conversion_uses_calibre_defaults_and_attaches_the_result(self) -> None:
        source = Path(self.temp_dir.name) / "dune.txt"
        source.write_text("Dune\n\nA novel by Frank Herbert.\n", encoding="utf-8")
        bootstrap = self.bridge.request(
            {
                "protocol": 1,
                "id": "bootstrap-convert",
                "operation": "bootstrap",
                "input": {"rememberedLibraries": [str(self.library)]},
            }
        )[-1]["result"]
        library_token = bootstrap["currentLibrary"]
        self.bridge.request(
            {
                "protocol": 1,
                "id": "format-for-convert",
                "operation": "action.run",
                "library": library_token,
                "input": {"name": "format.add", "bookId": 1, "path": str(source)},
            }
        )

        events = self.bridge.request(
            {
                "protocol": 1,
                "id": "convert-quick",
                "operation": "action.run",
                "library": library_token,
                "input": {
                    "name": "book.convert.quick",
                    "bookId": 1,
                    "outputFormat": "EPUB",
                },
            }
        )

        self.assertEqual([event["type"] for event in events], ["accepted", "succeeded"])
        result = events[-1]["result"]
        self.assertEqual(result["inputFormat"], "TXT")
        self.assertEqual(result["outputFormat"], "EPUB")
        self.assertEqual(result["format"]["name"], "EPUB")
        self.assertTrue(Path(result["format"]["path"]).is_file())
        self.assertGreater(result["format"]["size"], 0)
        self.assertEqual({item["name"] for item in result["book"]["formats"]}, {"TXT", "EPUB"})

    def test_book_removal_requires_a_single_use_confirmation(self) -> None:
        bootstrap = self.bridge.request(
            {
                "protocol": 1,
                "id": "bootstrap-remove",
                "operation": "bootstrap",
                "input": {"rememberedLibraries": [str(self.library)]},
            }
        )[-1]["result"]
        token = bootstrap["currentLibrary"]

        prepared = self.bridge.request(
            {
                "protocol": 1,
                "id": "remove-prepare",
                "operation": "action.prepare",
                "library": token,
                "input": {"name": "book.remove", "bookIds": [1]},
            }
        )[-1]["result"]
        self.assertIn("Dune", prepared["summary"])
        self.assertTrue(prepared["confirmationToken"])

        before_commit = self.bridge.request(
            {
                "protocol": 1,
                "id": "query-before-remove",
                "operation": "books.query",
                "library": token,
                "input": {},
            }
        )[-1]["result"]
        self.assertEqual(before_commit["total"], 1)

        committed = self.bridge.request(
            {
                "protocol": 1,
                "id": "remove-commit",
                "operation": "action.commit",
                "input": {"confirmationToken": prepared["confirmationToken"]},
            }
        )[-1]["result"]
        self.assertEqual(committed["removedIds"], [1])

        after_commit = self.bridge.request(
            {
                "protocol": 1,
                "id": "query-after-remove",
                "operation": "books.query",
                "library": token,
                "input": {},
            }
        )[-1]["result"]
        self.assertEqual(after_commit["items"], [])

        replay = self.bridge.request(
            {
                "protocol": 1,
                "id": "remove-replay",
                "operation": "action.commit",
                "input": {"confirmationToken": prepared["confirmationToken"]},
            }
        )[-1]
        self.assertEqual(replay["type"], "failed")
        self.assertEqual(replay["error"]["code"], "confirmation_required")


if __name__ == "__main__":
    unittest.main()
