"""Thread-safe PDF inspection and on-demand full-page or cropped rendering."""

import atexit
from collections.abc import Callable
import os
from pathlib import Path
import tempfile
import threading
from typing import ClassVar

import pypdfium2 as pdfium
from PIL import Image

from ..core.memory import trim_memory
from ..core.models import ComicPage


_PAGE_PROVIDERS: dict[Path, Callable[[], None]] = {}
_REGION_PROVIDERS: dict[
    Path,
    Callable[[tuple[float, float, float, float], tuple[int, int]], Image.Image | None],
] = {}
_THUMBNAIL_PROVIDERS: dict[
    Path,
    Callable[[tuple[int, int]], Image.Image | None],
] = {}
_REGISTRY_LOCK = threading.Lock()
# PDFium owns process-wide mutable state and cannot be entered concurrently,
# even when each caller opens a separate document.
_PDFIUM_LOCK = threading.Lock()


def register_page_provider(
    file: Path,
    provider: Callable[[], None],
    region_provider: Callable[
        [tuple[float, float, float, float], tuple[int, int]], Image.Image | None
    ],
    thumbnail_provider: Callable[[tuple[int, int]], Image.Image | None] | None = None,
) -> None:
    """Register full-page, cropped, and thumbnail renderers for one virtual PDF page."""
    with _REGISTRY_LOCK:
        key = file.resolve()
        _PAGE_PROVIDERS[key] = provider
        _REGION_PROVIDERS[key] = region_provider
        if thumbnail_provider is not None:
            _THUMBNAIL_PROVIDERS[key] = thumbnail_provider


def unregister_page_provider(file: Path) -> None:
    """Remove a page generator from the registry."""
    with _REGISTRY_LOCK:
        key = file.resolve()
        _PAGE_PROVIDERS.pop(key, None)
        _REGION_PROVIDERS.pop(key, None)
        _THUMBNAIL_PROVIDERS.pop(key, None)


def ensure_page_available(file: Path) -> None:
    """Execute the registered generator if a page image does not exist yet."""
    if file.is_file():
        return
    provider = None
    with _REGISTRY_LOCK:
        provider = _PAGE_PROVIDERS.get(file.resolve())
    if provider is not None:
        provider()


def render_registered_page_region(
    file: Path,
    source_box: tuple[float, float, float, float],
    target_size: tuple[int, int],
) -> Image.Image | None:
    """Render a cropped virtual PDF page, or return None when it is not registered."""
    with _REGISTRY_LOCK:
        provider = _REGION_PROVIDERS.get(file.resolve())
    if provider is None:
        return None
    return provider(source_box, target_size)


def render_registered_page_thumbnail(
    file: Path,
    max_size: tuple[int, int] = (200, 260),
) -> Image.Image | None:
    """Render a fast low-resolution thumbnail for a virtual PDF page, or return None."""
    with _REGISTRY_LOCK:
        provider = _THUMBNAIL_PROVIDERS.get(file.resolve())
    if provider is None:
        return None
    return provider(max_size)


class PdfBookshelf:
    """Inspect PDF pages and render full pages or display regions on demand."""

    DEFAULT_SCALE: ClassVar[float] = 1.5
    JPEG_QUALITY: ClassVar[int] = 85

    def __init__(self, pdf_path: Path, scale: float = DEFAULT_SCALE) -> None:
        self.pdf_path = pdf_path.expanduser().resolve()
        self.scale = scale
        self._temp_dir = tempfile.TemporaryDirectory(prefix="csr_pdf_")
        self.cache_dir = Path(self._temp_dir.name)
        self._lock = threading.Lock()
        self._closed = False

        self.pages: list[ComicPage] = []
        self._page_files: list[Path] = []
        with _PDFIUM_LOCK:
            inspection_document = pdfium.PdfDocument(self.pdf_path)
            try:
                self.page_count = len(inspection_document)
                self._init_pages(inspection_document)
            finally:
                inspection_document.close()

        atexit.register(self.close)

    def _init_pages(self, document: pdfium.PdfDocument) -> None:
        for i in range(self.page_count):
            w_pt, h_pt = document.get_page_size(i)
            width = max(1, round(w_pt * self.scale))
            height = max(1, round(h_pt * self.scale))
            page_file = self.cache_dir / f"page_{i + 1:04d}.jpg"
            self._page_files.append(page_file)
            self.pages.append(ComicPage(page_file, width, height))
            register_page_provider(
                page_file,
                lambda idx=i: self._render_page_on_demand(idx),
                lambda source_box, target_size, idx=i: self._render_page_region(
                    idx, source_box, target_size
                ),
                lambda max_size, idx=i: self._render_page_thumbnail(idx, max_size),
            )

    def _render_page_on_demand(self, index: int) -> None:
        if self._closed or index < 0 or index >= self.page_count:
            return
        target_path = self._page_files[index]
        if target_path.is_file():
            return
        with self._lock:
            if self._closed or target_path.is_file():
                return
            temp_path = self.cache_dir / f".tmp_page_{index + 1:04d}.jpg"
            try:
                with _PDFIUM_LOCK:
                    document = pdfium.PdfDocument(self.pdf_path)
                    page = document[index]
                    try:
                        bitmap = page.render(scale=self.scale, limit_image_cache=True)
                        try:
                            image = bitmap.to_pil()
                            try:
                                image.save(
                                    temp_path, "JPEG", quality=self.JPEG_QUALITY
                                )
                                os.replace(temp_path, target_path)
                            finally:
                                image.close()
                        finally:
                            bitmap.close()
                    finally:
                        page.close()
                        document.close()
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise
        trim_memory()

    def _render_page_thumbnail(
        self, index: int, max_size: tuple[int, int] = (200, 260)
    ) -> Image.Image | None:
        """Render a fast low-resolution thumbnail directly from PDFium."""
        if self._closed or index < 0 or index >= self.page_count:
            return None
        page_item = self.pages[index]
        native_w = page_item.native_width
        native_h = page_item.native_height
        if native_w < 1 or native_h < 1:
            return None
        s = min(max_size[0] / native_w, max_size[1] / native_h)
        render_scale = max(0.01, s * self.scale)
        # Thumbnail lookup runs on Tk's thread. Never make the UI wait behind a
        # large region render; the page zone remains until a later crisp result.
        if not _PDFIUM_LOCK.acquire(blocking=False):
            return None
        try:
            if self._closed:
                return None
            document = pdfium.PdfDocument(self.pdf_path)
            page = document[index]
            try:
                bitmap = page.render(
                    scale=render_scale,
                    limit_image_cache=True,
                    rev_byteorder=True,
                )
                try:
                    shared = bitmap.to_pil()
                    try:
                        thumb = shared.convert("RGB")
                        thumb.load()
                        return thumb
                    finally:
                        shared.close()
                finally:
                    bitmap.close()
            finally:
                page.close()
                document.close()
        except pdfium.PdfiumError:
            # A preview is optional; a malformed page must not escape through a
            # Tk callback and start an exception loop while scrolling.
            return None
        finally:
            _PDFIUM_LOCK.release()

    def _render_page_region(
        self,
        index: int,
        source_box: tuple[float, float, float, float],
        target_size: tuple[int, int],
    ) -> Image.Image | None:
        """Rasterize only the requested display region directly from PDFium."""
        if self._closed or index < 0 or index >= self.page_count:
            return None
        native_width = self.pages[index].native_width
        native_height = self.pages[index].native_height
        left, top, right, bottom = source_box
        left = min(max(left, 0.0), float(native_width))
        right = min(max(right, left), float(native_width))
        top = min(max(top, 0.0), float(native_height))
        bottom = min(max(bottom, top), float(native_height))
        target_width, target_height = target_size
        if right <= left or bottom <= top or target_width < 1 or target_height < 1:
            return None

        render_scale = self.scale * target_width / (right - left)
        crop = (
            left / self.scale,
            (native_height - bottom) / self.scale,
            (native_width - right) / self.scale,
            top / self.scale,
        )
        with _PDFIUM_LOCK:
            if self._closed:
                return None
            document = pdfium.PdfDocument(self.pdf_path)
            page = document[index]
            try:
                bitmap = page.render(
                    scale=render_scale,
                    crop=crop,
                    limit_image_cache=True,
                    rev_byteorder=True,
                )
                try:
                    shared = bitmap.to_pil()
                    try:
                        rendered = shared.convert("RGB")
                    finally:
                        shared.close()
                finally:
                    bitmap.close()
            finally:
                page.close()
                document.close()

        if rendered.size != target_size:
            corrected = rendered.resize(target_size, Image.Resampling.BICUBIC)
            rendered.close()
            rendered = corrected
        rendered.load()
        return rendered

    def close(self) -> None:
        """Close PDF document and clean up temporary directory."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        atexit.unregister(self.close)
        for page_file in self._page_files:
            unregister_page_provider(page_file)
        try:
            self._temp_dir.cleanup()
        except Exception:
            pass
        trim_memory()

    def __enter__(self) -> "PdfBookshelf":
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()
