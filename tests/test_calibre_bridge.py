from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "backend" / "calibre_bridge.py"
TEST_TMP = ROOT / ".tmp" / "tests"


class BridgeProcess:
    def __init__(self, *, env: dict[str, str] | None = None) -> None:
        self.process = subprocess.Popen(
            [sys.executable, str(BRIDGE)],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
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
        calibre_config = Path(self.temp_dir.name) / "calibre-config"
        calibre_config.mkdir()
        self.environment = {
            **os.environ,
            "CALIBRE_CONFIG_DIRECTORY": str(calibre_config),
        }
        self.library = Path(self.temp_dir.name) / "Science Fiction"
        self.add_book(
            title="Dune",
            authors="Frank Herbert",
            tags="science fiction,classic",
        )
        self.bridge = BridgeProcess(env=self.environment)

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
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=self.environment,
            timeout=30,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"calibredb setup failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )

    def tearDown(self) -> None:
        self.bridge.close()
        self.temp_dir.cleanup()

    def assert_successful_lifecycle(self, events: list[dict]) -> None:
        self.assertEqual(events[0]["type"], "accepted")
        self.assertEqual(events[-1]["type"], "succeeded")
        self.assertTrue(any(event["type"] == "progress" for event in events))
        self.assertEqual(
            [event["sequence"] for event in events],
            list(range(len(events))),
        )

    def start_device_bridge(self, *, info: str | None = None) -> Path:
        self.bridge.close()
        device_bin = Path(self.temp_dir.name) / "device-bin"
        device_bin.mkdir(exist_ok=True)
        log = Path(self.temp_dir.name) / "ebook-device.log"
        device_script = device_bin / "ebook-device"
        info_output = info or (
            "Device name:      Kobo Clara\n"
            "Device version:   1.0\n"
            "Software version: 4.38\n"
            "Mime type:        application/x-kobo\n"
        )
        device_script.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$DEVICE_LOG\"\n"
            "if [ \"$1\" = \"--version\" ]; then\n"
            "  printf '%s\\n' 'calibre version: 9.4.0'\n"
            "elif [ \"$#\" -eq 0 ]; then\n"
            "  printf '%s\\n' 'Usage: ebook-device [options] command args'\n"
            "  printf '%s\\n' 'command is one of: info, books, df, ls, cp, mkdir, touch, cat, rm, eject, test_file'\n"
            "elif [ \"$1\" = \"info\" ]; then\n"
            f"  printf '%b' {json.dumps(info_output)}\n"
            "elif [ \"$1\" = \"ls\" ]; then\n"
            "  printf '%s\\n' 'drwxr-xr-x 0 2026-08-22 10:11 Books'\n"
            "elif [ \"$1\" = \"cp\" ]; then\n"
            "  exit 0\n"
            "elif [ \"$1\" = \"eject\" ]; then\n"
            "  exit 0\n"
            "fi\n",
            encoding="utf-8",
        )
        device_script.chmod(0o755)
        self.bridge = BridgeProcess(
            env={
                **self.environment,
                "PATH": f"{device_bin}{os.pathsep}{os.environ['PATH']}",
                "DEVICE_LOG": str(log),
            }
        )
        return log

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

        self.assert_successful_lifecycle(events)
        result = events[-1]["result"]
        self.assertTrue(result["calibre"]["available"])
        self.assertRegex(result["calibre"]["version"], r"^9\.")
        self.assertEqual(result["readiness"]["state"], "ready")
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

    def test_bootstrap_returns_a_recoverable_setup_state_when_calibre_is_missing(self) -> None:
        self.bridge.close()
        empty_path = Path(self.temp_dir.name) / "empty-path"
        empty_path.mkdir()
        self.bridge = BridgeProcess(env={**self.environment, "PATH": str(empty_path)})

        events = self.bridge.request(
            {
                "protocol": 1,
                "id": "bootstrap-missing-calibre",
                "operation": "bootstrap",
                "input": {"rememberedLibraries": [str(self.library)]},
            }
        )

        self.assert_successful_lifecycle(events)
        result = events[-1]["result"]
        self.assertEqual(
            result["calibre"],
            {
                "available": False,
                "installed": False,
                "supported": False,
                "status": "missing",
                "version": "",
                "missingCommands": ["calibredb", "ebook-convert"],
            },
        )
        self.assertEqual(result["readiness"]["state"], "calibre-missing")
        self.assertEqual(
            result["readiness"]["actions"],
            ["install.calibre.omarchy", "open.calibre.download", "retry"],
        )
        self.assertEqual(result["libraries"], [])
        self.assertEqual(result["page"], {"items": [], "total": 0, "nextCursor": None})
        self.assertEqual(result["capabilities"], {"actions": []})

    def test_bootstrap_distinguishes_unsupported_and_unusable_calibre(self) -> None:
        self.bridge.close()
        fake_path = Path(self.temp_dir.name) / "fake-path"
        fake_path.mkdir()
        calibredb = fake_path / "calibredb"
        calibredb.write_text(
            "#!/bin/sh\nprintf '%s\\n' 'calibredb (calibre 6.0)'\n",
            encoding="utf-8",
        )
        calibredb.chmod(0o755)
        self.bridge = BridgeProcess(env={**self.environment, "PATH": str(fake_path)})

        unsupported = self.bridge.request(
            {
                "protocol": 1,
                "id": "bootstrap-unsupported-calibre",
                "operation": "bootstrap",
                "input": {"rememberedLibraries": [str(self.library)]},
            }
        )[-1]["result"]
        self.assertEqual(unsupported["calibre"]["status"], "unsupported")
        self.assertEqual(unsupported["calibre"]["version"], "6.0")
        self.assertEqual(unsupported["readiness"]["state"], "calibre-unsupported")

        self.bridge.close()
        calibredb.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
        calibredb.chmod(0o755)
        self.bridge = BridgeProcess(env={**self.environment, "PATH": str(fake_path)})
        unusable = self.bridge.request(
            {
                "protocol": 1,
                "id": "bootstrap-unusable-calibre",
                "operation": "bootstrap",
                "input": {"rememberedLibraries": [str(self.library)]},
            }
        )[-1]["result"]
        self.assertEqual(unusable["calibre"]["status"], "unusable")
        self.assertEqual(unusable["readiness"]["state"], "calibre-unusable")

    def test_bootstrap_discovers_the_default_home_library(self) -> None:
        self.bridge.close()
        fake_home = Path(self.temp_dir.name) / "home"
        default_library = fake_home / "Calibre Library"
        subprocess.run(
            [
                "calibredb",
                "add",
                "--empty",
                "--with-library",
                str(default_library),
                "--title",
                "Parable of the Sower",
                "--authors",
                "Octavia E. Butler",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=self.environment,
        )
        calibre_config = fake_home / ".config" / "calibre"
        calibre_config.mkdir(parents=True)
        self.bridge = BridgeProcess(
            env={
                **self.environment,
                "HOME": str(fake_home),
                "CALIBRE_CONFIG_DIRECTORY": str(calibre_config),
            }
        )

        result = self.bridge.request(
            {
                "protocol": 1,
                "id": "bootstrap-discover-library",
                "operation": "bootstrap",
                "input": {"rememberedLibraries": []},
            }
        )[-1]["result"]

        self.assertEqual(result["readiness"]["state"], "ready")
        self.assertEqual([library["name"] for library in result["libraries"]], ["Calibre Library"])
        self.assertEqual(result["page"]["items"][0]["title"], "Parable of the Sower")

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

        self.assert_successful_lifecycle(events)
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
                env=self.environment,
            ).stdout
        )[0]
        self.assertEqual(saved["title"], "Dune Messiah")
        self.assertEqual(saved["tags"], ["science fiction", "politics"])
        self.assertEqual(saved["rating"], 8.0)

    def test_cover_update_uses_calibres_public_metadata_command(self) -> None:
        cover = Path(self.temp_dir.name) / "dune.png"
        cover.write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            )
        )
        bootstrap = self.bridge.request(
            {
                "protocol": 1,
                "id": "bootstrap-cover",
                "operation": "bootstrap",
                "input": {"rememberedLibraries": [str(self.library)]},
            }
        )[-1]["result"]

        result = self.bridge.request(
            {
                "protocol": 1,
                "id": "cover-set",
                "operation": "action.run",
                "library": bootstrap["currentLibrary"],
                "input": {
                    "name": "book.cover.set",
                    "bookId": 1,
                    "path": str(cover),
                },
            }
        )[-1]

        self.assertEqual(result["type"], "succeeded")
        saved_cover = Path(result["result"]["book"]["cover"])
        self.assertTrue(saved_cover.is_file())
        self.assertTrue(saved_cover.is_relative_to(self.library))

        invalid = Path(self.temp_dir.name) / "not-an-image.txt"
        invalid.write_text("not an image", encoding="utf-8")
        rejected = self.bridge.request(
            {
                "protocol": 1,
                "id": "cover-invalid",
                "operation": "action.run",
                "library": bootstrap["currentLibrary"],
                "input": {
                    "name": "book.cover.set",
                    "bookId": 1,
                    "path": str(invalid),
                },
            }
        )[-1]
        self.assertEqual(rejected["type"], "failed")
        self.assertEqual(rejected["error"]["code"], "invalid_request")

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
                env=self.environment,
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

        self.assert_successful_lifecycle(events)
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

        self.assert_successful_lifecycle(events)
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

        self.assert_successful_lifecycle(events)
        result = events[-1]["result"]
        self.assertEqual(result["inputFormat"], "TXT")
        self.assertEqual(result["outputFormat"], "EPUB")
        self.assertEqual(result["format"]["name"], "EPUB")
        self.assertTrue(Path(result["format"]["path"]).is_file())
        self.assertGreater(result["format"]["size"], 0)
        self.assertEqual({item["name"] for item in result["book"]["formats"]}, {"TXT", "EPUB"})

    def test_conversion_descriptors_expose_useful_options_without_unsafe_paths(self) -> None:
        source = Path(self.temp_dir.name) / "dune.txt"
        source.write_text("Dune\n\nA novel by Frank Herbert.\n", encoding="utf-8")
        bootstrap = self.bridge.request(
            {
                "protocol": 1,
                "id": "bootstrap-conversion-options",
                "operation": "bootstrap",
                "input": {"rememberedLibraries": [str(self.library)]},
            }
        )[-1]["result"]
        library_token = bootstrap["currentLibrary"]
        self.bridge.request(
            {
                "protocol": 1,
                "id": "format-for-conversion-options",
                "operation": "action.run",
                "library": library_token,
                "input": {"name": "format.add", "bookId": 1, "path": str(source)},
            }
        )

        events = self.bridge.request(
            {
                "protocol": 1,
                "id": "conversion-options",
                "operation": "conversion.describe",
                "library": library_token,
                "input": {"bookId": 1, "inputFormat": "TXT", "outputFormat": "EPUB"},
            }
        )

        self.assert_successful_lifecycle(events)
        result = events[-1]["result"]
        self.assertEqual(result["inputFormat"], "TXT")
        self.assertEqual(result["outputFormat"], "EPUB")
        self.assertEqual(result["source"], "calibre-runtime")
        options = {
            option["name"]: option
            for group in result["groups"]
            for option in group["options"]
        }
        self.assertIn("output_profile", options)
        self.assertEqual(options["output_profile"]["type"], "choice")
        self.assertIn("default", options["output_profile"]["choices"])
        self.assertEqual(options["paragraph_type"]["default"], "auto")
        self.assertIn("epub_version", options)
        self.assertNotIn("extract_to", options)
        self.assertNotIn("debug_pipeline", options)
        self.assertNotIn("cover", options)

    def test_quick_conversion_accepts_only_described_advanced_options(self) -> None:
        source = Path(self.temp_dir.name) / "dune.txt"
        source.write_text("Dune\n\nA novel by Frank Herbert.\n", encoding="utf-8")
        bootstrap = self.bridge.request(
            {
                "protocol": 1,
                "id": "bootstrap-advanced-convert",
                "operation": "bootstrap",
                "input": {"rememberedLibraries": [str(self.library)]},
            }
        )[-1]["result"]
        library_token = bootstrap["currentLibrary"]
        self.bridge.request(
            {
                "protocol": 1,
                "id": "format-for-advanced-convert",
                "operation": "action.run",
                "library": library_token,
                "input": {"name": "format.add", "bookId": 1, "path": str(source)},
            }
        )

        rejected = self.bridge.request(
            {
                "protocol": 1,
                "id": "convert-unsafe-option",
                "operation": "action.run",
                "library": library_token,
                "input": {
                    "name": "book.convert.quick",
                    "bookId": 1,
                    "outputFormat": "EPUB",
                    "options": {"extract_to": "/tmp/not-allowed"},
                },
            }
        )[-1]
        self.assertEqual(rejected["type"], "failed")
        self.assertEqual(rejected["error"]["code"], "invalid_request")

        converted = self.bridge.request(
            {
                "protocol": 1,
                "id": "convert-advanced-options",
                "operation": "action.run",
                "library": library_token,
                "input": {
                    "name": "book.convert.quick",
                    "bookId": 1,
                    "outputFormat": "EPUB",
                    "options": {
                        "output_profile": "tablet",
                        "pretty_print": True,
                        "epub_version": "3",
                    },
                },
            }
        )[-1]["result"]
        self.assertEqual(
            converted["appliedOptions"],
            {"output_profile": "tablet", "pretty_print": True, "epub_version": "3"},
        )
        self.assertEqual(converted["format"]["name"], "EPUB")

    def test_conversion_replacement_is_staged_until_confirmation(self) -> None:
        source = Path(self.temp_dir.name) / "dune.txt"
        source.write_text("Dune\n\nA novel by Frank Herbert.\n", encoding="utf-8")
        bootstrap = self.bridge.request(
            {
                "protocol": 1,
                "id": "bootstrap-convert-replace",
                "operation": "bootstrap",
                "input": {"rememberedLibraries": [str(self.library)]},
            }
        )[-1]["result"]
        library_token = bootstrap["currentLibrary"]
        self.bridge.request(
            {
                "protocol": 1,
                "id": "format-for-convert-replace",
                "operation": "action.run",
                "library": library_token,
                "input": {"name": "format.add", "bookId": 1, "path": str(source)},
            }
        )
        conversion_input = {
            "name": "book.convert.quick",
            "bookId": 1,
            "inputFormat": "TXT",
            "outputFormat": "EPUB",
        }
        first = self.bridge.request(
            {
                "protocol": 1,
                "id": "convert-first",
                "operation": "action.run",
                "library": library_token,
                "input": conversion_input,
            }
        )[-1]["result"]
        first_revision = Path(first["format"]["path"]).stat().st_mtime_ns

        unsafe = self.bridge.request(
            {
                "protocol": 1,
                "id": "convert-unsafe-replace",
                "operation": "action.run",
                "library": library_token,
                "input": conversion_input,
            }
        )[-1]
        self.assertEqual(unsafe["type"], "failed")
        self.assertEqual(unsafe["error"]["code"], "confirmation_required")

        prepared = self.bridge.request(
            {
                "protocol": 1,
                "id": "convert-replace-prepare",
                "operation": "action.prepare",
                "library": library_token,
                "input": {
                    **conversion_input,
                    "name": "book.convert.replace",
                    "options": {"epub_version": "3"},
                },
            }
        )[-1]["result"]
        self.assertIn("EPUB", prepared["summary"])
        before_commit = Path(first["format"]["path"]).stat().st_mtime_ns
        self.assertEqual(before_commit, first_revision)

        committed = self.bridge.request(
            {
                "protocol": 1,
                "id": "convert-replace-commit",
                "operation": "action.commit",
                "input": {"confirmationToken": prepared["confirmationToken"]},
            }
        )[-1]["result"]
        self.assertTrue(committed["replaced"])
        self.assertEqual(committed["inputFormat"], "TXT")
        self.assertEqual(committed["outputFormat"], "EPUB")
        self.assertEqual(committed["appliedOptions"], {"epub_version": "3"})
        self.assertTrue(Path(committed["format"]["path"]).is_file())

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

    def test_device_operations_use_the_json_lines_bridge(self) -> None:
        log = self.start_device_bridge()

        bootstrap = self.bridge.request(
            {
                "protocol": 1,
                "id": "bootstrap-device",
                "operation": "bootstrap",
                "input": {"rememberedLibraries": [str(self.library)]},
            }
        )[-1]["result"]
        self.assertIn("device.probe", bootstrap["capabilities"]["actions"])
        self.assertIn("device.info", bootstrap["capabilities"]["actions"])
        self.assertIn("device.list", bootstrap["capabilities"]["actions"])
        self.assertIn("device.eject", bootstrap["capabilities"]["actions"])
        self.assertIn("device.send", bootstrap["capabilities"]["actions"])
        self.assertEqual(bootstrap["capabilities"]["device"]["state"], "ready")

        for operation, input_data, expected in [
            (
                "device.probe",
                {},
                {"state": "connected", "available": True},
            ),
            (
                "device.info",
                {},
                {"deviceName": "Kobo Clara", "mimeType": "application/x-kobo"},
            ),
            (
                "device.list",
                {"path": "/", "recursive": False},
                {"path": "/", "entry": "Books"},
            ),
            (
                "device.eject",
                {},
                {"ejected": True},
            ),
        ]:
            with self.subTest(operation=operation):
                result = self.bridge.request(
                    {
                        "protocol": 1,
                        "id": operation,
                        "operation": operation,
                        "input": input_data,
                    }
                )[-1]
                self.assertEqual(result["type"], "succeeded")
                payload = result["result"]
                for key, value in expected.items():
                    if key == "entry":
                        self.assertEqual(payload["entries"][0]["name"], value)
                    else:
                        self.assertEqual(payload[key], value)

        calls = log.read_text(encoding="utf-8").splitlines()
        self.assertIn("--version", calls)
        self.assertIn("info", calls)
        self.assertIn("ls -l /", calls)
        self.assertIn("eject", calls)

    def test_device_send_exports_only_the_requested_format_to_a_staging_file(self) -> None:
        log = self.start_device_bridge()
        source = Path(self.temp_dir.name) / "Dune.txt"
        source.write_text("Fear is the mind-killer.\n", encoding="utf-8")
        bootstrap = self.bridge.request(
            {
                "protocol": 1,
                "id": "bootstrap-device-send",
                "operation": "bootstrap",
                "input": {"rememberedLibraries": [str(self.library)]},
            }
        )[-1]["result"]
        token = bootstrap["currentLibrary"]
        self.bridge.request(
            {
                "protocol": 1,
                "id": "device-send-format",
                "operation": "action.run",
                "library": token,
                "input": {"name": "format.add", "bookId": 1, "path": str(source)},
            }
        )

        result = self.bridge.request(
            {
                "protocol": 1,
                "id": "device-send",
                "operation": "device.send",
                "library": token,
                "input": {
                    "bookId": 1,
                    "format": "TXT",
                },
            }
        )[-1]

        self.assertEqual(result["type"], "succeeded")
        self.assertEqual(result["result"]["format"], "TXT")
        self.assertEqual(result["result"]["destination"], "/Books/Dune - Frank Herbert.txt")
        calls = log.read_text(encoding="utf-8").splitlines()
        send_calls = [call for call in calls if call.startswith("cp ")]
        self.assertEqual(len(send_calls), 1)
        send_parts = send_calls[0].split(" ")
        self.assertTrue(send_parts[1].startswith("/tmp/omarchy-calibre-device-"))
        self.assertTrue(send_calls[0].endswith("dev:/Books/Dune - Frank Herbert.txt"))
        self.assertNotIn(str(self.library), send_parts[1])
        self.assertFalse(Path(send_parts[1]).exists())

    def test_device_errors_are_returned_as_structured_terminal_events(self) -> None:
        self.start_device_bridge(
            info="Unable to find a connected ebook reader.\n",
        )

        result = self.bridge.request(
            {
                "protocol": 1,
                "id": "device-probe-no-device",
                "operation": "device.probe",
                "input": {},
            }
        )[-1]

        self.assertEqual(result["type"], "succeeded")
        self.assertEqual(result["result"]["state"], "no-device")

        info_result = self.bridge.request(
            {
                "protocol": 1,
                "id": "device-info-no-device",
                "operation": "device.info",
                "input": {},
            }
        )[-1]
        self.assertEqual(info_result["type"], "failed")
        self.assertEqual(info_result["error"]["code"], "no_device")
        self.assertEqual(info_result["error"]["action"], "info")
        self.assertTrue(info_result["error"]["retryable"])

        invalid = self.bridge.request(
            {
                "protocol": 1,
                "id": "device-list-invalid",
                "operation": "device.list",
                "input": {"path": "../../outside"},
            }
        )[-1]
        self.assertEqual(invalid["type"], "failed")
        self.assertEqual(invalid["error"]["code"], "invalid_request")


if __name__ == "__main__":
    unittest.main()
