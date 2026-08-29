"""Resize decoded comic pages with OpenCV CUDA when it is available."""

from __future__ import annotations

from typing import Any

from PIL import Image


class ImageResizer:
    """Select a CUDA resize backend once and fall back safely to Pillow."""

    def __init__(self, cv2_module: Any | None = None) -> None:
        self._cv2 = cv2_module
        self._gpu_mat_type: Any | None = None
        self._cuda_enabled = False

        if self._cv2 is None:
            try:
                import cv2
            except (ImportError, OSError):
                return
            self._cv2 = cv2

        try:
            cuda = self._cv2.cuda
            self._gpu_mat_type = getattr(self._cv2, "cuda_GpuMat", None)
            if self._gpu_mat_type is None:
                self._gpu_mat_type = getattr(cuda, "GpuMat", None)
            self._cuda_enabled = bool(
                self._gpu_mat_type is not None
                and hasattr(cuda, "resize")
                and cuda.getCudaEnabledDeviceCount() > 0
            )
        except Exception as error:
            if not self._is_opencv_error(error) and not isinstance(
                error, AttributeError
            ):
                raise
            self._cuda_enabled = False

    @property
    def backend_name(self) -> str:
        return "CUDA" if self._cuda_enabled else "CPU"

    def resize(
        self,
        image: Image.Image,
        size: tuple[int, int],
        cpu_filter: Image.Resampling,
    ) -> Image.Image:
        if self._cuda_enabled:
            try:
                return self._resize_cuda(image, size)
            except Exception as error:
                # OpenCV reports build, driver, and CUDA runtime failures with
                # different exception types across releases. Disable the
                # backend after any such failure so rendering can continue.
                if self._is_opencv_error(error):
                    self._cuda_enabled = False
                else:
                    raise

        working = image
        if size[0] < image.width and size[1] < image.height:
            reduction = min(image.width // size[0], image.height // size[1])
            if reduction >= 2:
                working = image.reduce(reduction)
        return working.resize(size, cpu_filter)

    def _resize_cuda(
        self, image: Image.Image, size: tuple[int, int]
    ) -> Image.Image:
        import numpy as np

        source = np.asarray(image)
        gpu_source = self._gpu_mat_type()
        gpu_source.upload(source)
        interpolation = (
            self._cv2.INTER_LINEAR
            if size[0] > image.width or size[1] > image.height
            else self._cv2.INTER_CUBIC
        )
        gpu_result = self._cv2.cuda.resize(
            gpu_source, size, interpolation=interpolation
        )
        return Image.fromarray(gpu_result.download(), mode="RGB")

    def _is_opencv_error(self, error: Exception) -> bool:
        error_type = getattr(self._cv2, "error", None)
        return isinstance(error, RuntimeError) or (
            isinstance(error_type, type) and isinstance(error, error_type)
        )
