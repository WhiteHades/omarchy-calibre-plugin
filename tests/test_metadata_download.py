from __future__ import annotations

import copy
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

from backend.calibre_bridge import BridgeError, BridgeRuntime, CalibreBridge


ROOT = Path(__file__).resolve().parents[1]
TEST_TMP = ROOT / ".tmp" / "tests"


OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
  <metadata>
    <dc:title>Updated title</dc:title>
    <dc:creator opf:file-as="Author, New">New Author</dc:creator>
    <dc:subject>new tag</dc:subject>
    <dc:publisher>New Press</dc:publisher>
    <dc:date>2024-03-01</dc:date>
    <dc:language>en</dc:language>
    <dc:description>New comments</dc:description>
    <dc:identifier opf:scheme="ISBN">9780000000002</dc:identifier>
    <dc:identifier opf:scheme="goodreads">123</dc:identifier>
    <meta name="calibre:series" content="New series" />
    <meta name="calibre:series_index" content="2" />
    <meta name="calibre:rating" content="8" />
  </metadata>
</package>
"""


class MetadataFixtureBridge(CalibreBridge):
    def __init__(self, root: Path, book: dict) -> None:
        super().__init__()
        self.root = root
        self.book = copy.deepcopy(book)
        self.staging: list[Path] = []

    def require_library(self, library_token: object) -> Path:
        return self.root

    def get_book(self, library_token: str, book_id: int) -> dict:
        return copy.deepcopy(self.book)

    def metadata_staging_directory(self) -> Path:
        path = Path(tempfile.mkdtemp(dir=self.root, prefix="metadata-preview-"))
        self.staging.append(path)
        return path


def write_executable(path: Path, source: str) -> None:
    path.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
    path.chmod(0o755)


def write_fake_tools(
    root: Path,
    *,
    output: str = OPF,
    cover: bool = True,
    sleep: float = 0,
    fetch_exit: int = 0,
    fetch_stderr: str = "",
) -> Path:
    bin_dir = root / "bin"
    bin_dir.mkdir(exist_ok=True)
    output_literal = repr(output)
    write_executable(
        bin_dir / "fetch-ebook-metadata",
        "import os, pathlib, sys, time\n"
        f"time.sleep({sleep!r})\n"
        "cover_index = sys.argv.index('--cover') if '--cover' in sys.argv else -1\n"
        "if cover_index >= 0:\n"
        f"    cover = pathlib.Path(sys.argv[cover_index + 1])\n"
        f"    {'cover.write_bytes(b\"fake-cover\")' if cover else 'pass'}\n"
        f"sys.stderr.write({fetch_stderr!r})\n"
        f"sys.stdout.write({output_literal})\n"
        f"raise SystemExit({fetch_exit!r})\n",
    )
    write_executable(
        bin_dir / "calibredb",
        "import os, pathlib, sys\n"
        "log = os.environ.get('CALIBRE_LOG')\n"
        "if log:\n"
        "    with pathlib.Path(log).open('a', encoding='utf-8') as handle:\n"
        "        handle.write(' '.join(sys.argv[1:]) + '\\n')\n",
    )
    return bin_dir


def fixture_book() -> dict:
    return {
        "id": 1,
        "title": "Original title",
        "authors": ["Old Author"],
        "authorSort": "Author, Old",
        "series": "",
        "seriesIndex": 1.0,
        "rating": 0.0,
        "tags": ["old tag"],
        "publisher": "Old Press",
        "published": "2020-01-01",
        "languages": ["en"],
        "identifiers": {"isbn": "9780000000001", "asin": "old"},
        "comments": "Old comments",
        "formats": [],
        "cover": "",
        "modified": "revision-1",
    }


class MetadataDownloadTest(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TMP.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.root = Path(self.temporary.name)
        self.log = self.root / "calibre.log"
        self.environment = {
            **os.environ,
            "PATH": os.environ["PATH"],
            "CALIBRE_LOG": str(self.log),
        }
        self.bridge = MetadataFixtureBridge(self.root, fixture_book())

    def tearDown(self) -> None:
        self.bridge.close()
        self.temporary.cleanup()

    def use_tools(self, **kwargs: object) -> None:
        bin_dir = write_fake_tools(self.root, **kwargs)
        self.environment["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
        self.path_patch = mock.patch.dict(os.environ, self.environment)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def test_preview_passes_book_identity_and_returns_field_diffs_without_mutating(self) -> None:
        self.use_tools()

        result = self.bridge.fetch_metadata(
            "library-token",
            self.root,
            {"bookId": 1, "timeout": 2},
        )

        self.assertTrue(result["previewToken"])
        self.assertEqual(result["candidate"]["title"], "Updated title")
        self.assertEqual(result["candidate"]["authors"], ["New Author"])
        self.assertEqual(result["candidate"]["rating"], 4.0)
        self.assertEqual(result["candidate"]["tags"], ["new tag", "old tag"])
        self.assertEqual(
            result["candidate"]["identifiers"],
            {"isbn": "9780000000002", "asin": "old", "goodreads": "123"},
        )
        self.assertTrue(result["coverAvailable"])
        self.assertEqual(Path(result["coverPath"]), self.bridge.staging[0] / "cover.jpg")
        self.assertEqual(result["expiresInSeconds"], 300)
        self.assertTrue(any(change["field"] == "title" for change in result["changes"]))
        self.assertTrue(self.bridge.staging[0].exists())
        self.assertFalse(self.log.exists(), "preview must not call calibredb mutation commands")

        command = CalibreBridge.metadata_fetch_command(
            "/fake/fetch-ebook-metadata",
            fixture_book() | {"isbn": "9780000000001"},
            self.root / "cover.jpg",
            2,
        )
        self.assertIn("9780000000001", command)
        self.assertIn("asin:old", command)

    def test_opf_ratings_always_convert_from_calibres_ten_point_scale(self) -> None:
        parsed = CalibreBridge.parse_metadata_opf(
            """<?xml version="1.0" encoding="utf-8"?>
            <package xmlns="http://www.idpf.org/2007/opf">
              <metadata>
                <meta name="calibre:rating" content="4" />
              </metadata>
            </package>"""
        )

        self.assertEqual(parsed["rating"], 2.0)

    def test_apply_uses_only_selected_fields_and_cleans_preview(self) -> None:
        self.use_tools()
        preview = self.bridge.fetch_metadata("library-token", self.root, {"bookId": 1})

        applied = self.bridge.action_commit(
            {
                "confirmationToken": preview["previewToken"],
                "fields": ["title", "cover"],
            }
        )

        self.assertEqual(applied["result"] if "result" in applied else applied["appliedFields"], ["title", "cover"])
        self.assertFalse(self.bridge.staging[0].exists())
        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("set_metadata", lines[0])
        self.assertIn("--field title:Updated title", lines[0])
        self.assertNotIn("tags:", lines[0])
        self.assertIn("--field cover:", lines[0])

    def test_discard_and_expiry_remove_staging(self) -> None:
        self.use_tools()
        preview = self.bridge.fetch_metadata("library-token", self.root, {"bookId": 1})
        staging = self.bridge.staging[0]
        discarded = self.bridge.action_discard({"confirmationToken": preview["previewToken"]})
        self.assertEqual(discarded, {"discarded": True})
        self.assertFalse(staging.exists())

        preview = self.bridge.fetch_metadata("library-token", self.root, {"bookId": 1})
        staging = self.bridge.staging[1]
        self.bridge.confirmations[preview["previewToken"]]["expires"] = time.monotonic() - 1
        self.assertEqual(self.bridge.prune_confirmations(), 1)
        self.assertFalse(staging.exists())

    def test_missing_cli_is_structured(self) -> None:
        empty = self.root / "empty-bin"
        empty.mkdir()
        with mock.patch.dict(os.environ, {"PATH": str(empty)}):
            with self.assertRaises(BridgeError) as raised:
                self.bridge.fetch_metadata("library-token", self.root, {"bookId": 1})
        self.assertEqual(raised.exception.code, "capability_unavailable")

    def test_no_result_and_malformed_opf_clean_staging(self) -> None:
        for output, expected_code in (("", "metadata_no_result"), ("not opf", "metadata_malformed_opf")):
            with self.subTest(expected_code=expected_code):
                self.use_tools(output=output, cover=False)
                with self.assertRaises(BridgeError) as raised:
                    self.bridge.fetch_metadata("library-token", self.root, {"bookId": 1})
                self.assertEqual(raised.exception.code, expected_code)
                self.assertFalse(self.bridge.staging[-1].exists())

    def test_calibres_nonzero_no_result_exit_is_retryable(self) -> None:
        self.use_tools(
            output="",
            cover=False,
            fetch_exit=1,
            fetch_stderr="provider diagnostics\nNo results found\n",
        )

        with self.assertRaises(BridgeError) as raised:
            self.bridge.fetch_metadata("library-token", self.root, {"bookId": 1})

        self.assertEqual(raised.exception.code, "metadata_no_result")
        self.assertTrue(raised.exception.retryable)
        self.assertFalse(self.bridge.staging[-1].exists())

    def test_timeout_cleans_staging(self) -> None:
        self.use_tools(output=OPF, sleep=2)
        with self.assertRaises(BridgeError) as raised:
            self.bridge.fetch_metadata("library-token", self.root, {"bookId": 1, "timeout": 1})
        self.assertEqual(raised.exception.code, "timeout")
        self.assertFalse(self.bridge.staging[-1].exists())

    def test_running_preview_can_be_cancelled_and_cleans_staging(self) -> None:
        marker = self.root / "started"
        bin_dir = write_fake_tools(self.root, sleep=10)
        self.environment["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
        self.environment["METADATA_STARTED"] = str(marker)
        script = (bin_dir / "fetch-ebook-metadata").read_text(encoding="utf-8")
        script = script.replace("time.sleep(10)", "pathlib.Path(os.environ['METADATA_STARTED']).write_text('yes')\ntime.sleep(10)")
        (bin_dir / "fetch-ebook-metadata").write_text(script, encoding="utf-8")
        (bin_dir / "fetch-ebook-metadata").chmod(0o755)

        events: list[dict] = []
        condition = threading.Condition()

        def emit(event: dict) -> None:
            with condition:
                events.append(event)
                condition.notify_all()

        with mock.patch.dict(os.environ, self.environment):
            runtime = BridgeRuntime(self.bridge, emit, max_workers=1)
            try:
                request = {
                    "protocol": 1,
                    "id": "metadata-cancel",
                    "operation": "action.run",
                    "library": "library-token",
                    "input": {"name": "book.metadata.fetch", "bookId": 1, "timeout": 10},
                }
                runtime.receive(request)
                deadline = time.monotonic() + 2
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(marker.exists())
                runtime.receive({"protocol": 1, "type": "cancel", "id": "metadata-cancel"})
                with condition:
                    self.assertTrue(
                        condition.wait_for(
                            lambda: any(event["type"] == "cancelled" for event in events),
                            timeout=3,
                        )
                    )
                self.assertTrue(self.bridge.staging)
                self.assertFalse(self.bridge.staging[0].exists())
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
