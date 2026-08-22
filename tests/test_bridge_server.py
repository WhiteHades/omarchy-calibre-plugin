from __future__ import annotations

import threading
import unittest
from typing import Any

from backend.bridge_server import OperationScheduler, SchedulerClosed


def wait_for(event: threading.Event, message: str = "event did not fire") -> None:
    if not event.wait(1.0):
        raise AssertionError(message)


class BridgeServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.event_lock = threading.RLock()
        self.terminals: dict[str, threading.Event] = {}

    def sink(self, event: dict[str, Any]) -> None:
        with self.event_lock:
            self.events.append(event)
            if event["type"] in {"succeeded", "failed", "cancelled"}:
                self.terminals.setdefault(event["id"], threading.Event()).set()

    def wait_for_terminal(self, request_id: str) -> None:
        with self.event_lock:
            terminal = self.terminals.setdefault(request_id, threading.Event())
        wait_for(terminal, f"{request_id} did not reach a terminal event")

    def event_types(self, request_id: str) -> list[str]:
        with self.event_lock:
            return [event["type"] for event in self.events if event["id"] == request_id]

    def events_for(self, request_id: str) -> list[dict[str, Any]]:
        with self.event_lock:
            return [event.copy() for event in self.events if event["id"] == request_id]

    def test_acceptance_is_emitted_before_executor_can_start(self) -> None:
        scheduler = OperationScheduler(self.sink, max_workers=1)
        started = threading.Event()
        release = threading.Event()
        observed: list[list[str]] = []

        def execute(context: Any) -> str:
            observed.append(self.event_types("request-1"))
            started.set()
            wait_for(release)
            return "done"

        try:
            scheduler.submit("request-1", execute)
            wait_for(started)
            self.assertEqual(observed, [["accepted"]])
            release.set()
            self.wait_for_terminal("request-1")
        finally:
            scheduler.close()

        self.assertEqual(self.event_types("request-1"), ["accepted", "succeeded"])
        self.assertEqual(self.events_for("request-1")[0]["sequence"], 0)

    def test_unkeyed_operations_can_run_concurrently(self) -> None:
        scheduler = OperationScheduler(self.sink, max_workers=2)
        first_started = threading.Event()
        second_started = threading.Event()
        release = threading.Event()

        def execute(context: Any) -> str:
            if context.request_id == "first":
                first_started.set()
            else:
                second_started.set()
            wait_for(release)
            return context.request_id

        try:
            scheduler.submit("first", execute)
            scheduler.submit("second", execute)
            wait_for(first_started)
            wait_for(second_started)
            release.set()
            self.wait_for_terminal("first")
            self.wait_for_terminal("second")
        finally:
            scheduler.close()

        self.assertEqual(self.event_types("first"), ["accepted", "succeeded"])
        self.assertEqual(self.event_types("second"), ["accepted", "succeeded"])

    def test_keyed_lane_is_fifo_and_does_not_occupy_a_worker_while_queued(self) -> None:
        scheduler = OperationScheduler(self.sink, max_workers=2)
        first_started = threading.Event()
        second_started = threading.Event()
        other_started = threading.Event()
        release_first = threading.Event()
        release_other = threading.Event()
        order: list[str] = []
        order_lock = threading.Lock()

        def execute(context: Any) -> str:
            with order_lock:
                order.append(context.request_id)
            if context.request_id == "first":
                first_started.set()
                wait_for(release_first)
            elif context.request_id == "second":
                second_started.set()
            else:
                other_started.set()
                wait_for(release_other)
            return context.request_id

        try:
            scheduler.submit("first", execute, key="library-a")
            wait_for(first_started)
            scheduler.submit("second", execute, key="library-a")
            scheduler.submit("other", execute, key="library-b")
            wait_for(other_started)
            self.assertFalse(second_started.is_set())
            with order_lock:
                self.assertEqual(order, ["first", "other"])
            release_first.set()
            wait_for(second_started)
            release_other.set()
        finally:
            scheduler.close()

        with order_lock:
            self.assertEqual(order, ["first", "other", "second"])

    def test_progress_is_monotonic_and_terminal_is_emitted_once(self) -> None:
        scheduler = OperationScheduler(self.sink, max_workers=1)

        def execute(context: Any) -> dict[str, str]:
            self.assertTrue(context.report_progress(0))
            self.assertTrue(context.report_progress(0.5, stage="half"))
            with self.assertRaises(ValueError):
                context.report_progress(0.25)
            return {"state": "ready"}

        try:
            scheduler.submit("progress", execute)
            self.wait_for_terminal("progress")
        finally:
            scheduler.close()

        events = self.events_for("progress")
        self.assertEqual([event["sequence"] for event in events], [0, 1, 2, 3])
        self.assertEqual([event["type"] for event in events], ["accepted", "progress", "progress", "succeeded"])
        self.assertEqual(events[2]["progress"], 0.5)
        self.assertEqual(events[-1]["result"], {"state": "ready"})

    def test_event_sink_is_serialized_across_workers(self) -> None:
        state_lock = threading.Lock()
        progress_barrier = threading.Barrier(2)
        first_progress = threading.Event()
        release_sink = threading.Event()
        active = 0
        maximum_active = 0
        progress_seen = 0

        def sink(event: dict[str, Any]) -> None:
            nonlocal active, maximum_active, progress_seen
            if event["type"] != "progress":
                self.sink(event)
                return
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
                progress_seen += 1
                first = progress_seen == 1
            if first:
                first_progress.set()
                wait_for(release_sink)
            with state_lock:
                active -= 1
            self.sink(event)

        scheduler = OperationScheduler(sink, max_workers=2)

        def execute(context: Any) -> str:
            progress_barrier.wait(1.0)
            context.report_progress(1)
            return context.request_id

        try:
            scheduler.submit("one", execute)
            scheduler.submit("two", execute)
            wait_for(first_progress)
            with state_lock:
                self.assertEqual(maximum_active, 1)
            release_sink.set()
            self.wait_for_terminal("one")
            self.wait_for_terminal("two")
        finally:
            scheduler.close()

    def test_queued_cancellation_is_idempotent_and_skips_executor(self) -> None:
        scheduler = OperationScheduler(self.sink, max_workers=1)
        running_started = threading.Event()
        release_running = threading.Event()
        queued_started = threading.Event()

        def running(context: Any) -> str:
            running_started.set()
            wait_for(release_running)
            return "running"

        def queued(context: Any) -> str:
            queued_started.set()
            return "queued"

        try:
            scheduler.submit("running", running)
            wait_for(running_started)
            scheduler.submit("queued", queued)
            self.assertTrue(scheduler.cancel("queued"))
            self.assertFalse(scheduler.cancel("queued"))
            release_running.set()
            self.wait_for_terminal("running")
        finally:
            scheduler.close()

        self.assertFalse(queued_started.is_set())
        self.assertEqual(self.event_types("queued"), ["accepted", "cancelled"])
        self.assertEqual(self.event_types("running"), ["accepted", "succeeded"])

    def test_running_cancellation_invokes_terminator_once(self) -> None:
        scheduler = OperationScheduler(self.sink, max_workers=1)
        started = threading.Event()
        terminated = threading.Event()
        release = threading.Event()
        terminator_calls = 0
        calls_lock = threading.Lock()

        def execute(context: Any) -> str:
            nonlocal terminator_calls

            def terminate_process() -> None:
                nonlocal terminator_calls
                with calls_lock:
                    terminator_calls += 1
                terminated.set()

            context.register_terminator(terminate_process)
            started.set()
            wait_for(release)
            context.check_cancelled()
            return "unreachable"

        try:
            scheduler.submit("running", execute)
            wait_for(started)
            self.assertTrue(scheduler.cancel("running"))
            self.assertFalse(scheduler.cancel("running"))
            wait_for(terminated)
            release.set()
        finally:
            scheduler.close()

        self.assertEqual(terminator_calls, 1)
        self.assertEqual(self.event_types("running"), ["accepted", "cancelled"])

    def test_late_cancellation_cannot_cancel_committed_work(self) -> None:
        scheduler = OperationScheduler(self.sink, max_workers=1)
        started = threading.Event()
        committed = threading.Event()
        release = threading.Event()
        terminator_calls: list[None] = []

        def execute(context: Any) -> str:
            context.register_terminator(lambda: terminator_calls.append(None))
            started.set()
            self.assertTrue(context.begin_commit())
            committed.set()
            wait_for(release)
            return "committed"

        try:
            scheduler.submit("commit", execute)
            wait_for(started)
            wait_for(committed)
            self.assertFalse(scheduler.cancel("commit"))
            release.set()
        finally:
            scheduler.close()

        self.assertEqual(terminator_calls, [])
        self.assertEqual(self.event_types("commit"), ["accepted", "succeeded"])

    def test_close_cancels_queued_and_precommit_work_then_joins(self) -> None:
        scheduler = OperationScheduler(self.sink, max_workers=1)
        started = threading.Event()
        terminated = threading.Event()
        release = threading.Event()
        queued_started = threading.Event()

        def running(context: Any) -> str:
            context.register_terminator(terminated.set)
            started.set()
            wait_for(release)
            context.check_cancelled()
            return "unreachable"

        def queued(context: Any) -> str:
            queued_started.set()
            return "unreachable"

        scheduler.submit("running", running)
        wait_for(started)
        scheduler.submit("queued", queued)

        close_thread = threading.Thread(target=scheduler.close)
        close_thread.start()
        wait_for(terminated)
        self.assertTrue(close_thread.is_alive())
        release.set()
        close_thread.join(1.0)
        self.assertFalse(close_thread.is_alive())
        self.assertFalse(queued_started.is_set())
        self.assertEqual(self.event_types("running"), ["accepted", "cancelled"])
        self.assertEqual(self.event_types("queued"), ["accepted", "cancelled"])
        with self.assertRaises(SchedulerClosed):
            scheduler.submit("after-close", lambda context: None)


if __name__ == "__main__":
    unittest.main()
