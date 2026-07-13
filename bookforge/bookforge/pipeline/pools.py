"""Context-propagating thread pool for pipeline fan-out.

Every worker pool in the pipeline MUST come from `ctx_pool()`: it copies the
submitting thread's contextvars into each task, so the per-book budget
(bookforge.cost) and job run context (bookforge.pipeline.runctx) resolve
correctly inside workers. A bare ThreadPoolExecutor would silently fall back
to the process-global budget and lose deadline/cancel checks.
"""
from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable


class CtxThreadPoolExecutor(ThreadPoolExecutor):
    def submit(self, fn: Callable, /, *args: Any, **kwargs: Any):
        ctx = contextvars.copy_context()
        return super().submit(ctx.run, fn, *args, **kwargs)


def ctx_pool(max_workers: int, thread_name_prefix: str = "bf") -> CtxThreadPoolExecutor:
    return CtxThreadPoolExecutor(
        max_workers=max(1, max_workers), thread_name_prefix=thread_name_prefix
    )
