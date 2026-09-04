"""Isolated background worker process routine for rendering comic pages and regions."""

from __future__ import annotations

import io
import queue
import time
from typing import Any

from PIL import Image

from ..files.bookshelf import render_page_region
from .messages import RenderResponse, RenderTask, ShutdownSentinel
from .pdf_bridge import close_cached_documents


def render_worker_main(
    task_queue: Any,
    result_queue: Any,
    stop_event: Any,
) -> None:
    """Main event loop for an isolated rendering worker process."""
    try:
        while not stop_event.is_set():
            try:
                task = task_queue.get(timeout=0.2)
            except (queue.Empty, TimeoutError):
                continue
            except (OSError, EOFError, ValueError):
                break

            if task is None or isinstance(task, ShutdownSentinel):
                break

            if not isinstance(task, RenderTask):
                continue

            rendered: Image.Image | None = None
            webp_bytes: bytes | None = None
            raw_bytes: bytes | None = None
            raw_mode: str | None = None
            error_msg: str | None = None

            try:
                try:
                    resample_filter = Image.Resampling(task.resample_code)
                except (ValueError, TypeError):
                    resample_filter = Image.Resampling.BICUBIC

                rendered = render_page_region(
                    task.page_file,
                    task.source_box,
                    task.target_size,
                    resample=resample_filter,
                )

                if rendered is not None:
                    try:
                        buffer = io.BytesIO()
                        rendered.save(buffer, format="WEBP", quality=85, method=0)
                        webp_bytes = buffer.getvalue()
                    except Exception:
                        webp_bytes = None
                        try:
                            raw_bytes = rendered.tobytes()
                            raw_mode = rendered.mode
                        except Exception:
                            raw_bytes = None
            except Exception as exc:
                error_msg = str(exc)
            finally:
                if rendered is not None:
                    try:
                        rendered.close()
                    except Exception:
                        pass

            response = RenderResponse(
                generation=task.generation,
                page_file=task.page_file,
                region_key=task.region_key,
                webp_bytes=webp_bytes,
                raw_bytes=raw_bytes,
                raw_mode=raw_mode,
                target_size=task.target_size,
                error=error_msg,
            )

            try:
                result_queue.put(response)
            except (OSError, EOFError, ValueError):
                break
    finally:
        close_cached_documents()
