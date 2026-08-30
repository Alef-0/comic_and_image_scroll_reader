import unittest
from pathlib import Path

from comic_scroll_reader.core.layout import (
    arrange_pages,
    clamp_scroll,
    detect_double_spread_indices,
    neighboring_page_indices,
    pages_nearest_to,
    visible_page_range,
)
from comic_scroll_reader.core.models import ComicPage


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

    def test_dual_page_keeps_first_solo_and_pairs_the_rest(self) -> None:
        pages = self.pages + [ComicPage(Path("four.png"), 100, 300)]

        positions = arrange_pages(
            pages, page_width=100, viewport_width=400, dual_page=True
        )

        self.assertEqual(
            [(item.x, item.y, item.width, item.height) for item in positions],
            [
                (150, 0, 100, 200),
                (100, 200, 100, 50),
                (200, 200, 100, 100),
                (150, 300, 100, 300),
            ],
        )
        self.assertEqual(visible_page_range(positions, 260, 40), (1, 3))

    def test_manga_reading_reverses_each_pair_but_not_page_order(self) -> None:
        positions = arrange_pages(
            self.pages,
            page_width=100,
            viewport_width=400,
            dual_page=True,
            manga_reading=True,
        )

        self.assertEqual([item.x for item in positions], [150, 200, 100])

    def test_page_gap_is_only_inserted_between_pages(self) -> None:
        positions = arrange_pages(
            self.pages,
            page_width=100,
            viewport_width=400,
            dual_page=True,
            page_gap=12,
        )

        self.assertEqual(
            [(item.x, item.y, item.width, item.height) for item in positions],
            [(150, 0, 100, 200), (94, 212, 100, 50), (206, 212, 100, 100)],
        )

    def test_single_page_gaps_have_no_outer_spacing(self) -> None:
        positions = arrange_pages(
            self.pages, page_width=100, viewport_width=400, page_gap=12
        )

        self.assertEqual(
            [(item.x, item.y, item.width, item.height) for item in positions],
            [(150, 0, 100, 200), (150, 212, 100, 50), (150, 274, 100, 100)],
        )

    def test_detects_pages_that_are_ratio_outliers(self) -> None:
        pages = [
            ComicPage(Path("one.png"), 600, 900),
            ComicPage(Path("two.png"), 620, 900),
            ComicPage(Path("spread.png"), 1_200, 900),
            ComicPage(Path("three.png"), 590, 900),
        ]

        self.assertEqual(detect_double_spread_indices(pages), {2})

    def test_detected_spread_occupies_a_solo_dual_page_row(self) -> None:
        pages = self.pages + [
            ComicPage(Path("four.png"), 100, 100),
            ComicPage(Path("five.png"), 100, 100),
        ]

        positions = arrange_pages(
            pages,
            page_width=100,
            viewport_width=400,
            dual_page=True,
            solo_page_indices={2},
        )

        self.assertEqual(
            [position.y for position in positions], [0, 200, 250, 350, 350]
        )

    def test_arrangement_accepts_native_width_for_each_page(self) -> None:
        positions = arrange_pages(
            self.pages, page_width=[100, 200, 75], viewport_width=500
        )

        self.assertEqual(
            [(item.width, item.height) for item in positions],
            [(100, 200), (200, 100), (75, 75)],
        )

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
