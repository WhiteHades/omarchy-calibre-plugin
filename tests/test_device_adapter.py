from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from backend.device_adapter import DeviceAdapter, DeviceError


USAGE = """Usage: ebook-device [options] command args

command is one of: info, books, df, ls, cp, mkdir, touch, cat, rm, eject, test_file

For help on a particular command: ebook-device command
"""


class FixtureCommand:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.responses: dict[tuple[str, ...], subprocess.CompletedProcess[str]] = {}

    def add(
        self,
        args: tuple[str, ...],
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.responses[args] = subprocess.CompletedProcess(
            args,
            returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def __call__(self, command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        self.calls.append(command)
        args = tuple(command[1:])
        response = self.responses.get(args)
        if response is None:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            command,
            response.returncode,
            stdout=response.stdout,
            stderr=response.stderr,
        )


class DeviceAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.executable = root / "ebook-device"
        self.executable.write_text("fixture", encoding="utf-8")
        self.executable.chmod(0o755)
        self.source = root / "Book.epub"
        self.source.write_bytes(b"book")
        self.runner = FixtureCommand()
        self.runner.add(("--version",), stdout="calibre version: 9.4.0\n")
        self.runner.add((), stdout=USAGE)
        self.adapter = DeviceAdapter(executable=self.executable, runner=self.runner)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_unavailable_is_structured_without_running_a_command(self) -> None:
        adapter = DeviceAdapter(executable=Path(self.temp_dir.name) / "missing", runner=self.runner)

        result = adapter.capabilities()

        self.assertEqual(result["state"], "unavailable")
        self.assertFalse(result["available"])
        self.assertEqual(result["commands"], [])
        self.assertEqual(self.runner.calls, [])

    def test_probe_distinguishes_no_device_from_missing_cli(self) -> None:
        self.runner.add(
            ("info",),
            stderr="Unable to find a connected ebook reader.\n",
        )

        result = self.adapter.probe()

        self.assertEqual(result["state"], "no-device")
        self.assertTrue(result["available"])
        self.assertIsNone(result["info"])
        self.assertEqual(self.runner.calls[-1][1:], ["info"])

    def test_info_parses_calibre_device_output(self) -> None:
        self.runner.add(
            ("info",),
            stdout=(
                "Device name:      Kobo Clara\n"
                "Device version:   1.0\n"
                "Software version: 4.38\n"
                "Mime type:        application/x-kobo\n"
            ),
        )

        result = self.adapter.info()

        self.assertEqual(
            result,
            {
                "deviceName": "Kobo Clara",
                "deviceVersion": "1.0",
                "softwareVersion": "4.38",
                "mimeType": "application/x-kobo",
            },
        )
        self.assertEqual(self.runner.calls[-1][1:], ["info"])

    def test_list_parses_long_listing_and_preserves_spaces(self) -> None:
        self.runner.add(
            ("ls", "-l", "/"),
            stdout=(
                "drwxr-xr-x 0 2026-08-22 10:11 Books\n"
                "-rw-r--r-- 42 2026-08-22 10:12 A book.epub\n"
            ),
        )

        result = self.adapter.list("/")

        self.assertEqual(result["path"], "/")
        self.assertEqual(
            result["entries"],
            [
                {
                    "name": "Books",
                    "path": "/Books",
                    "isDirectory": True,
                    "size": 0,
                    "mode": "drwxr-xr-x",
                    "modified": "2026-08-22 10:11",
                },
                {
                    "name": "A book.epub",
                    "path": "/A book.epub",
                    "isDirectory": False,
                    "size": 42,
                    "mode": "-rw-r--r--",
                    "modified": "2026-08-22 10:12",
                },
            ],
        )
        self.assertEqual(self.runner.calls[-1][1:], ["ls", "-l", "/"])

    def test_list_rejects_traversal_before_invocation(self) -> None:
        with self.assertRaises(DeviceError) as raised:
            self.adapter.list("/Books/../private")

        self.assertEqual(raised.exception.code, "invalid_request")
        self.assertEqual(self.runner.calls, [])

    def test_send_validates_local_and_device_paths_and_uses_argv(self) -> None:
        self.runner.add(("cp", "--force", str(self.source), "dev:/Books/Book.epub"))

        result = self.adapter.send(self.source, "/Books/Book.epub", force=True)

        self.assertEqual(
            result,
            {
                "source": str(self.source.resolve()),
                "destination": "/Books/Book.epub",
                "replaced": True,
            },
        )
        self.assertEqual(
            self.runner.calls[-1][1:],
            ["cp", "--force", str(self.source.resolve()), "dev:/Books/Book.epub"],
        )

    def test_send_rejects_missing_source_and_unsafe_destination(self) -> None:
        for source, destination in [
            (Path(self.temp_dir.name) / "missing.epub", "/Books/missing.epub"),
            (self.source, "../../outside.epub"),
            (self.source, "/Books/../outside.epub"),
            (self.source, "/Books/"),
        ]:
            with self.subTest(source=source, destination=destination):
                with self.assertRaises(DeviceError) as raised:
                    self.adapter.send(source, destination)
                self.assertEqual(raised.exception.code, "invalid_request")
        self.assertEqual(self.runner.calls, [])

    def test_send_reports_an_existing_destination_separately(self) -> None:
        self.runner.add(
            ("cp", str(self.source), "dev:/Books/Book.epub"),
            returncode=1,
            stderr="File already exists: /Books/Book.epub\n",
        )

        with self.assertRaises(DeviceError) as raised:
            self.adapter.send(self.source, "/Books/Book.epub")

        self.assertEqual(raised.exception.code, "destination_exists")
        self.assertEqual(raised.exception.message, "This book already exists on the ebook reader")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.action, "send")
        self.assertNotIn("detail", raised.exception.as_dict())
        self.assertNotIn("/Books/Book.epub", str(raised.exception.as_dict()))

    def test_receive_copies_a_device_file_to_a_new_local_path(self) -> None:
        destination = Path(self.temp_dir.name) / "received.epub"

        def receive_runner(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            result = self.runner(command, timeout)
            if command[1:3] == ["cp", "dev:/Books/Book.epub"]:
                Path(command[3]).write_bytes(b"reader copy")
            return result

        adapter = DeviceAdapter(executable=self.executable, runner=receive_runner)
        result = adapter.receive("/Books/Book.epub", destination)

        self.assertEqual(result, {"source": "/Books/Book.epub", "destination": str(destination)})
        self.assertEqual(destination.read_bytes(), b"reader copy")
        self.assertEqual(
            self.runner.calls[-1][1:],
            ["cp", "dev:/Books/Book.epub", str(destination)],
        )

        with self.assertRaises(DeviceError) as raised:
            adapter.receive("/Books/Book.epub", destination)
        self.assertEqual(raised.exception.code, "invalid_request")

    def test_receive_rejects_unsafe_device_and_local_paths(self) -> None:
        missing_parent = Path(self.temp_dir.name) / "missing" / "received.epub"
        for source, destination in (
            ("/Books/../private.epub", Path(self.temp_dir.name) / "private.epub"),
            ("/Books/Book.epub", missing_parent),
            ("/Books/Book.epub", "x" * 4097),
        ):
            with self.subTest(source=source, destination=destination):
                with self.assertRaises(DeviceError) as raised:
                    self.adapter.receive(source, destination)
                self.assertEqual(raised.exception.code, "invalid_request")
        self.assertEqual(self.runner.calls, [])

    def test_eject_returns_a_normalized_result(self) -> None:
        self.runner.add(("eject",))

        self.assertEqual(self.adapter.eject(), {"ejected": True})
        self.assertEqual(self.runner.calls[-1][1:], ["eject"])

    def test_unsupported_command_is_structured(self) -> None:
        self.runner.add(
            (),
            stdout="Usage: ebook-device [options] command args\ncommand is one of: info, ls\n",
        )

        with self.assertRaises(DeviceError) as raised:
            self.adapter.eject()

        self.assertEqual(raised.exception.code, "unsupported")

    def test_timeout_is_retryable(self) -> None:
        class TimeoutRunner:
            def __call__(self, command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
                raise subprocess.TimeoutExpired(command, timeout)

        adapter = DeviceAdapter(executable=self.executable, runner=TimeoutRunner(), timeout=0.25)

        with self.assertRaises(DeviceError) as raised:
            adapter.info()

        self.assertEqual(raised.exception.code, "timeout")
        self.assertTrue(raised.exception.retryable)

    def test_malformed_info_is_structured(self) -> None:
        self.runner.add(("info",), stdout="Device name: only this field\n")

        with self.assertRaises(DeviceError) as raised:
            self.adapter.info()

        self.assertEqual(raised.exception.code, "invalid_output")

    def test_locked_device_is_structured_even_when_calibre_exits_zero(self) -> None:
        self.runner.add(("eject",), stderr="The device is locked. Use the --unlock option\n")

        with self.assertRaises(DeviceError) as raised:
            self.adapter.eject()

        self.assertEqual(raised.exception.code, "device_locked")

    def test_error_serialization_is_qml_safe(self) -> None:
        error = DeviceError("no_device", "No device is connected", retryable=True)

        self.assertEqual(
            error.as_dict(),
            {
                "code": "no_device",
                "message": "No device is connected",
                "retryable": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
