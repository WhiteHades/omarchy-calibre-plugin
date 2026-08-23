from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.calibre_bridge import BridgeError, CalibreBridge


class CalibreCommandLockTest(unittest.TestCase):
    def test_busy_local_library_uses_matching_content_server(self) -> None:
        library = Path("/tmp/Real Library")
        local = ["calibredb", "list", "--with-library", str(library)]
        server = "http://127.0.0.1:54321"
        remote = f"{server}/#Real_Library"
        success = Mock(stdout="")
        bridge = CalibreBridge()

        with (
            patch.object(bridge, "acquire_calibredb_lock", return_value=None),
            patch.object(bridge, "local_content_servers", return_value=[server]),
            patch.object(
                bridge,
                "run_command",
                side_effect=[
                    BridgeError("calibre_busy", "busy"),
                    Mock(stdout="Other_Library\nReal_Library\n"),
                    success,
                    success,
                ],
            ) as run_command,
        ):
            bridge.run(local)
            bridge.run(local)

        targets = [call.args[0][3] for call in run_command.call_args_list]
        self.assertEqual(targets, [str(library), f"{server}/#-", remote, remote])

    def test_busy_write_does_not_use_a_cached_read_only_server(self) -> None:
        library = Path("/tmp/Real Library")
        read = ["calibredb", "list", "--with-library", str(library)]
        write = ["calibredb", "add", "--with-library", str(library), "/tmp/book.pdf"]
        server = "http://127.0.0.1:54321"
        remote = f"{server}/#Real_Library"
        bridge = CalibreBridge()

        with (
            patch.object(bridge, "acquire_calibredb_lock", return_value=None),
            patch.object(bridge, "local_content_servers", return_value=[server]),
            patch.object(
                bridge,
                "run_command",
                side_effect=[
                    BridgeError("calibre_busy", "busy"),
                    Mock(stdout="Real_Library\n"),
                    Mock(stdout="[]"),
                    BridgeError("calibre_busy", "busy"),
                ],
            ) as run_command,
        ):
            bridge.run(read)
            with self.assertRaises(BridgeError) as raised:
                bridge.run(write, commit=True)

        self.assertEqual(raised.exception.code, "calibre_busy")
        self.assertEqual(
            raised.exception.message,
            "The running Calibre Content server is read-only. Stop it, then retry",
        )
        self.assertEqual(
            [call.args[0] for call in run_command.call_args_list],
            [
                read,
                ["calibredb", "list", "--with-library", f"{server}/#-"],
                ["calibredb", "list", "--with-library", remote],
                write,
            ],
        )

    def test_separate_bridges_do_not_overlap_calibredb_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            runtime.mkdir(mode=0o700)
            command = root / "calibredb"
            guard = root / "process-running"
            overlap = root / "overlap"
            command.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, sys, time\n"
                "guard = pathlib.Path(sys.argv[1])\n"
                "overlap = pathlib.Path(sys.argv[2])\n"
                "try:\n"
                "    guard.mkdir()\n"
                "    acquired = True\n"
                "except FileExistsError:\n"
                "    overlap.write_text('overlap')\n"
                "    acquired = False\n"
                "time.sleep(0.15)\n"
                "if acquired:\n"
                "    guard.rmdir()\n",
                encoding="utf-8",
            )
            command.chmod(0o755)
            bridges = [CalibreBridge(), CalibreBridge()]
            start = threading.Barrier(3)
            failures: list[BaseException] = []

            def invoke(bridge: CalibreBridge) -> None:
                try:
                    start.wait()
                    bridge.run([str(command), str(guard), str(overlap)])
                except BaseException as error:  # pragma: no cover - asserted below
                    failures.append(error)

            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
                threads = [threading.Thread(target=invoke, args=(bridge,)) for bridge in bridges]
                for thread in threads:
                    thread.start()
                start.wait()
                for thread in threads:
                    thread.join(2)

            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(failures, [])
            self.assertFalse(overlap.exists(), "calibredb processes overlapped")

    def test_command_failures_do_not_expose_raw_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            command = Path(temporary) / "failing-tool"
            command.write_text(
                "#!/bin/sh\nprintf '%s\\n' 'private path: /home/example/Library' >&2\nexit 1\n",
                encoding="utf-8",
            )
            command.chmod(0o755)

            with self.assertRaises(BridgeError) as raised:
                CalibreBridge().run([str(command)])

            self.assertEqual(raised.exception.code, "tool_failed")
            self.assertEqual(raised.exception.message, "Calibre could not complete the requested operation")
            self.assertNotIn("/home/example", raised.exception.message)


if __name__ == "__main__":
    unittest.main()
