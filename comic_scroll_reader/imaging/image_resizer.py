"""Resize decoded comic pages with OpenCV when it is available."""

from __future__ import annotations

from typing import Any

from PIL import Image


class ImageResizer:
    """Use OpenCV CPU resizing and fall back safely to Pillow."""

    def __init__(self, cv2_module: Any | None = None) -> None:
        self._cv2 = cv2_module

        if self._cv2 is None:
            try:
                import cv2
            except (ImportError, OSError):
                return
            self._cv2 = cv2

    def resize(
        self,
        image: Image.Image,
        size: tuple[int, int],
        cpu_filter: Image.Resampling,
    ) -> Image.Image:
        if self._cv2 is not None:
            try:
                return self._resize_opencv(image, size)
            except Exception as error:
                if self._is_opencv_error(error):
                    self._cv2 = None
                else:
                    raise

        return image.resize(size, cpu_filter)

    def _resize_opencv(
        self, image: Image.Image, size: tuple[int, int]
    ) -> Image.Image:
        import numpy as np

        source = np.asarray(image)
        result = self._cv2.resize(
            source, size, interpolation=self._cv2.INTER_CUBIC
        )
        return Image.fromarray(result)

    def _is_opencv_error(self, error: Exception) -> bool:
        error_type = getattr(self._cv2, "error", None)
        return isinstance(error, RuntimeError) or (
            isinstance(error_type, type) and isinstance(error, error_type)
        )
