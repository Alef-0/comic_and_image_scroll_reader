import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import pypdfium2 as pdfium

from comic_scroll_reader.files.bookshelf import open_page
from comic_scroll_reader.files.pdf_reader import PdfBookshelf


class PdfReaderTests(unittest.TestCase):
    def _create_sample_pdf(self, path: Path, page_sizes: list[tuple[float, float]]) -> None:
        doc = pdfium.PdfDocument.new()
        for width, height in page_sizes:
            doc.new_page(width, height)
        with open(path, "wb") as file:
            doc.save(file)
        doc.close()

    def test_pdf_bookshelf_inspects_pages_and_cleans_up_on_close(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pdf_file = Path(temporary) / "sample.pdf"
            self._create_sample_pdf(pdf_file, [(100, 200), (300, 400), (150, 250)])

            bookshelf = PdfBookshelf(pdf_file, scale=1.5)
            cache_dir = bookshelf.cache_dir

            self.assertTrue(cache_dir.is_dir())
            self.assertEqual(bookshelf.page_count, 3)
            self.assertEqual(len(bookshelf.pages), 3)

            # Dimensions scaled by 1.5
            self.assertEqual(bookshelf.pages[0].native_width, 150)
            self.assertEqual(bookshelf.pages[0].native_height, 300)
            self.assertEqual(bookshelf.pages[1].native_width, 450)
            self.assertEqual(bookshelf.pages[1].native_height, 600)

            # Page identities exist without materializing full-page JPEG files.
            self.assertFalse(bookshelf.pages[0].file.is_file())

            # Decode page 1
            image0 = open_page(bookshelf.pages[0].file)
            self.assertIsNotNone(image0)
            self.assertEqual(image0.size, (150, 300))

            # Decode page 3 via on-demand rendering
            image2 = open_page(bookshelf.pages[2].file)
            self.assertIsNotNone(image2)
            self.assertEqual(image2.size, (225, 375))

            # Close bookshelf and ensure temporary directory is deleted
            bookshelf.close()
            self.assertFalse(cache_dir.exists())

    def test_pdf_bookshelf_renders_strictly_on_demand(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pdf_file = Path(temporary) / "multi_page.pdf"
            self._create_sample_pdf(
                pdf_file, [(100, 100), (100, 100), (100, 100), (100, 100), (100, 100)]
            )

            bookshelf = PdfBookshelf(pdf_file, scale=1.0)
            try:
                self.assertEqual(bookshelf.page_count, 5)
                # Opening the document does not render any complete page.
                for i in range(5):
                    self.assertFalse(bookshelf.pages[i].file.is_file())

                # Request page 3 on demand
                img3 = open_page(bookshelf.pages[3].file)
                self.assertIsNotNone(img3)
                self.assertTrue(bookshelf.pages[3].file.is_file())

                # Other unrequested pages remain unrendered
                self.assertFalse(bookshelf.pages[1].file.is_file())
                self.assertFalse(bookshelf.pages[2].file.is_file())
                self.assertFalse(bookshelf.pages[4].file.is_file())
            finally:
                bookshelf.close()

    def test_pdf_bookshelf_renders_region_without_materializing_page_jpeg(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pdf_file = Path(temporary) / "region.pdf"
            self._create_sample_pdf(pdf_file, [(200, 300)])
            bookshelf = PdfBookshelf(pdf_file, scale=1.5)
            try:
                from comic_scroll_reader.files.bookshelf import render_page_region

                rendered = render_page_region(
                    bookshelf.pages[0].file,
                    (75.0, 150.0, 225.0, 300.0),
                    (320, 320),
                )
                self.assertIsNotNone(rendered)
                self.assertEqual(rendered.size, (320, 320))
                rendered.close()
                self.assertFalse(bookshelf.pages[0].file.is_file())
            finally:
                bookshelf.close()

    def test_each_pdf_region_uses_a_short_lived_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pdf_file = Path(temporary) / "region-lifetime.pdf"
            self._create_sample_pdf(pdf_file, [(200, 300)])
            bookshelf = PdfBookshelf(pdf_file, scale=1.5)
            try:
                from comic_scroll_reader.files.bookshelf import render_page_region

                with patch(
                    "comic_scroll_reader.files.pdf_reader.pdfium.PdfDocument",
                    wraps=pdfium.PdfDocument,
                ) as open_document:
                    for source_box in (
                        (0.0, 0.0, 150.0, 150.0),
                        (150.0, 150.0, 300.0, 300.0),
                    ):
                        rendered = render_page_region(
                            bookshelf.pages[0].file, source_box, (256, 256)
                        )
                        self.assertIsNotNone(rendered)
                        rendered.close()

                self.assertEqual(open_document.call_count, 2)
                self.assertFalse(bookshelf.pages[0].file.is_file())
            finally:
                bookshelf.close()

    def test_large_pdf_opens_instantly(self) -> None:
        pdf_path = Path("/media/alef/Everything/DepositoDownloads/Just Porn/Risky-Patrol.pdf")
        if not pdf_path.is_file():
            self.skipTest("Large test PDF not accessible in current environment")

        t0 = time.perf_counter()
        bookshelf = PdfBookshelf(pdf_path, scale=2.0)
        t1 = time.perf_counter()

        try:
            self.assertLess(t1 - t0, 1.5, "PDF opening took longer than 1.5 seconds")
            self.assertEqual(bookshelf.page_count, 32)
            self.assertEqual(len(bookshelf.pages), 32)
            self.assertFalse(bookshelf.pages[0].file.is_file())

            image0 = open_page(bookshelf.pages[0].file)
            self.assertIsNotNone(image0)
        finally:
            cache_dir = bookshelf.cache_dir
            bookshelf.close()
            self.assertFalse(cache_dir.exists())


if __name__ == "__main__":
    unittest.main()
