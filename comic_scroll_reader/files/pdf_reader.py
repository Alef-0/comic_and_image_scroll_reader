"""Fast, thread-safe PDF page inspection, background caching, and on-demand rendering."""

import atexit
from collections.abc import Callable
import os
from pathlib import Path
import tempfile
import threading
from typing import ClassVar

import pypdfium2 as pdfium

from ..core.models import ComicPage


_PAGE_PROVIDERS: dict[Path, Callable[[], None]] = {}
_REGISTRY_LOCK = threading.Lock()


def register_page_provider(file: Path, provider: Callable[[], None]) -> None:
    """Register an on-demand generator for a page image that has not been written yet."""
    with _REGISTRY_LOCK:
        _PAGE_PROVIDERS[file.resolve()] = provider


def unregister_page_provider(file: Path) -> None:
    """Remove a page generator from the registry."""
    with _REGISTRY_LOCK:
        _PAGE_PROVIDERS.pop(file.resolve(), None)


def ensure_page_available(file: Path) -> None:
    """Execute the registered generator if a page image does not exist yet."""
    if file.is_file():
        return
    provider = None
    with _REGISTRY_LOCK:
        provider = _PAGE_PROVIDERS.get(file.resolve())
    if provider is not None:
        provider()


class PdfBookshelf:
    """Inspect and extract pages from a PDF file with background and on-demand rendering."""

    DEFAULT_SCALE: ClassVar[float] = 2.0
    JPEG_QUALITY: ClassVar[int] = 85

    def __init__(self, pdf_path: Path, scale: float = DEFAULT_SCALE) -> None:
        self.pdf_path = pdf_path.expanduser().resolve()
        self.scale = scale
        self._doc = pdfium.PdfDocument(self.pdf_path)
        self.page_count = len(self._doc)
        self._temp_dir = tempfile.TemporaryDirectory(prefix="csr_pdf_")
        self.cache_dir = Path(self._temp_dir.name)
        self._lock = threading.Lock()
        self._closed = False
        self._worker_thread: threading.Thread | None = None

        self.pages: list[ComicPage] = []
        self._page_files: list[Path] = []
        self._init_pages()

        atexit.register(self.close)

    def _init_pages(self) -> None:
        for i in range(self.page_count):
            with self._lock:
                page = self._doc[i]
                w_pt, h_pt = page.get_size()
            width = max(1, round(w_pt * self.scale))
            height = max(1, round(h_pt * self.scale))
            page_file = self.cache_dir / f"page_{i + 1:04d}.jpg"
            self._page_files.append(page_file)
            self.pages.append(ComicPage(page_file, width, height))
            register_page_provider(page_file, lambda idx=i: self._render_page_on_demand(idx))

        # Render page 1 synchronously so the reader window opens with it immediately
        if self.page_count > 0:
            self._render_page_on_demand(0)

        # Start background daemon thread to progressively render remaining pages
        if self.page_count > 1:
            self._worker_thread = threading.Thread(
                target=self._background_extract,
                name=f"PdfExtract-{self.pdf_path.name}",
                daemon=True,
            )
            self._worker_thread.start()

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
                page = self._doc[index]
                image = page.render(scale=self.scale).to_pil()
                image.save(temp_path, "JPEG", quality=self.JPEG_QUALITY)
                os.replace(temp_path, target_path)
            except Exception:
                if temp_path.is_file():
                    temp_path.unlink(missing_ok=True)
                raise

    def _background_extract(self) -> None:
        for i in range(1, self.page_count):
            if self._closed:
                break
            target_path = self._page_files[i]
            if not target_path.is_file():
                try:
                    self._render_page_on_demand(i)
                except Exception:
                    if self._closed:
                        break

    def close(self) -> None:
        """Stop background worker, close PDF document, and clean up temporary directory."""
        if self._closed:
            return
        self._closed = True
        for page_file in self._page_files:
            unregister_page_provider(page_file)
        if self._worker_thread is not None and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=0.5)
        with self._lock:
            try:
                self._doc.close()
            except Exception:
                pass
        try:
            self._temp_dir.cleanup()
        except Exception:
            pass

    def __enter__(self) -> "PdfBookshelf":
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()
