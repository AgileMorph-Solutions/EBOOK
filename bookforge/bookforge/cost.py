"""Per-book budget guardrail.

A process-local accumulator that hard-stops the pipeline if spend exceeds the
configured cap. In the Celery deployment this is backed by a Redis counter keyed
per book_id (see workflow.tasks) so all workers share one running total.
"""
from __future__ import annotations

import threading

import structlog

from bookforge.config import settings

log = structlog.get_logger(__name__)


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, cap_usd: float) -> None:
        self.cap = cap_usd
        self._spent = 0.0
        self._lock = threading.Lock()

    def charge(self, usd: float) -> None:
        with self._lock:
            self._spent += usd

    def reset(self) -> None:
        """Zero the accumulator. Called at the top of each pipeline attempt so
        retries don't inherit prior spend."""
        with self._lock:
            self._spent = 0.0

    @property
    def spent(self) -> float:
        return self._spent

    def assert_ok(self) -> None:
        # cap <= 0 disables the guardrail entirely (PER_BOOK_BUDGET_USD=0).
        if self.cap > 0 and self._spent >= self.cap:
            raise BudgetExceeded(
                f"Per-book budget ${self.cap:.2f} exhausted (spent ${self._spent:.2f})"
            )


budget = Budget(settings.per_book_budget_usd)
