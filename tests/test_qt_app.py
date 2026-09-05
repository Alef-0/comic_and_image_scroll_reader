from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import pypdfium2 as pdfium


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    from comic_scroll_reader.config import DEFAULT_CONFIG
    from comic_scroll_reader.core.models import ComicPage
    from comic_scroll_reader.qt_app import (
        DecodeTask,
        QtComicView,
        QtReaderWindow,
        read_arguments,
    )

    QT_AVAILABLE = True
except ModuleNotFoundError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, "PySide6 is an optional dependency")
class QtReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.config = DEFAULT_CONFIG.copy()
        self.config["remember_folder"] = False

    def _view(self, *, dual_page: bool = False, manga: bool = False) -> QtComicView:
        self.config["dual_page"] = dual_page
        self.config["manga_reading"] = manga
        view = QtComicView(self.config)
        view.resize(1000, 650)
        view.show()
        self.application.processEvents()
        view._schedule_decode_refresh = lambda: None
        view._schedule_visible_update = lambda _value=None: None
        return view

    @staticmethod
    def _pages() -> list[ComicPage]:
        return [
            ComicPage(Path(f"/missing/page-{index}.jpg"), 1000, 1500)
            for index in range(4)
        ]

    def test_startup_layout_uses_the_shown_viewport(self) -> None:
        view = self._view(dual_page=True)
        try:
            view.set_pages(self._pages(), Path("/missing"), is_folder=True)

            self.assertLessEqual(view.strip_width * 2, view.viewport().width())
            self.assertEqual(view.positions[1].y, view.positions[2].y)
            self.assertNotEqual(view.positions[1].x, view.positions[2].x)
        finally:
            view.shutdown()
            view.close()

    def test_manga_layout_swaps_paired_page_positions(self) -> None:
        view = self._view(dual_page=True, manga=True)
        try:
            view.set_pages(self._pages(), Path("/missing"), is_folder=True)

            self.assertGreater(view.positions[1].x, view.positions[2].x)
        finally:
            view.shutdown()
            view.close()

    def test_page_counter_reports_the_last_visible_paired_row(self) -> None:
        view = self._view(dual_page=True)
        try:
            view.set_pages(self._pages(), Path("/missing"), is_folder=True)
            view._visible_range = lambda: (1, 3)

            self.assertEqual(view.current_page_number, 3)
            self.assertEqual(view.page_counter_text, "2-3 / 4")
        finally:
            view.shutdown()
            view.close()

    def test_display_decode_is_bounded_for_oversized_pages(self) -> None:
        view = self._view()
        try:
            page = ComicPage(Path("/missing/large.jpg"), 12000, 24000)
            view.set_pages([page], Path("/missing"), is_folder=True)
            view.stop_at_fit_width = False
            view.strip_width = 10000
            view._layout_pages(preserve_position=False)

            size = view._decode_size(0)
            self.assertLessEqual(
                size.width() * size.height(),
                view.MAX_PIXMAP_PIXELS,
            )
        finally:
            view.shutdown()
            view.close()

    def test_zoom_preserves_the_page_relative_reading_anchor(self) -> None:
        view = self._view()
        try:
            pages = [
                ComicPage(Path(f"/missing/page-{index}.jpg"), 1000, 1200 + index * 80)
                for index in range(8)
            ]
            view.set_pages(pages, Path("/missing"), is_folder=True)
            view.stop_at_fit_width = False
            view.verticalScrollBar().setValue(view.positions[4].y + 200)
            before = view._capture_layout_anchor()

            view.zoom(1)
            after = view._capture_layout_anchor()

            self.assertIsNotNone(before)
            self.assertIsNotNone(after)
            self.assertEqual(after[0], before[0])
            self.assertAlmostEqual(after[1], before[1], delta=0.02)
        finally:
            view.shutdown()
            view.close()

    def test_visible_image_decodes_into_a_pixmap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "page.webp"
            Image.new("RGB", (640, 960), "navy").save(image_path)
            page = ComicPage(image_path, 640, 960)
            view = QtComicView(self.config)
            view.resize(800, 600)
            view.show()
            self.application.processEvents()
            try:
                view.set_pages([page], Path(temporary), is_folder=True)
                view._decode_timer.stop()
                view._update_visible_pages()
                view._thread_pool.waitForDone()
                self.application.processEvents()

                self.assertFalse(view._pixmap_items[0].pixmap().isNull())
                self.assertIn(0, view._decoded_sizes)
            finally:
                view.shutdown()
                view.close()

    def test_progressive_base_build_retains_every_page_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pages: list[ComicPage] = []
            for index in range(4):
                image_path = Path(temporary) / f"page-{index}.webp"
                Image.new("RGB", (640, 960), (index * 30, 20, 80)).save(image_path)
                pages.append(ComicPage(image_path, 640, 960))
            view = QtComicView(self.config)
            view.resize(800, 600)
            view.show()
            self.application.processEvents()
            try:
                view.set_pages(pages, Path(temporary), is_folder=True)
                view._decode_timer.stop()
                view._queue_base_decodes()
                view._thread_pool.waitForDone()
                self.application.processEvents()

                self.assertEqual(set(view._base_pixmaps), set(range(len(pages))))
                self.assertLessEqual(
                    sum(view._base_pixmap_bytes.values()),
                    view.BASE_PIXMAP_CACHE_BYTES,
                )
                view._decoded_sizes[0] = QSize(640, 960)
                view._pixmap_bytes[0] = 640 * 960 * 4
                view._discard_pixmap(0)
                self.assertFalse(view._pixmap_items[0].pixmap().isNull())
            finally:
                view.shutdown()
                view.close()

    def test_pillow_fallback_keeps_non_qt_image_formats_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "page.png"
            Image.new("RGB", (320, 480), "purple").save(image_path)
            page = ComicPage(image_path, 320, 480)
            result: list[QImage] = []
            task = DecodeTask(1, 0, "detail", page, QSize(160, 240))
            task.signals.finished.connect(
                lambda _revision, _index, _tier, image, _error: result.append(image)
            )

            with patch("comic_scroll_reader.qt_app.QImageReader") as reader_type:
                reader = reader_type.return_value
                reader.size.return_value = QSize(320, 480)
                reader.read.return_value = QImage()
                reader.errorString.return_value = "unsupported"
                task.run()

            self.assertEqual(len(result), 1)
            self.assertFalse(result[0].isNull())
            self.assertEqual(result[0].size(), QSize(160, 240))

    def test_qt_arguments_keep_reader_aliases(self) -> None:
        arguments = read_arguments(
            [
                "--not-maximized",
                "--dual-page",
                "--manga-reading",
                "--expand-all",
                "pages",
            ]
        )

        self.assertFalse(arguments.start_maximized)
        self.assertTrue(arguments.dual_page)
        self.assertTrue(arguments.manga_reading)
        self.assertTrue(arguments.expand_all)
        self.assertEqual(arguments.paths, [Path("pages")])

    def test_toolbar_preserves_established_option_names(self) -> None:
        window = QtReaderWindow(self.config)
        try:
            labels = {action.text() for action in window._option_actions.values()}

            self.assertEqual(
                labels,
                {
                    "Don't enlarge images",
                    "Stop at fit width",
                    "Dual page",
                    "Manga order",
                    "Page spacing",
                    "Detect double-page spreads",
                    "Remember folder",
                },
            )
        finally:
            window._save_automatic_state = lambda: None
            window._save_progress = lambda: None
            window.close()
            self.application.processEvents()

    def test_pdf_opens_and_decodes_on_demand(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pdf_path = Path(temporary) / "sample.pdf"
            document = pdfium.PdfDocument.new()
            document.new_page(400, 600)
            with pdf_path.open("wb") as output:
                document.save(output)
            document.close()

            window = QtReaderWindow(self.config)
            window.resize(800, 600)
            window.show()
            self.application.processEvents()
            try:
                self.assertTrue(window.open_paths([pdf_path]))
                window.view._decode_timer.stop()
                window.view._update_visible_pages()
                window.view._thread_pool.waitForDone()
                self.application.processEvents()

                self.assertIsNotNone(window._active_pdf)
                self.assertFalse(window.view._pixmap_items[0].pixmap().isNull())
                self.assertIn("sample.pdf (1 page)", window.windowTitle())
            finally:
                window._save_automatic_state = lambda: None
                window._save_progress = lambda: None
                window.close()
                self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
