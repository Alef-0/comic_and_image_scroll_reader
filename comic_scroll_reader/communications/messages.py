"""Serializable message definitions for multiprocessing communications."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class RenderTask:
    """A prioritized task dispatched to a worker process."""

    priority: int
    sequence: int
    generation: int
    page_file: Path
    region_key: tuple[Any, ...]
    source_box: tuple[float, float, float, float]
    target_size: tuple[int, int]
    resample_code: int = 3  # Image.Resampling.BICUBIC


@dataclass(frozen=True, slots=True)
class RenderResponse:
    """The outcome of a completed background rasterization job returned from a worker."""

    generation: int
    page_file: Path
    region_key: tuple[Any, ...]
    webp_bytes: bytes | None = None
    raw_bytes: bytes | None = None
    raw_mode: str | None = None
    target_size: tuple[int, int] = (0, 0)
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ShutdownSentinel:
    """Poison pill token indicating that the worker process should shut down."""

    pass
