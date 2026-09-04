"""Resize decoded comic pages with Pillow."""

from __future__ import annotations

from PIL import Image


class ImageResizer:
    """Resize images using Pillow SIMD-accelerated filters."""

    def __init__(self, default_filter: Image.Resampling = Image.Resampling.BICUBIC) -> None:
        self.default_filter = default_filter

    def resize(
        self,
        image: Image.Image,
        size: tuple[int, int],
        cpu_filter: Image.Resampling | None = None,
    ) -> Image.Image:
        """Resize a Pillow image to the target size using the specified filter."""
        resample = cpu_filter if cpu_filter is not None else self.default_filter
        return image.resize(size, resample)
