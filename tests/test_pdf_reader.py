import os
from pathlib import Path
import tempfile
import time
import unittest

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

            # First page is rendered immediately
            self.assertTrue(bookshelf.pages[0].file.is_file())

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
            self.assertTrue(bookshelf.pages[0].file.is_file())

            image0 = open_page(bookshelf.pages[0].file)
            self.assertIsNotNone(image0)
        finally:
            cache_dir = bookshelf.cache_dir
            bookshelf.close()
            self.assertFalse(cache_dir.exists())


if __name__ == "__main__":
    unittest.main()
