"""IPC channels, process context, and deadlock prevention helpers."""

from __future__ import annotations

import multiprocessing as mp
from multiprocessing.context import BaseContext
import queue
import time
from typing import Any


def get_mp_context() -> BaseContext:
    """Always enforce 'spawn' context to avoid POSIX fork deadlocks with GUI and active threads."""
    return mp.get_context("spawn")


def drain_queue(target_queue: Any, timeout: float = 0.5) -> list[Any]:
    """Safely drain an IPC queue to prevent pipe buffer deadlock prior to process joining."""
    drained: list[Any] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            drained.append(target_queue.get(timeout=min(0.05, remaining)))
        except (queue.Empty, TimeoutError, OSError, EOFError, ValueError):
            break
    return drained


def safe_close_queue(target_queue: Any) -> None:
    """Close an IPC queue and release feeder thread resources safely."""
    try:
        target_queue.close()
        target_queue.cancel_join_thread()
    except (AttributeError, OSError, ValueError):
        pass


def safe_terminate_process(process: mp.Process, timeout: float = 2.0) -> None:
    """Join a process with a bounded timeout, terminating if unresponsive."""
    if not process.is_alive():
        try:
            process.close()
        except (ValueError, OSError):
            pass
        return

    process.join(timeout=timeout)
    if process.is_alive():
        try:
            process.terminate()
            process.join(timeout=1.0)
        except (OSError, ValueError):
            pass

    if process.is_alive():
        try:
            process.kill()
            process.join(timeout=0.5)
        except (OSError, ValueError):
            pass

    try:
        process.close()
    except (ValueError, OSError):
        pass
