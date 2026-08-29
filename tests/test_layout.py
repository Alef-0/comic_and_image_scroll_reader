import unittest
from pathlib import Path

from comic_scroll_reader.layout import (
    arrange_pages,
    clamp_scroll,
    neighboring_page_indices,
    pages_nearest_to,
    visible_page_range,
)
from comic_scroll_reader.models import ComicPage


class LayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pages = [
            ComicPage(Path("one.png"), 100, 200),
            ComicPage(Path("two.png"), 200, 100),
            ComicPage(Path("three.png"), 100, 100),
        ]

    def test_arranges_scaled_pages_without_gaps(self) -> None:
        positions = arrange_pages(self.pages, page_width=200, viewport_width=500)

        self.assertEqual(
            [(item.x, item.y, item.width, item.height) for item in positions],
            [(150, 0, 200, 400), (150, 400, 200, 100), (150, 500, 200, 200)],
        )

    def test_visible_range_is_half_open(self) -> None:
        positions = arrange_pages(self.pages, page_width=200, viewport_width=500)

        self.assertEqual(visible_page_range(positions, 400, 100), (1, 2))
        self.assertEqual(visible_page_range(positions, 450, 100), (1, 3))

    def test_scroll_is_clamped_to_the_strip(self) -> None:
        self.assertEqual(clamp_scroll(-20, 1_000, 300), 0)
        self.assertEqual(clamp_scroll(900, 1_000, 300), 700)

    def test_prioritizes_the_page_containing_the_zoom_anchor(self) -> None:
        positions = arrange_pages(self.pages, page_width=200, viewport_width=500)

        self.assertEqual(pages_nearest_to(positions, [0, 1, 2], 450), [1, 2, 0])

    def test_selects_two_neighbor_pages_in_each_direction(self) -> None:
        self.assertEqual(neighboring_page_indices(3, 5, 8, 2), [2, 5, 1, 6])


if __name__ == "__main__":
    unittest.main()
