"""Dependency-free operation scheduling for the persistent Calibre bridge.

The bridge protocol is deliberately kept out of this module.  The scheduler
owns operation lifecycle and ordering while an integration layer translates
the event dictionaries to its transport format.

Typical use::

    scheduler = OperationScheduler(events.append, max_workers=4)
    scheduler.submit("request-7", run_operation, key="library-token")
    scheduler.cancel("request-7")
    scheduler.close()

``run_operation`` receives an :class:`OperationContext`.  It can call
``report_progress``, ``check_cancelled``, ``register_terminator``, and
``begin_commit``.  A successful return value becomes the operation result.
Exceptions become failed terminal events, except for
:class:`OperationCancelled`, which becomes a cancelled terminal event.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import numbers
import threading
from typing import Any, Callable, Deque, Hashable, Mapping


Event = dict[str, Any]
EventSink = Callable[[Event], None]
Executor = Callable[["OperationContext"], Any]
Terminator = Callable[[], None]


class OperationCancelled(Exception):
    """Raised by ``check_cancelled`` when pre-commit work was cancelled."""


class SchedulerClosed(RuntimeError):
    """Raised when an operation is submitted after the scheduler was closed."""


class DuplicateRequest(ValueError):
    """Raised when a request id is submitted more than once."""


@dataclass(frozen=True)
class _Cancellation:
    changed: bool
    callbacks: tuple[Terminator, ...]


class OperationContext:
    """Thread-safe state and event methods for one scheduled operation.

    The scheduler emits ``accepted`` with sequence ``0`` before it makes the
    executor runnable.  Progress events receive increasing sequence numbers;
    numeric progress values must not decrease.  The scheduler emits exactly
    one terminal event (``succeeded``, ``failed``, or ``cancelled``).

    ``begin_commit`` is the cancellation fence for mutations.  It atomically
    changes the operation to committed when cancellation has not won the race.
    Cancellation requested after that transition is ignored for lifecycle
    purposes and does not invoke a registered process terminator.
    """

    def __init__(
        self,
        request_id: str,
        event_sink: EventSink,
        sink_lock: threading.RLock,
    ) -> None:
        self.request_id = request_id
        self._event_sink = event_sink
        self._sink_lock = sink_lock
        self._lock = threading.RLock()
        self._next_sequence = 0
        self._last_progress: numbers.Real | None = None
        self._cancel_requested = False
        self._committed = False
        self._terminal = False
        self._terminators: list[Terminator] = []
        self._terminators_invoked = False

    @property
    def id(self) -> str:
        """Alias for ``request_id`` used by transport adapters."""

        return self.request_id

    @property
    def committed(self) -> bool:
        """Whether the operation crossed its cancellation fence."""

        with self._lock:
            return self._committed

    @property
    def terminal(self) -> bool:
        """Whether the one terminal event has already been emitted."""

        with self._lock:
            return self._terminal

    def is_cancelled(self) -> bool:
        """Return whether cancellation won before commit."""

        with self._lock:
            return self._cancel_requested and not self._committed

    def check_cancelled(self) -> None:
        """Raise :class:`OperationCancelled` for cooperative cancellation."""

        if self.is_cancelled():
            raise OperationCancelled(self.request_id)

    def register_terminator(self, callback: Terminator) -> None:
        """Register a process terminator, invoked once if cancellation wins.

        Registration after cancellation invokes the callback immediately.  A
        callback registered after commit is ignored because there is no
        pre-commit process left to terminate.
        """

        if not callable(callback):
            raise TypeError("terminator must be callable")

        invoke_now = False
        with self._lock:
            if self._committed or self._terminal:
                return
            if self._cancel_requested:
                invoke_now = True
            else:
                self._terminators.append(callback)

        if invoke_now:
            self._invoke_terminators((callback,))

    # The longer name reads naturally at integration call sites.
    register_process_terminator = register_terminator

    def report_progress(
        self,
        progress: Any = None,
        **details: Any,
    ) -> bool:
        """Emit one progress event and return whether it was emitted.

        Numeric progress values are monotonic.  Progress after a pre-commit
        cancellation or after the terminal event is a no-op; this prevents
        stale work from writing events after cancellation.  Arbitrary details
        can be attached as event fields.
        """

        with self._lock:
            if self._terminal or self._cancel_requested and not self._committed:
                return False
            if {"id", "sequence", "type"}.intersection(details):
                raise ValueError("progress details cannot replace event identity")
            if isinstance(progress, numbers.Real) and not isinstance(progress, bool):
                if self._last_progress is not None and progress < self._last_progress:
                    raise ValueError("progress must be monotonic")
                self._last_progress = progress
            event = self._event_locked("progress", progress=progress, **details)
            self._deliver_locked(event)
            return True

    # Short spelling for executors that report progress frequently.
    progress = report_progress

    def begin_commit(self) -> bool:
        """Atomically enter committed state, returning whether it succeeded.

        A repeated call after a successful transition returns ``True``.  A
        cancellation that won first, or a terminal operation, returns
        ``False``.
        """

        with self._lock:
            if self._committed:
                return True
            if self._cancel_requested or self._terminal:
                return False
            self._committed = True
            return True

    def succeed(self, result: Any = None) -> bool:
        """Emit a successful terminal event if the operation is still live."""

        return self._finish("succeeded", result=result)

    def fail(self, error: Any) -> bool:
        """Emit a failed terminal event if the operation is still live."""

        return self._finish("failed", error=_error_payload(error))

    def _emit_accepted(self) -> None:
        with self._lock:
            event = self._event_locked("accepted")
            self._deliver_locked(event)

    def _request_cancel(self, *, queued: bool) -> _Cancellation:
        """Record cancellation and optionally finish a queued operation."""

        with self._lock:
            if self._terminal or self._committed:
                return _Cancellation(False, ())
            if self._cancel_requested:
                return _Cancellation(False, ())
            self._cancel_requested = True
            callbacks = self._claim_terminators_locked()
            if queued:
                self._terminal = True
                event = self._event_locked("cancelled")
                self._deliver_locked(event)

        return _Cancellation(True, callbacks)

    def _finish(self, event_type: str, **fields: Any) -> bool:
        with self._lock:
            if self._terminal:
                return False
            if self._cancel_requested and not self._committed:
                event_type = "cancelled"
                fields = {}
            self._terminal = True
            event = self._event_locked(event_type, **fields)
            self._deliver_locked(event)
            return True

    def _claim_terminators_locked(self) -> tuple[Terminator, ...]:
        if self._terminators_invoked:
            return ()
        self._terminators_invoked = True
        callbacks = tuple(self._terminators)
        self._terminators.clear()
        return callbacks

    def _invoke_terminators(self, callbacks: tuple[Terminator, ...]) -> None:
        for callback in callbacks:
            try:
                callback()
            except Exception:
                # A failed best-effort process termination must not prevent
                # the scheduler from delivering the cancellation lifecycle.
                continue

    def _event_locked(self, event_type: str, **fields: Any) -> Event:
        event: Event = {
            "id": self.request_id,
            "sequence": self._next_sequence,
            "type": event_type,
        }
        self._next_sequence += 1
        event.update(fields)
        return event

    def _deliver_locked(self, event: Event) -> None:
        # Holding the context lock while taking the shared sink lock preserves
        # per-request sequence order when cancellation races with progress.
        with self._sink_lock:
            self._event_sink(event)


def _error_payload(error: Any) -> dict[str, Any]:
    if isinstance(error, Mapping):
        return dict(error)
    if isinstance(error, BaseException):
        message = str(error)
    else:
        message = str(error)
    return {"code": "operation_error", "message": message}


@dataclass
class _Operation:
    context: OperationContext
    executor: Executor
    key: Hashable | None
    started: bool = False
    cancel_claimed: bool = False
    done: bool = False


class OperationScheduler:
    """Bounded worker pool with FIFO keyed lanes and lifecycle events.

    ``event_sink`` is the one callback used for all operation events.  It is
    invoked serially across submitters and worker threads.  ``key=None``
    schedules an unkeyed operation directly, so independent reads can use all
    workers.  A non-``None`` key creates a FIFO lane: only the lane head is
    eligible for a worker, and queued successors do not consume workers.

    ``submit`` returns the operation context.  Use ``cancel(request_id)`` to
    request additive, idempotent cancellation.  ``close`` rejects new work,
    cancels queued and pre-commit operations, and joins all worker threads.
    """

    def __init__(
        self,
        event_sink: EventSink | None = None,
        *,
        max_workers: int = 4,
    ) -> None:
        if not isinstance(max_workers, int) or isinstance(max_workers, bool) or max_workers < 1:
            raise ValueError("max_workers must be a positive integer")
        self._event_sink = event_sink or (lambda event: None)
        if not callable(self._event_sink):
            raise TypeError("event_sink must be callable")
        self._sink_lock = threading.RLock()
        self._condition = threading.Condition(threading.RLock())
        self._ready: Deque[_Operation] = deque()
        self._lanes: dict[Hashable, Deque[_Operation]] = {}
        self._operations: dict[str, _Operation] = {}
        self._known_request_ids: set[str] = set()
        self._closed = False
        self._workers = [
            threading.Thread(
                target=self._worker,
                name=f"bridge-worker-{index}",
                daemon=True,
            )
            for index in range(max_workers)
        ]
        for worker in self._workers:
            worker.start()

    def submit(
        self,
        request_id: str,
        executor: Executor,
        *,
        key: Hashable | None = None,
    ) -> OperationContext:
        """Accept and schedule an executor, returning its operation context."""

        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id must be a non-empty string")
        if not callable(executor):
            raise TypeError("executor must be callable")
        if key is not None:
            hash(key)

        context = OperationContext(request_id, self._event_sink, self._sink_lock)
        operation = _Operation(context, executor, key)
        with self._condition:
            if self._closed:
                raise SchedulerClosed("operation scheduler is closed")
            if request_id in self._known_request_ids:
                raise DuplicateRequest(request_id)
            self._known_request_ids.add(request_id)
            self._operations[request_id] = operation

        # This call intentionally precedes the queue operation.  It also
        # leaves a small but useful integration seam: a sink can observe an
        # accepted request before any executor-side progress is possible.
        context._emit_accepted()

        cancel_on_submit = False
        with self._condition:
            if self._closed or operation.cancel_claimed or context.terminal:
                if not operation.cancel_claimed and not operation.done:
                    operation.cancel_claimed = True
                    operation.done = True
                    cancel_on_submit = True
                if operation.done:
                    # Cancellation can be re-entrant from the accepted-event
                    # sink, before this operation has entered a queue.
                    self._operations.pop(request_id, None)
            else:
                self._enqueue_locked(operation)
                self._condition.notify()

        if cancel_on_submit:
            self._cancel_unstarted(operation)
        return context

    def cancel(self, request_id: str) -> bool:
        """Request cancellation and return whether this call changed state.

        Repeated cancellation is a no-op.  A committed or already terminal
        operation is not cancellable and returns ``False``.
        """

        with self._condition:
            operation = self._operations.get(request_id)
            if operation is None or operation.done:
                return False
            if not operation.started:
                if operation.cancel_claimed:
                    return False
                operation.cancel_claimed = True
                operation.done = True
                queued = True
            else:
                queued = False

        if queued:
            return self._cancel_unstarted(operation)

        cancellation = operation.context._request_cancel(queued=False)
        operation.context._invoke_terminators(cancellation.callbacks)
        with self._condition:
            self._condition.notify_all()
        return cancellation.changed

    def close(self) -> None:
        """Cancel pending work, reject submissions, and join worker threads."""

        queued: list[_Operation] = []
        running: list[_Operation] = []
        with self._condition:
            self._closed = True
            for operation in tuple(self._operations.values()):
                if operation.done:
                    continue
                if operation.started:
                    running.append(operation)
                else:
                    operation.cancel_claimed = True
                    operation.done = True
                    queued.append(operation)
            self._condition.notify_all()

        for operation in queued:
            self._cancel_unstarted(operation)
        for operation in running:
            cancellation = operation.context._request_cancel(queued=False)
            operation.context._invoke_terminators(cancellation.callbacks)

        with self._condition:
            self._condition.notify_all()
        current = threading.current_thread()
        for worker in self._workers:
            if worker is not current:
                worker.join()

    def __enter__(self) -> "OperationScheduler":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        del exc_type, exc_value, traceback
        self.close()

    def _enqueue_locked(self, operation: _Operation) -> None:
        if operation.key is None:
            self._ready.append(operation)
            return
        lane = self._lanes.setdefault(operation.key, deque())
        lane.append(operation)
        if len(lane) == 1:
            self._ready.append(operation)

    def _cancel_unstarted(self, operation: _Operation) -> bool:
        cancellation = operation.context._request_cancel(queued=True)
        operation.context._invoke_terminators(cancellation.callbacks)
        with self._condition:
            self._condition.notify_all()
        return cancellation.changed

    def _worker(self) -> None:
        while True:
            with self._condition:
                while not self._ready and not self._closed:
                    self._condition.wait()
                if not self._ready and self._closed:
                    return
                operation = self._ready.popleft()
                if operation.done or operation.cancel_claimed or operation.context.terminal:
                    self._operation_done_locked(operation)
                    continue
                operation.started = True

            context = operation.context
            try:
                context.check_cancelled()
                result = operation.executor(context)
            except OperationCancelled as error:
                if context.committed:
                    context.fail(error)
                else:
                    context._finish("cancelled")
            except BaseException as error:
                context.fail(error)
            else:
                context.succeed(result)
            finally:
                with self._condition:
                    self._operation_done_locked(operation)

    def _operation_done_locked(self, operation: _Operation) -> None:
        operation.done = True
        self._operations.pop(operation.context.request_id, None)
        if operation.key is None:
            self._condition.notify_all()
            return

        lane = self._lanes.get(operation.key)
        if lane is None:
            self._condition.notify_all()
            return
        if lane and lane[0] is operation:
            lane.popleft()
        while lane and lane[0].done:
            stale = lane.popleft()
            self._operations.pop(stale.context.request_id, None)
        if lane:
            self._ready.append(lane[0])
            self._condition.notify()
        else:
            self._lanes.pop(operation.key, None)
            self._condition.notify_all()


# The explicit alias makes the integration intent discoverable without
# duplicating the scheduler implementation.
BridgeScheduler = OperationScheduler


__all__ = [
    "BridgeScheduler",
    "DuplicateRequest",
    "OperationCancelled",
    "OperationContext",
    "OperationScheduler",
    "SchedulerClosed",
]
