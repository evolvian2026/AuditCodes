"""Minimal background task registry for work that outlives a request (compiling, running tests)."""

from __future__ import annotations

import secrets
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Task:
    id: str
    kind: str
    status: str = "queued"  # queued | running | done | failed
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    @property
    def finished(self) -> bool:
        return self.status in ("done", "failed")


class TaskRegistry:
    def __init__(self, workers: int = 2) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="auditcodes")
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, fn: Callable[[], Any]) -> Task:
        task = Task(id=secrets.token_hex(8), kind=kind)
        with self._lock:
            self._tasks[task.id] = task

        def run() -> None:
            task.status = "running"
            try:
                task.result = fn()
                task.status = "done"
            except Exception:
                task.error = traceback.format_exc()
                task.status = "failed"
            finally:
                task.finished_at = time.time()

        self._pool.submit(run)
        return task

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def prune(self, max_age_seconds: float = 3600) -> None:
        cutoff = time.time() - max_age_seconds
        with self._lock:
            for tid in [t for t, task in self._tasks.items() if task.finished and (task.finished_at or 0) < cutoff]:
                del self._tasks[tid]
