"""Per-book budget guardrail.

Each pipeline run binds its own `Budget` (keyed by book_id) via `bind()`; the
module-level `budget` is a proxy that resolves the current book's accumulator
through a ContextVar, so call sites keep the plain `budget.charge()/assert_ok()`
surface. Code that never binds (CLI spikes, scripts) falls back to a shared
process-global accumulator — same behavior as before, but API books no longer
pollute each other or inherit spend across books/retries.

ContextVars do NOT propagate into ThreadPoolExecutor workers on their own —
worker pools inside the pipeline must use `pipeline.pools.ctx_pool()`, which
copies the submitting context into each task.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

import structlog

from bookforge.config import settings

log = structlog.get_logger(__name__)


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, cap_usd: float, initial_spent: float = 0.0) -> None:
        self.cap = cap_usd
        self._spent = float(initial_spent)
        self._lock = threading.Lock()

    def charge(self, usd: float) -> None:
        with self._lock:
            self._spent += usd

    def reset(self) -> None:
        """Zero the accumulator (fresh pipeline attempt with no prior spend)."""
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


_books: dict[str, Budget] = {}
_books_lock = threading.Lock()
_current: ContextVar[str | None] = ContextVar("bookforge_budget_book", default=None)

# Fallback for unbound contexts (CLI spike, scripts, tests that don't bind).
_default = Budget(settings.per_book_budget_usd)


@contextmanager
def bind(book_id: str, cap_usd: float | None = None,
         initial_spent: float = 0.0) -> Iterator[Budget]:
    """Give the current context (and ctx_pool children) its own budget.

    `initial_spent` carries spend across park/resume cycles (loaded from the
    spend.json checkpoint) so a resumed book doesn't get a fresh cap.
    """
    b = Budget(settings.per_book_budget_usd if cap_usd is None else cap_usd,
               initial_spent=initial_spent)
    with _books_lock:
        _books[book_id] = b
    token = _current.set(book_id)
    try:
        yield b
    finally:
        _current.reset(token)
        with _books_lock:
            _books.pop(book_id, None)


class _BudgetProxy:
    """Resolves to the bound book's Budget, else the process-global default.

    Keeps the historical `from bookforge.cost import budget` call sites working
    unchanged (`budget.charge(...)`, `budget.assert_ok()`, `budget.spent`).
    """

    def _resolve(self) -> Budget:
        book_id = _current.get()
        if book_id is not None:
            with _books_lock:
                b = _books.get(book_id)
            if b is not None:
                return b
        return _default

    def charge(self, usd: float) -> None:
        self._resolve().charge(usd)

    def reset(self) -> None:
        self._resolve().reset()

    def assert_ok(self) -> None:
        self._resolve().assert_ok()

    @property
    def spent(self) -> float:
        return self._resolve().spent

    @property
    def cap(self) -> float:
        return self._resolve().cap


budget = _BudgetProxy()
