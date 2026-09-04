"""Bounded, prioritized asynchronous image rendering pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
import heapq
import io
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
    webp_bytes: bytes | None = None


class BoundedRequestQueue:
    """Thread-safe priority queue with capacity bounds and generation invalidation."""

    def __init__(self, max_pending: int = 16) -> None:
        self.max_pending = max_pending
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._heap: list[RenderRequest] = []
        self._active_keys: set[tuple[int, tuple[Any, ...]]] = set()
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
            request_id = (generation, region_key)
            if request_id in self._active_keys:
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
                    self._active_keys.discard(
                        (evicted.generation, evicted.region_key)
                    )
                else:
                    return False

            heapq.heappush(self._heap, request)
            self._active_keys.add(request_id)
            self._cv.notify()
            return True

    def get(self) -> RenderRequest | None:
        """Pop the highest priority request, blocking until available or closed."""
        with self._cv:
            while not self._heap and not self._closed:
                self._cv.wait()
            if self._closed or not self._heap:
                return None
            return heapq.heappop(self._heap)

    def task_done(self, request: RenderRequest) -> None:
        """Release a request identity after its worker fully publishes a result."""
        with self._cv:
            self._active_keys.discard((request.generation, request.region_key))

    def cancel_stale_generations(self, current_generation: int) -> None:
        """Drop unstarted requests whose generation is older than current_generation."""
        with self._cv:
            new_heap: list[RenderRequest] = []
            for request in self._heap:
                if request.generation >= current_generation:
                    new_heap.append(request)
                else:
                    self._active_keys.discard(
                        (request.generation, request.region_key)
                    )
            self._heap = new_heap
            heapq.heapify(self._heap)

    def is_empty(self) -> bool:
        """Check if there are any unstarted requests in the queue."""
        with self._cv:
            return len(self._heap) == 0

    def has_work(self) -> bool:
        """Return whether a request is queued or owned by a worker."""
        with self._cv:
            return bool(self._active_keys)

    def cancel_all(self) -> None:
        """Discard all queued, unstarted render jobs."""
        with self._cv:
            for request in self._heap:
                self._active_keys.discard(
                    (request.generation, request.region_key)
                )
            self._heap.clear()

    def close(self) -> None:
        """Wake up all waiting threads and disallow new requests."""
        with self._cv:
            self._closed = True
            for request in self._heap:
                self._active_keys.discard(
                    (request.generation, request.region_key)
                )
            self._heap.clear()
            self._cv.notify_all()


class AsyncRenderer:
    """Manages background workers (processes or threads) to rasterize comic page regions."""

    def __init__(
        self,
        max_workers: int | None = None,
        max_pending: int = 16,
        backend: str = "process",
    ) -> None:
        cpu_count = os.cpu_count() or 2
        workers = (
            max_workers
            if max_workers is not None
            else max(1, min(4, cpu_count - 1))
        )
        self.max_workers = workers
        self.backend = backend
        self.request_queue = BoundedRequestQueue(max_pending=max_pending)
        self.result_queue: queue.Queue[RenderResult] = queue.Queue()
        self._current_generation = 0
        self._closed = False

        if self.backend == "process":
            from ..communications.channel import (
                get_mp_context,
            )
            from ..communications.worker import render_worker_main

            self._ctx = get_mp_context()
            self._task_queue = self._ctx.Queue(maxsize=max_pending * 2)
            self._worker_result_queue = self._ctx.Queue()
            self._stop_event = self._ctx.Event()
            self._processes: list[Any] = []
            self._in_flight: dict[tuple[int, tuple[Any, ...]], RenderRequest] = {}
            self._in_flight_lock = threading.Lock()

            for i in range(self.max_workers):
                proc = self._ctx.Process(
                    target=render_worker_main,
                    args=(self._task_queue, self._worker_result_queue, self._stop_event),
                    name=f"CSR-RenderProc-{i + 1}",
                    daemon=True,
                )
                proc.start()
                self._processes.append(proc)

            self._dispatcher_thread = threading.Thread(
                target=self._process_dispatch_loop,
                name="CSR-ProcessDispatcher",
                daemon=True,
            )
            self._dispatcher_thread.start()

            self._collector_thread = threading.Thread(
                target=self._process_collector_loop,
                name="CSR-ProcessCollector",
                daemon=True,
            )
            self._collector_thread.start()
        else:
            self._threads: list[threading.Thread] = []
            self._start_thread_workers()

    def _start_thread_workers(self) -> None:
        for i in range(self.max_workers):
            thread = threading.Thread(
                target=self._thread_worker_loop,
                name=f"CSR-RenderWorker-{i + 1}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def _thread_worker_loop(self) -> None:
        while not self._closed:
            request = self.request_queue.get()
            if request is None:
                break
            if request.generation < self._current_generation:
                self.request_queue.task_done(request)
                continue

            rendered: Image.Image | None = None
            webp_bytes: bytes | None = None
            try:
                try:
                    rendered = render_page_region(
                        request.page_file,
                        request.source_box,
                        request.target_size,
                        resample=request.resample,
                    )
                except Exception:
                    rendered = None

                if rendered is not None:
                    try:
                        buffer = io.BytesIO()
                        rendered.save(buffer, format="WEBP", quality=85, method=0)
                        webp_bytes = buffer.getvalue()
                    except Exception:
                        webp_bytes = None

                if self._closed or request.generation < self._current_generation:
                    if rendered is not None:
                        rendered.close()
                    continue

                self.result_queue.put(
                    RenderResult(
                        generation=request.generation,
                        page_file=request.page_file,
                        region_key=request.region_key,
                        image=rendered,
                        webp_bytes=webp_bytes,
                    )
                )
            finally:
                self.request_queue.task_done(request)

    def _process_dispatch_loop(self) -> None:
        from ..communications.messages import RenderTask

        while not self._closed:
            request = self.request_queue.get()
            if request is None:
                break
            if request.generation < self._current_generation:
                self.request_queue.task_done(request)
                continue

            resample_code = (
                request.resample.value
                if hasattr(request.resample, "value")
                else int(request.resample)
            )
            task = RenderTask(
                priority=request.priority,
                sequence=request.sequence,
                generation=request.generation,
                page_file=request.page_file,
                region_key=request.region_key,
                source_box=request.source_box,
                target_size=request.target_size,
                resample_code=resample_code,
            )
            key = (request.generation, request.region_key)
            with self._in_flight_lock:
                self._in_flight[key] = request

            try:
                self._task_queue.put(task)
            except Exception:
                with self._in_flight_lock:
                    self._in_flight.pop(key, None)
                self.request_queue.task_done(request)
                break

    def _process_collector_loop(self) -> None:
        from ..communications.messages import RenderResponse

        while not self._closed:
            try:
                response = self._worker_result_queue.get(timeout=0.1)
            except (queue.Empty, TimeoutError):
                continue
            except (OSError, EOFError, ValueError):
                break

            if not isinstance(response, RenderResponse):
                continue

            key = (response.generation, response.region_key)
            with self._in_flight_lock:
                orig_request = self._in_flight.pop(key, None)

            image: Image.Image | None = None
            if response.webp_bytes is not None:
                try:
                    image = Image.open(io.BytesIO(response.webp_bytes))
                    image.load()
                except Exception:
                    image = None
            elif response.raw_bytes is not None and response.raw_mode is not None:
                try:
                    image = Image.frombytes(
                        response.raw_mode, response.target_size, response.raw_bytes
                    )
                    image.load()
                except Exception:
                    image = None

            if self._closed or response.generation < self._current_generation:
                if image is not None:
                    try:
                        image.close()
                    except Exception:
                        pass
                if orig_request is not None:
                    self.request_queue.task_done(orig_request)
                continue

            self.result_queue.put(
                RenderResult(
                    generation=response.generation,
                    page_file=response.page_file,
                    region_key=response.region_key,
                    image=image,
                    webp_bytes=response.webp_bytes,
                )
            )
            if orig_request is not None:
                self.request_queue.task_done(orig_request)

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
        if self.backend == "process" and hasattr(self, "_in_flight_lock"):
            with self._in_flight_lock:
                stale_keys = [k for k in self._in_flight if k[0] < generation]
                for k in stale_keys:
                    req = self._in_flight.pop(k, None)
                    if req is not None:
                        self.request_queue.task_done(req)

    def cancel_all(self) -> None:
        """Cancel all pending, unstarted work."""
        self.request_queue.cancel_all()

    @property
    def has_pending_work(self) -> bool:
        """Return True if any requests are queued, actively rendering, or uncollected."""
        return self.request_queue.has_work() or not self.result_queue.empty()

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

        if self.backend == "process":
            from ..communications.channel import (
                drain_queue,
                safe_close_queue,
                safe_terminate_process,
            )
            from ..communications.messages import ShutdownSentinel

            if hasattr(self, "_stop_event"):
                self._stop_event.set()

            if hasattr(self, "_task_queue") and hasattr(self, "_processes"):
                for _ in self._processes:
                    try:
                        self._task_queue.put_nowait(ShutdownSentinel())
                    except Exception:
                        pass

            if hasattr(self, "_worker_result_queue"):
                drain_queue(self._worker_result_queue, timeout=0.2)

            if hasattr(self, "_processes"):
                for proc in self._processes:
                    safe_terminate_process(proc, timeout=1.0)
                self._processes.clear()

            if hasattr(self, "_dispatcher_thread"):
                self._dispatcher_thread.join(timeout=1.0)
            if hasattr(self, "_collector_thread"):
                self._collector_thread.join(timeout=1.0)

            if hasattr(self, "_task_queue"):
                safe_close_queue(self._task_queue)
            if hasattr(self, "_worker_result_queue"):
                safe_close_queue(self._worker_result_queue)

            if hasattr(self, "_in_flight_lock"):
                with self._in_flight_lock:
                    for req in self._in_flight.values():
                        try:
                            self.request_queue.task_done(req)
                        except Exception:
                            pass
                    self._in_flight.clear()

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
