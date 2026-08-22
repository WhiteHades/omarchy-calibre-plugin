from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.calibre_bridge import BridgeError, CalibreBridge


class CalibreCommandLockTest(unittest.TestCase):
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
