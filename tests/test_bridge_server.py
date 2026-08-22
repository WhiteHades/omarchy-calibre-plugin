from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch
from typing import Any

from backend.bridge_server import OperationContext, OperationScheduler, SchedulerClosed


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

    def test_concurrent_cancel_cannot_overtake_acceptance(self) -> None:
        scheduler = OperationScheduler(self.sink, max_workers=1)
        acceptance_entered = threading.Event()
        release_acceptance = threading.Event()
        executor_release = threading.Event()
        original = OperationContext._emit_accepted

        def delayed_acceptance(context: OperationContext) -> None:
            acceptance_entered.set()
            wait_for(release_acceptance)
            original(context)

        submit_thread = threading.Thread(
            target=lambda: scheduler.submit(
                "cancel-race",
                lambda context: executor_release.wait(1),
            )
        )
        cancel_thread = threading.Thread(target=lambda: scheduler.cancel("cancel-race"))
        try:
            with patch.object(OperationContext, "_emit_accepted", delayed_acceptance):
                submit_thread.start()
                wait_for(acceptance_entered)
                cancel_thread.start()
                time.sleep(0.02)
                release_acceptance.set()
                submit_thread.join(1)
                cancel_thread.join(1)
            executor_release.set()
            self.wait_for_terminal("cancel-race")
        finally:
            release_acceptance.set()
            executor_release.set()
            submit_thread.join(1)
            cancel_thread.join(1)
            scheduler.close()

        self.assertEqual(self.event_types("cancel-race"), ["accepted", "cancelled"])

    def test_reentrant_close_from_acceptance_sink_does_not_deadlock(self) -> None:
        events: list[dict[str, Any]] = []
        scheduler_holder: dict[str, OperationScheduler] = {}

        def close_on_acceptance(event: dict[str, Any]) -> None:
            events.append(event)
            if event["type"] == "accepted":
                scheduler_holder["scheduler"].close()

        scheduler = OperationScheduler(close_on_acceptance, max_workers=1)
        scheduler_holder["scheduler"] = scheduler
        submit_thread = threading.Thread(
            target=lambda: scheduler.submit("close-reentrant", lambda context: None),
            daemon=True,
        )

        submit_thread.start()
        submit_thread.join(1)

        self.assertFalse(submit_thread.is_alive(), "re-entrant close deadlocked submit")
        self.assertEqual(
            [event["type"] for event in events],
            ["accepted", "cancelled"],
        )

    def test_concurrent_close_waits_for_acceptance_and_cancellation(self) -> None:
        scheduler = OperationScheduler(self.sink, max_workers=1)
        acceptance_entered = threading.Event()
        release_acceptance = threading.Event()
        original = OperationContext._emit_accepted

        def delayed_acceptance(context: OperationContext) -> None:
            acceptance_entered.set()
            wait_for(release_acceptance)
            original(context)

        submit_thread = threading.Thread(
            target=lambda: scheduler.submit("close-race", lambda context: None)
        )
        close_thread = threading.Thread(target=scheduler.close)
        try:
            with patch.object(OperationContext, "_emit_accepted", delayed_acceptance):
                submit_thread.start()
                wait_for(acceptance_entered)
                close_thread.start()
                time.sleep(0.02)
                self.assertTrue(close_thread.is_alive())
                release_acceptance.set()
                submit_thread.join(1)
                close_thread.join(1)
        finally:
            release_acceptance.set()
            submit_thread.join(1)
            close_thread.join(1)
            scheduler.close()

        self.assertFalse(submit_thread.is_alive())
        self.assertFalse(close_thread.is_alive())
        self.assertEqual(self.event_types("close-race"), ["accepted", "cancelled"])

    def test_reentrant_close_does_not_wait_on_another_acceptance(self) -> None:
        scheduler_holder: dict[str, OperationScheduler] = {}
        second_entered = threading.Event()
        second_thread_holder: dict[str, threading.Thread] = {}
        original = OperationContext._emit_accepted

        def observe_second(context: OperationContext) -> None:
            if context.request_id == "second-acceptance":
                second_entered.set()
            original(context)

        def close_with_another_acceptance(event: dict[str, Any]) -> None:
            self.sink(event)
            if event["id"] != "first-acceptance" or event["type"] != "accepted":
                return
            second_thread = threading.Thread(
                target=lambda: scheduler_holder["scheduler"].submit(
                    "second-acceptance",
                    lambda context: None,
                ),
                daemon=True,
            )
            second_thread_holder["thread"] = second_thread
            second_thread.start()
            wait_for(second_entered)
            scheduler_holder["scheduler"].close()

        scheduler = OperationScheduler(close_with_another_acceptance, max_workers=1)
        scheduler_holder["scheduler"] = scheduler
        first_thread = threading.Thread(
            target=lambda: scheduler.submit("first-acceptance", lambda context: None),
            daemon=True,
        )

        with patch.object(OperationContext, "_emit_accepted", observe_second):
            first_thread.start()
            first_thread.join(1)
            second_thread = second_thread_holder.get("thread")
            if second_thread is not None:
                second_thread.join(1)

        self.assertFalse(first_thread.is_alive(), "re-entrant close deadlocked the accepting thread")
        self.assertIsNotNone(second_thread)
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(self.event_types("first-acceptance"), ["accepted", "cancelled"])
        self.assertEqual(self.event_types("second-acceptance"), ["accepted", "cancelled"])

    def test_reentrant_close_does_not_join_a_worker_waiting_on_the_sink(self) -> None:
        scheduler_holder: dict[str, OperationScheduler] = {}
        running_started = threading.Event()
        release_running = threading.Event()

        def close_from_trigger(event: dict[str, Any]) -> None:
            self.sink(event)
            if event["id"] == "close-trigger" and event["type"] == "accepted":
                scheduler_holder["scheduler"].close()

        def run_until_cancelled(context: OperationContext) -> None:
            context.register_terminator(release_running.set)
            running_started.set()
            wait_for(release_running)
            context.check_cancelled()

        scheduler = OperationScheduler(close_from_trigger, max_workers=1)
        scheduler_holder["scheduler"] = scheduler
        scheduler.submit("running-during-close", run_until_cancelled)
        wait_for(running_started)
        trigger_thread = threading.Thread(
            target=lambda: scheduler.submit("close-trigger", lambda context: None),
            daemon=True,
        )

        try:
            trigger_thread.start()
            trigger_thread.join(1)
            self.assertFalse(
                trigger_thread.is_alive(),
                "re-entrant close joined a worker blocked on the event sink",
            )
            self.wait_for_terminal("running-during-close")
            self.wait_for_terminal("close-trigger")
        finally:
            release_running.set()

        self.assertEqual(
            self.event_types("running-during-close"),
            ["accepted", "cancelled"],
        )
        self.assertEqual(self.event_types("close-trigger"), ["accepted", "cancelled"])

    def test_close_finishes_a_claimed_queued_cancellation_before_return(self) -> None:
        scheduler = OperationScheduler(self.sink, max_workers=1)
        running_started = threading.Event()
        release_running = threading.Event()
        cancel_entered = threading.Event()
        release_cancel = threading.Event()
        original = OperationScheduler._cancel_unstarted

        def run_lane_head(context: OperationContext) -> None:
            context.register_terminator(release_running.set)
            running_started.set()
            wait_for(release_running)
            context.check_cancelled()

        def delay_cancel(
            active_scheduler: OperationScheduler,
            operation: Any,
        ) -> bool:
            if threading.current_thread().name == "queued-canceller":
                cancel_entered.set()
                wait_for(release_cancel)
            return original(active_scheduler, operation)

        scheduler.submit("lane-head", run_lane_head, key="library")
        scheduler.submit("queued-cancel", lambda context: None, key="library")
        wait_for(running_started)
        cancel_thread = threading.Thread(
            target=lambda: scheduler.cancel("queued-cancel"),
            name="queued-canceller",
        )
        close_thread = threading.Thread(target=scheduler.close)

        try:
            with patch.object(OperationScheduler, "_cancel_unstarted", delay_cancel):
                cancel_thread.start()
                wait_for(cancel_entered)
                close_thread.start()
                close_thread.join(1)
                self.assertFalse(close_thread.is_alive())
                self.assertEqual(
                    self.event_types("queued-cancel"),
                    ["accepted", "cancelled"],
                )
        finally:
            release_cancel.set()
            release_running.set()
            cancel_thread.join(1)
            close_thread.join(1)

        self.assertFalse(cancel_thread.is_alive())

    def test_worker_cannot_remove_claimed_work_before_its_terminal_event(self) -> None:
        scheduler = OperationScheduler(self.sink, max_workers=1)
        head_started = threading.Event()
        release_head = threading.Event()
        cancel_entered = threading.Event()
        release_cancel = threading.Event()
        original = OperationScheduler._cancel_unstarted

        def run_lane_head(context: OperationContext) -> None:
            head_started.set()
            wait_for(release_head)

        def delay_cancel(
            active_scheduler: OperationScheduler,
            operation: Any,
        ) -> bool:
            if threading.current_thread().name == "removal-race-canceller":
                cancel_entered.set()
                wait_for(release_cancel)
            return original(active_scheduler, operation)

        scheduler.submit("removal-race-head", run_lane_head)
        scheduler.submit("removal-race-queued", lambda context: None)
        wait_for(head_started)
        cancel_thread = threading.Thread(
            target=lambda: scheduler.cancel("removal-race-queued"),
            name="removal-race-canceller",
        )
        close_thread = threading.Thread(target=scheduler.close)

        try:
            with patch.object(OperationScheduler, "_cancel_unstarted", delay_cancel):
                cancel_thread.start()
                wait_for(cancel_entered)
                release_head.set()
                with scheduler._condition:
                    removed = scheduler._condition.wait_for(
                        lambda: "removal-race-queued" not in scheduler._operations,
                        timeout=1,
                    )
                self.assertTrue(removed)
                close_thread.start()
                close_thread.join(1)
                self.assertFalse(close_thread.is_alive())
                self.assertEqual(
                    self.event_types("removal-race-queued"),
                    ["accepted", "cancelled"],
                )
        finally:
            release_head.set()
            release_cancel.set()
            cancel_thread.join(1)
            close_thread.join(1)

        self.assertFalse(cancel_thread.is_alive())

    def test_keyed_lane_cannot_drop_claimed_work_before_its_terminal_event(self) -> None:
        scheduler = OperationScheduler(self.sink, max_workers=1)
        head_started = threading.Event()
        release_head = threading.Event()
        cancel_entered = threading.Event()
        release_cancel = threading.Event()
        original = OperationScheduler._cancel_unstarted

        def run_lane_head(context: OperationContext) -> None:
            head_started.set()
            wait_for(release_head)

        def delay_cancel(
            active_scheduler: OperationScheduler,
            operation: Any,
        ) -> bool:
            if threading.current_thread().name == "keyed-removal-canceller":
                cancel_entered.set()
                wait_for(release_cancel)
            return original(active_scheduler, operation)

        scheduler.submit("keyed-removal-head", run_lane_head, key="library")
        scheduler.submit(
            "keyed-removal-queued",
            lambda context: None,
            key="library",
        )
        wait_for(head_started)
        cancel_thread = threading.Thread(
            target=lambda: scheduler.cancel("keyed-removal-queued"),
            name="keyed-removal-canceller",
        )
        close_thread = threading.Thread(target=scheduler.close)

        try:
            with patch.object(OperationScheduler, "_cancel_unstarted", delay_cancel):
                cancel_thread.start()
                wait_for(cancel_entered)
                release_head.set()
                with scheduler._condition:
                    removed = scheduler._condition.wait_for(
                        lambda: "keyed-removal-queued" not in scheduler._operations,
                        timeout=1,
                    )
                self.assertTrue(removed)
                close_thread.start()
                close_thread.join(1)
                self.assertFalse(close_thread.is_alive())
                self.assertEqual(
                    self.event_types("keyed-removal-queued"),
                    ["accepted", "cancelled"],
                )
        finally:
            release_head.set()
            release_cancel.set()
            cancel_thread.join(1)
            close_thread.join(1)

        self.assertFalse(cancel_thread.is_alive())

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
