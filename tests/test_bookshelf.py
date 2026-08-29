import tempfile
import unittest
from pathlib import Path

from PIL import Image

from comic_scroll_reader.bookshelf import natural_file_key, scan_bookshelf


class BookshelfTests(unittest.TestCase):
    def test_natural_key_places_page_2_before_page_10(self) -> None:
        files = [Path("page10.png"), Path("page2.png"), Path("page1.png")]

        self.assertEqual(
            [file.name for file in sorted(files, key=natural_file_key)],
            ["page1.png", "page2.png", "page10.png"],
        )

    def test_scan_ignores_unsupported_and_broken_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            Image.new("RGB", (30, 40)).save(folder / "2.png")
            Image.new("RGB", (10, 20)).save(folder / "1.png")
            (folder / "3.png").write_text("not really an image", encoding="utf-8")
            (folder / "notes.txt").write_text("hello", encoding="utf-8")

            pages = scan_bookshelf(folder)

        self.assertEqual([page.file.name for page in pages], ["1.png", "2.png"])
        self.assertEqual(
            [(page.native_width, page.native_height) for page in pages],
            [(10, 20), (30, 40)],
        )


if __name__ == "__main__":
    unittest.main()
