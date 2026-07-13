"""Cooperative per-job run context: deadline, cancellation, heartbeat.

The API binds a JobRun around run_book(); `checkpoint()` is called at every
LLM/image call boundary (next to budget.assert_ok()) and at stage boundaries.
That gives three guarantees without ever killing a thread:

- a cancelled job stops at the next call boundary (PipelineCancelledError);
- a wedged job cannot hold a concurrency slot past its wall-clock deadline
  (JobDeadlineError -> park with checkpoints intact, resume loses nothing);
- status.json carries a fresh heartbeat_at, so the watchdog can tell a live
  slow job from a dead one.

Propagates into worker pools only via pipeline.pools.ctx_pool.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator

from bookforge.errors import JobDeadlineError, PipelineCancelledError

_HEARTBEAT_MIN_INTERVAL_S = 15.0

_current: ContextVar["JobRun | None"] = ContextVar("bookforge_job_run", default=None)


class JobRun:
    def __init__(
        self,
        book_id: str,
        *,
        deadline_ts: float | None = None,
        cancel_cb: Callable[[], bool] | None = None,
        heartbeat_cb: Callable[[], None] | None = None,
    ) -> None:
        self.book_id = book_id
        self.deadline_ts = deadline_ts
        self._cancel_cb = cancel_cb
        self._heartbeat_cb = heartbeat_cb
        self._last_beat = 0.0

    def checkpoint(self) -> None:
        if self._cancel_cb is not None and self._cancel_cb():
            raise PipelineCancelledError(f"book {self.book_id} cancelled")
        now = time.monotonic()
        if self.deadline_ts is not None and now >= self.deadline_ts:
            raise JobDeadlineError(
                f"book {self.book_id} exceeded its per-attempt time budget"
            )
        if self._heartbeat_cb is not None and now - self._last_beat >= _HEARTBEAT_MIN_INTERVAL_S:
            self._last_beat = now
            try:
                self._heartbeat_cb()
            except Exception:  # noqa: BLE001 — a failed beat must never kill work
                pass


@contextmanager
def bind(run: JobRun) -> Iterator[JobRun]:
    token = _current.set(run)
    try:
        yield run
    finally:
        _current.reset(token)


def checkpoint() -> None:
    """No-op when no JobRun is bound (CLI spikes, scripts, tests)."""
    run = _current.get()
    if run is not None:
        run.checkpoint()
