from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

from backend.calibre_bridge import BridgeError, BridgeRuntime
from backend.calibre_bridge import CalibreBridge


class FixtureBridge:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.terminated = threading.Event()
        self.release = threading.Event()

    @staticmethod
    def validate_request(request: dict[str, Any]) -> None:
        if request.get("protocol") != 1:
            raise BridgeError("invalid_request", "Unsupported bridge protocol")
        if not isinstance(request.get("id"), str) or not request["id"]:
            raise BridgeError("invalid_request", "Request id must be a non-empty string")

    @staticmethod
    def scheduling_key(request: dict[str, Any]) -> str | None:
        return request.get("library")

    def execute(self, request: dict[str, Any], context: Any = None) -> dict[str, Any]:
        operation = request.get("operation")
        if operation == "fail":
            raise BridgeError("fixture_failed", "Fixture failed", retryable=True)
        if operation == "slow":
            context.register_terminator(self.terminated.set)
            self.started.set()
            self.release.wait(1)
            context.check_cancelled()
        if context is not None:
            context.report_progress({"fraction": 0.5, "message": "Halfway"})
        return {"operation": operation}


class BridgeRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.condition = threading.Condition()
        self.bridge = FixtureBridge()

        def emit(event: dict[str, Any]) -> None:
            with self.condition:
                self.events.append(event)
                self.condition.notify_all()

        self.runtime = BridgeRuntime(self.bridge, emit, max_workers=2)

    def tearDown(self) -> None:
        self.bridge.release.set()
        self.runtime.close()

    def wait_for_terminal(self, request_id: str) -> list[dict[str, Any]]:
        with self.condition:
            ready = self.condition.wait_for(
                lambda: any(
                    event.get("id") == request_id
                    and event.get("type") in {"succeeded", "failed", "cancelled"}
                    for event in self.events
                ),
                timeout=1,
            )
            self.assertTrue(ready, f"{request_id} did not finish")
            return [event for event in self.events if event.get("id") == request_id]

    def test_request_emits_protocol_progress_and_one_terminal_event(self) -> None:
        self.runtime.receive(
            {
                "protocol": 1,
                "id": "query-1",
                "operation": "query",
                "library": "library-a",
                "input": {},
            }
        )

        events = self.wait_for_terminal("query-1")

        self.assertEqual([event["type"] for event in events], ["accepted", "progress", "succeeded"])
        self.assertEqual([event["sequence"] for event in events], [0, 1, 2])
        self.assertTrue(all(event["protocol"] == 1 for event in events))
        self.assertEqual(events[1]["progress"], {"fraction": 0.5, "message": "Halfway"})
        self.assertEqual(events[-1]["result"], {"operation": "query"})

    def test_cancel_frame_cancels_running_precommit_work(self) -> None:
        self.runtime.receive(
            {
                "protocol": 1,
                "id": "slow-1",
                "operation": "slow",
                "library": "library-a",
                "input": {},
            }
        )
        self.assertTrue(self.bridge.started.wait(1))

        self.runtime.receive({"protocol": 1, "type": "cancel", "id": "slow-1"})

        self.assertTrue(self.bridge.terminated.wait(1))
        self.bridge.release.set()
        events = self.wait_for_terminal("slow-1")
        self.assertEqual([event["type"] for event in events], ["accepted", "cancelled"])

    def test_bridge_errors_keep_their_structured_payload(self) -> None:
        self.runtime.receive(
            {
                "protocol": 1,
                "id": "fail-1",
                "operation": "fail",
                "input": {},
            }
        )

        terminal = self.wait_for_terminal("fail-1")[-1]

        self.assertEqual(terminal["type"], "failed")
        self.assertEqual(
            terminal["error"],
            {
                "code": "fixture_failed",
                "message": "Fixture failed",
                "retryable": True,
            },
        )

    def test_invalid_requests_fail_without_entering_the_scheduler(self) -> None:
        self.runtime.receive({"protocol": 2, "id": "invalid-1", "operation": "query"})

        events = self.wait_for_terminal("invalid-1")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["sequence"], 0)
        self.assertEqual(events[0]["type"], "failed")
        self.assertEqual(events[0]["error"]["code"], "invalid_request")


class ManagedProcessRuntimeTest(unittest.TestCase):
    def test_cancel_terminates_the_calibre_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "pids"
            script = (
                "import os,pathlib,signal,subprocess,sys,time; "
                "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                "child=subprocess.Popen([sys.executable,'-c',"
                "'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(5)']); "
                f"pathlib.Path({str(marker)!r}).write_text(f'{{os.getpid()}} {{child.pid}}'); "
                "time.sleep(5)"
            )

            class CommandBridge(CalibreBridge):
                def execute(self, request: dict[str, Any], context: Any = None) -> dict[str, str]:
                    completed = self.run(
                        [sys.executable, "-c", script],
                        timeout=10,
                        context=context,
                    )
                    return {"stdout": completed.stdout}

            events: list[dict[str, Any]] = []
            condition = threading.Condition()

            def emit(event: dict[str, Any]) -> None:
                with condition:
                    events.append(event)
                    condition.notify_all()

            runtime = BridgeRuntime(CommandBridge(), emit, max_workers=1)
            try:
                runtime.receive(
                    {
                        "protocol": 1,
                        "id": "process-1",
                        "operation": "fixture.command",
                        "input": {},
                    }
                )
                deadline = time.monotonic() + 1
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(marker.exists(), "managed process did not start")
                pids = [int(value) for value in marker.read_text().split()]

                runtime.receive({"protocol": 1, "type": "cancel", "id": "process-1"})

                with condition:
                    finished = condition.wait_for(
                        lambda: any(event["type"] == "cancelled" for event in events),
                        timeout=2,
                    )
                self.assertTrue(finished, "cancel did not terminate the managed command")
                deadline = time.monotonic() + 1
                while any(self.process_is_running(pid) for pid in pids) and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertFalse(any(self.process_is_running(pid) for pid in pids))
            finally:
                runtime.close()

    def test_cancel_after_commit_does_not_interrupt_the_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "committed"
            script = (
                "import pathlib,time; "
                f"pathlib.Path({str(marker)!r}).write_text('ready'); "
                "time.sleep(0.2)"
            )

            class CommittedBridge(CalibreBridge):
                def execute(self, request: dict[str, Any], context: Any = None) -> dict[str, bool]:
                    self.run(
                        [sys.executable, "-c", script],
                        timeout=2,
                        context=context,
                        commit=True,
                    )
                    return {"committed": True}

            events: list[dict[str, Any]] = []
            condition = threading.Condition()

            def emit(event: dict[str, Any]) -> None:
                with condition:
                    events.append(event)
                    condition.notify_all()

            runtime = BridgeRuntime(CommittedBridge(), emit, max_workers=1)
            try:
                runtime.receive(
                    {
                        "protocol": 1,
                        "id": "commit-1",
                        "operation": "fixture.commit",
                        "input": {},
                    }
                )
                deadline = time.monotonic() + 1
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(marker.exists(), "committed command did not start")

                runtime.receive({"protocol": 1, "type": "cancel", "id": "commit-1"})

                with condition:
                    finished = condition.wait_for(
                        lambda: any(
                            event["type"] in {"succeeded", "failed", "cancelled"}
                            for event in events
                        ),
                        timeout=1,
                    )
                self.assertTrue(finished)
                self.assertEqual(events[-1]["type"], "succeeded")
                self.assertEqual(events[-1]["result"], {"committed": True})
            finally:
                runtime.close()

    @staticmethod
    def process_is_running(pid: int) -> bool:
        try:
            state = Path(f"/proc/{pid}/stat").read_text().split()[2]
        except (FileNotFoundError, IndexError, OSError):
            return False
        return state != "Z"


class CalibreSchedulingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.started = {
            "first": threading.Event(),
            "second": threading.Event(),
        }
        self.release = {
            "first": threading.Event(),
            "second": threading.Event(),
        }

        started = self.started
        release = self.release

        class SchedulingBridge(CalibreBridge):
            def execute(self, request: dict[str, Any], context: Any = None) -> dict[str, str]:
                request_id = request["id"]
                started[request_id].set()
                release[request_id].wait(1)
                return {"id": request_id}

        self.runtime = BridgeRuntime(SchedulingBridge(), lambda event: None, max_workers=2)

    def tearDown(self) -> None:
        for event in self.release.values():
            event.set()
        self.runtime.close()

    @staticmethod
    def request(request_id: str, operation: str, *, action: str = "") -> dict[str, Any]:
        input_data = {"name": action} if action else {}
        return {
            "protocol": 1,
            "id": request_id,
            "operation": operation,
            "library": "library-a",
            "input": input_data,
        }

    def test_same_library_reads_can_run_concurrently(self) -> None:
        self.runtime.receive(self.request("first", "books.query"))
        self.runtime.receive(self.request("second", "conversion.describe"))

        self.assertTrue(self.started["first"].wait(1))
        self.assertTrue(self.started["second"].wait(1))

    def test_same_library_mutations_remain_fifo(self) -> None:
        self.runtime.receive(
            self.request("first", "action.run", action="book.metadata.update")
        )
        self.runtime.receive(
            self.request("second", "action.run", action="format.add")
        )

        self.assertTrue(self.started["first"].wait(1))
        self.assertFalse(self.started["second"].wait(0.05))
        self.release["first"].set()
        self.assertTrue(self.started["second"].wait(1))

    def test_export_path_aliases_and_confirmed_replace_share_one_lane(self) -> None:
        bridge = CalibreBridge()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "exports"
            bridge.confirmations["replace-token"] = {
                "name": "book.export.replace",
                "libraryToken": "library-a",
                "destination": destination.resolve(),
            }
            direct = {
                "protocol": 1,
                "id": "direct",
                "operation": "action.run",
                "library": "library-a",
                "input": {
                    "name": "book.export",
                    "destination": str(destination),
                },
            }
            alias = {
                **direct,
                "id": "alias",
                "input": {
                    **direct["input"],
                    "destination": f"{temporary}/./exports",
                },
            }
            prepare = {
                **direct,
                "id": "prepare",
                "operation": "action.prepare",
                "input": {
                    **direct["input"],
                    "name": "book.export.replace",
                },
            }
            commit = {
                "protocol": 1,
                "id": "commit",
                "operation": "action.commit",
                "library": "library-a",
                "input": {"confirmationToken": "replace-token"},
            }

            expected = ("export", str(destination.resolve()))
            self.assertEqual(bridge.scheduling_key(direct), expected)
            self.assertEqual(bridge.scheduling_key(alias), expected)
            self.assertEqual(bridge.scheduling_key(prepare), expected)
            self.assertEqual(bridge.scheduling_key(commit), expected)


if __name__ == "__main__":
    unittest.main()
