"""Bounded, prioritized asynchronous image rendering pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
import heapq
import os
from pathlib import Path
import queue
import threading
from typing import Any

from PIL import Image

from ..files.bookshelf import render_page_region


@dataclass(order=True)
class RenderRequest:
    """A prioritized request to rasterize a page region."""

    priority: int  # Lower values indicate higher priority (0 = visible, 1 = preload)
    sequence: int  # Secondary sort key to preserve FIFO order for equal priorities
    generation: int = field(compare=False)
    page_file: Path = field(compare=False)
    region_key: tuple[Any, ...] = field(compare=False)
    source_box: tuple[float, float, float, float] = field(compare=False)
    target_size: tuple[int, int] = field(compare=False)
    resample: Image.Resampling = field(
        default=Image.Resampling.BICUBIC, compare=False
    )


@dataclass
class RenderResult:
    """The outcome of a completed background rasterization job."""

    generation: int
    page_file: Path
    region_key: tuple[Any, ...]
    image: Image.Image | None


class BoundedRequestQueue:
    """Thread-safe priority queue with capacity bounds and generation invalidation."""

    def __init__(self, max_pending: int = 16) -> None:
        self.max_pending = max_pending
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._heap: list[RenderRequest] = []
        self._active_keys: set[tuple[Any, ...]] = set()
        self._closed = False
        self._counter = 0

    def submit(
        self,
        *,
        priority: int,
        generation: int,
        page_file: Path,
        region_key: tuple[Any, ...],
        source_box: tuple[float, float, float, float],
        target_size: tuple[int, int],
        resample: Image.Resampling = Image.Resampling.BICUBIC,
    ) -> bool:
        """Enqueue a render job if not already queued. Evicts lowest priority if full."""
        with self._cv:
            if self._closed:
                return False
            if region_key in self._active_keys:
                return False

            self._counter += 1
            request = RenderRequest(
                priority=priority,
                sequence=self._counter,
                generation=generation,
                page_file=page_file,
                region_key=region_key,
                source_box=source_box,
                target_size=target_size,
                resample=resample,
            )

            # Evict lowest-priority request if queue exceeds capacity
            if len(self._heap) >= self.max_pending:
                # Find maximum priority value (lowest priority)
                max_idx = max(range(len(self._heap)), key=lambda i: self._heap[i].priority)
                evicted = self._heap[max_idx]
                if evicted.priority > priority:
                    self._heap.pop(max_idx)
                    heapq.heapify(self._heap)
                    self._active_keys.discard(evicted.region_key)
                else:
                    return False

            heapq.heappush(self._heap, request)
            self._active_keys.add(region_key)
            self._cv.notify()
            return True

    def get(self) -> RenderRequest | None:
        """Pop the highest priority request, blocking until available or closed."""
        with self._cv:
            while not self._heap and not self._closed:
                self._cv.wait()
            if self._closed or not self._heap:
                return None
            request = heapq.heappop(self._heap)
            self._active_keys.discard(request.region_key)
            return request

    def cancel_stale_generations(self, current_generation: int) -> None:
        """Drop unstarted requests whose generation is older than current_generation."""
        with self._cv:
            new_heap: list[RenderRequest] = []
            new_keys: set[tuple[Any, ...]] = set()
            for req in self._heap:
                if req.generation >= current_generation:
                    new_heap.append(req)
                    new_keys.add(req.region_key)
            self._heap = new_heap
            self._active_keys = new_keys
            heapq.heapify(self._heap)

    def is_empty(self) -> bool:
        """Check if there are any unstarted requests in the queue."""
        with self._cv:
            return len(self._heap) == 0

    def cancel_all(self) -> None:
        """Discard all queued, unstarted render jobs."""
        with self._cv:
            self._heap.clear()
            self._active_keys.clear()

    def close(self) -> None:
        """Wake up all waiting threads and disallow new requests."""
        with self._cv:
            self._closed = True
            self._heap.clear()
            self._active_keys.clear()
            self._cv.notify_all()


class AsyncRenderer:
    """Manages worker threads to rasterize comic page regions in the background."""

    def __init__(
        self,
        max_workers: int | None = None,
        max_pending: int = 16,
    ) -> None:
        cpu_count = os.cpu_count() or 2
        workers = (
            max_workers
            if max_workers is not None
            else max(1, min(4, cpu_count - 1))
        )
        self.max_workers = workers
        self.request_queue = BoundedRequestQueue(max_pending=max_pending)
        self.result_queue: queue.Queue[RenderResult] = queue.Queue()
        self._current_generation = 0
        self._threads: list[threading.Thread] = []
        self._busy_count = 0
        self._busy_lock = threading.Lock()
        self._closed = False
        self._start_workers()

    def _start_workers(self) -> None:
        for i in range(self.max_workers):
            thread = threading.Thread(
                target=self._worker_loop,
                name=f"CSR-RenderWorker-{i + 1}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def _worker_loop(self) -> None:
        while not self._closed:
            request = self.request_queue.get()
            if request is None:
                break
            if request.generation < self._current_generation:
                continue

            with self._busy_lock:
                self._busy_count += 1
            try:
                rendered = render_page_region(
                    request.page_file,
                    request.source_box,
                    request.target_size,
                    resample=request.resample,
                )
            except Exception:
                rendered = None
            finally:
                with self._busy_lock:
                    self._busy_count -= 1

            if self._closed or request.generation < self._current_generation:
                if rendered is not None:
                    try:
                        rendered.close()
                    except Exception:
                        pass
                continue

            self.result_queue.put(
                RenderResult(
                    generation=request.generation,
                    page_file=request.page_file,
                    region_key=request.region_key,
                    image=rendered,
                )
            )

    def submit(
        self,
        *,
        priority: int,
        generation: int,
        page_file: Path,
        region_key: tuple[Any, ...],
        source_box: tuple[float, float, float, float],
        target_size: tuple[int, int],
        resample: Image.Resampling = Image.Resampling.BICUBIC,
    ) -> bool:
        """Submit a region render job to the background queue."""
        if self._closed:
            return False
        return self.request_queue.submit(
            priority=priority,
            generation=generation,
            page_file=page_file,
            region_key=region_key,
            source_box=source_box,
            target_size=target_size,
            resample=resample,
        )

    def set_current_generation(self, generation: int) -> None:
        """Update active generation and prune stale unstarted requests."""
        self._current_generation = generation
        self.request_queue.cancel_stale_generations(generation)

    def cancel_all(self) -> None:
        """Cancel all pending, unstarted work."""
        self.request_queue.cancel_all()

    @property
    def has_pending_work(self) -> bool:
        """Return True if any requests are queued, actively rendering, or uncollected."""
        with self._busy_lock:
            busy = self._busy_count > 0
        return busy or not self.request_queue.is_empty() or not self.result_queue.empty()

    def get_results(self) -> list[RenderResult]:
        """Fetch all available completed render results without blocking."""
        results: list[RenderResult] = []
        while True:
            try:
                results.append(self.result_queue.get_nowait())
            except queue.Empty:
                break
        return results

    def shutdown(self) -> None:
        """Signal all workers to exit and clear remaining work."""
        if self._closed:
            return
        self._closed = True
        self.request_queue.close()
        # Drain any leftover results and close images
        while True:
            try:
                result = self.result_queue.get_nowait()
                if result.image is not None:
                    try:
                        result.image.close()
                    except Exception:
                        pass
            except queue.Empty:
                break
