import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from comic_scroll_reader.files.bookshelf import (
    load_pages,
    natural_file_key,
    render_page_region,
    scan_bookshelf,
)


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

    def test_scan_reads_dimensions_without_decoding_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / "page.png"
            file.touch()
            image = MagicMock()
            image.__enter__.return_value = image
            image.size = (800, 1_200)
            image.getexif.return_value = {}
            image.load.side_effect = AssertionError("pixel data should stay lazy")

            with patch(
                "comic_scroll_reader.files.bookshelf.Image.open", return_value=image
            ):
                pages = scan_bookshelf(file.parent)

        self.assertEqual(
            [(page.native_width, page.native_height) for page in pages],
            [(800, 1_200)],
        )
        image.load.assert_not_called()

    def test_load_pages_from_file_list_with_natural_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            p10 = folder / "10.png"
            p2 = folder / "2.png"
            p1 = folder / "1.png"
            txt = folder / "info.txt"
            Image.new("RGB", (100, 200)).save(p10)
            Image.new("RGB", (20, 30)).save(p2)
            Image.new("RGB", (10, 15)).save(p1)
            txt.write_text("not an image", encoding="utf-8")

            pages = load_pages([p10, p2, txt, p1])

        self.assertEqual([page.file.name for page in pages], ["1.png", "2.png", "10.png"])
        self.assertEqual(
            [(page.native_width, page.native_height) for page in pages],
            [(10, 15), (20, 30), (100, 200)],
        )

    def test_render_page_region_returns_only_requested_display_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / "page.png"
            Image.new("RGB", (2_000, 3_000), "red").save(file)

            rendered = render_page_region(
                file,
                (500.0, 1_000.0, 1_500.0, 2_000.0),
                (320, 240),
            )

        self.assertIsNotNone(rendered)
        self.assertEqual(rendered.size, (320, 240))
        rendered.close()

    def test_render_page_region_with_fractional_boxes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / "pattern.png"
            # Create a 100x100 image with green on top-half and blue on bottom-half
            img = Image.new("RGB", (100, 100), (0, 255, 0))
            for y in range(50, 100):
                for x in range(100):
                    img.putpixel((x, y), (0, 0, 255))
            img.save(file)

            # Crop entirely within the blue bottom half using fractional coordinates
            rendered = render_page_region(
                file,
                (10.25, 60.5, 80.75, 90.25),
                (50, 50),
            )

        self.assertIsNotNone(rendered)
        self.assertEqual(rendered.size, (50, 50))
        # Center pixel must be pure blue (0, 0, 255)
        self.assertEqual(rendered.getpixel((25, 25)), (0, 0, 255))
        rendered.close()

    def test_render_page_region_clamps_out_of_bounds_box(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / "bounded.png"
            Image.new("RGB", (100, 100), (128, 64, 32)).save(file)

            # Box extends past image edges
            rendered = render_page_region(
                file,
                (-10.5, -5.0, 115.0, 105.5),
                (40, 40),
            )

        self.assertIsNotNone(rendered)
        self.assertEqual(rendered.size, (40, 40))
        self.assertEqual(rendered.getpixel((20, 20)), (128, 64, 32))
        rendered.close()

    def test_render_page_region_rejects_inverted_or_empty_box(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / "page.png"
            Image.new("RGB", (100, 100), "white").save(file)

            # Inverted box
            self.assertIsNone(render_page_region(file, (80.0, 80.0, 20.0, 20.0), (50, 50)))
            # Zero-width box
            self.assertIsNone(render_page_region(file, (20.0, 20.0, 20.0, 50.0), (50, 50)))
            # Zero target dimension
            self.assertIsNone(render_page_region(file, (0.0, 0.0, 50.0, 50.0), (0, 50)))

    def test_get_page_thumbnail_image_file(self) -> None:
        from comic_scroll_reader.files.bookshelf import get_page_thumbnail

        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / "thumb_test.jpg"
            Image.new("RGB", (600, 800), "blue").save(file)

            thumb = get_page_thumbnail(file, (150, 200))
            self.assertIsNotNone(thumb)
            self.assertLessEqual(thumb.width, 150)
            self.assertLessEqual(thumb.height, 200)
            thumb.close()


if __name__ == "__main__":
    unittest.main()

